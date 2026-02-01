#!/usr/bin/env python3
"""Scan BLE advertisements and print heart rate if found in broadcast data."""
import argparse
import asyncio
import sys
from datetime import datetime

from typing import Optional

from bleak import BleakScanner


def parse_hr_from_payload(payload: bytes):
    if not payload or len(payload) < 2:
        return None
    flags = payload[0]
    hr_16bit = flags & 0x01
    if hr_16bit:
        if len(payload) < 3:
            return None
        hr = int.from_bytes(payload[1:3], byteorder="little")
        return hr
    else:
        hr = payload[1]
        return hr


def match_device(
    name: Optional[str],
    local_name: Optional[str],
    address: str,
    want_name: Optional[str],
    want_addr: Optional[str],
):
    if want_addr and address.lower() != want_addr.lower():
        return False
    if want_name:
        name_l = (name or "").lower()
        local_l = (local_name or "").lower()
        if want_name.lower() not in name_l and want_name.lower() not in local_l:
            return False
    return True


async def main():
    ap = argparse.ArgumentParser(description="Scan BLE advertisements and print HR if present.")
    ap.add_argument("--name", help="Substring match on device name")
    ap.add_argument("--address", help="Exact BLE address match")
    ap.add_argument("--timeout", type=float, default=0.0, help="Stop after N seconds (0=forever)")
    ap.add_argument("--raw", action="store_true", help="Print raw service/manufacturer data")
    ap.add_argument("--dump", action="store_true", help="Print full advertisement object")
    args = ap.parse_args()

    print("Scanning for BLE advertisements...", flush=True)

    def detection_callback(device, advertisement_data):
        if not match_device(
            device.name,
            getattr(advertisement_data, "local_name", None),
            device.address,
            args.name,
            args.address,
        ):
            return

        # Try common locations where HR might be embedded in advertisements.
        hr = None

        # Service Data might contain Heart Rate Measurement (0x2A37) or service UUID (0x180D)
        for uuid, data in (advertisement_data.service_data or {}).items():
            candidate = parse_hr_from_payload(bytes(data))
            if candidate is not None:
                hr = candidate
                break

        # Some vendors embed HR in manufacturer data. Try to parse as HR Measurement if length fits.
        if hr is None:
            for _, data in (advertisement_data.manufacturer_data or {}).items():
                candidate = parse_hr_from_payload(bytes(data))
                if candidate is not None:
                    hr = candidate
                    break

        if args.dump:
            ts = datetime.now().strftime("%H:%M:%S")
            name = device.name or advertisement_data.local_name or "(unknown)"
            print(f"[{ts}] {name} {device.address} adv={advertisement_data!r}", flush=True)
            return

        if args.raw:
            svc_items = []
            for uuid, data in (advertisement_data.service_data or {}).items():
                svc_items.append(f"{uuid}={bytes(data).hex()}")
            mfg_items = []
            for mfg_id, data in (advertisement_data.manufacturer_data or {}).items():
                mfg_items.append(f"{mfg_id:04x}={bytes(data).hex()}")
            uuids = advertisement_data.service_uuids or []
            uuid_str = ",".join(uuids) if uuids else "-"
            ts = datetime.now().strftime("%H:%M:%S")
            name = device.name or advertisement_data.local_name or "(unknown)"
            rssi = getattr(device, "rssi", None)
            rssi_str = f" rssi:{rssi}dBm" if rssi is not None else ""
            svc_str = " ".join(svc_items) if svc_items else "-"
            mfg_str = " ".join(mfg_items) if mfg_items else "-"
            print(
                f"[{ts}] {name} {device.address} svc:{svc_str} mfg:{mfg_str} uuids:{uuid_str}{rssi_str}",
                flush=True,
            )
            return

        if hr is not None:
            ts = datetime.now().strftime("%H:%M:%S")
            name = device.name or advertisement_data.local_name or "(unknown)"
            print(f"[{ts}] {name} {device.address} HR={hr} bpm", flush=True)

    scanner = BleakScanner(detection_callback)
    await scanner.start()

    if args.timeout and args.timeout > 0:
        await asyncio.sleep(args.timeout)
        await scanner.stop()
    else:
        try:
            while True:
                await asyncio.sleep(3600)
        except KeyboardInterrupt:
            await scanner.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
