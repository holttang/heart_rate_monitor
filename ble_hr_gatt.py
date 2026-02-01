#!/usr/bin/env python3
"""Connect to BLE Heart Rate service and print measurements (GATT)."""
import argparse
import asyncio
import sys
from datetime import datetime
from typing import Dict, Optional, Tuple

from bleak import BleakClient, BleakScanner

HEART_RATE_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"


def parse_hr_measurement(data: bytearray):
    if not data or len(data) < 2:
        return None
    flags = data[0]
    hr_16bit = flags & 0x01
    if hr_16bit:
        if len(data) < 3:
            return None
        hr = int.from_bytes(data[1:3], byteorder="little")
        return hr
    return data[1]


async def scan_devices(timeout: float) -> Dict[str, Tuple[object, object]]:
    seen: Dict[str, Tuple[object, object]] = {}

    def detection_callback(device, advertisement_data):
        seen[device.address] = (device, advertisement_data)

    scanner = BleakScanner(detection_callback)
    await scanner.start()
    await asyncio.sleep(timeout)
    await scanner.stop()
    return seen


def match_name(device, advertisement_data, name_substr: Optional[str]) -> bool:
    if not name_substr:
        return True
    want = name_substr.lower()
    dev_name = (device.name or "").lower()
    local_name = (getattr(advertisement_data, "local_name", None) or "").lower()
    return want in dev_name or want in local_name


def match_service(advertisement_data, service_uuid: Optional[str]) -> bool:
    if not service_uuid:
        return True
    uuids = [u.lower() for u in (advertisement_data.service_uuids or [])]
    return service_uuid.lower() in uuids


async def find_device(name_substr: Optional[str], address: Optional[str], timeout: float, require_service: bool):
    if address:
        return address

    devices = await scan_devices(timeout)
    if not devices:
        return None

    candidates = []
    for _, (device, adv) in devices.items():
        if not match_name(device, adv, name_substr):
            continue
        if require_service and not match_service(adv, HEART_RATE_SERVICE):
            continue
        candidates.append((device, adv))

    if not candidates:
        return None

    # Prefer stronger RSSI if available.
    candidates.sort(key=lambda x: getattr(x[0], "rssi", -999), reverse=True)
    return candidates[0][0].address


async def main():
    ap = argparse.ArgumentParser(description="Connect to BLE Heart Rate service and print measurements.")
    ap.add_argument("--name", help="Substring match on device name")
    ap.add_argument("--address", help="Exact BLE address match")
    ap.add_argument("--scan-time", type=float, default=8.0, help="Scan time (seconds) when using --name")
    ap.add_argument("--require-hr-service", action="store_true", help="Only match devices advertising Heart Rate service")
    ap.add_argument("--scan-only", action="store_true", help="Scan and list devices, do not connect")
    args = ap.parse_args()

    if args.scan_only:
        devices = await scan_devices(args.scan_time)
        for _, (device, adv) in devices.items():
            uuids = adv.service_uuids or []
            uuid_str = ",".join(uuids) if uuids else "-"
            rssi = getattr(device, "rssi", None)
            rssi_str = f" rssi:{rssi}dBm" if rssi is not None else ""
            local_name = getattr(adv, "local_name", None)
            name = device.name or local_name or "(unknown)"
            local_str = f" local_name:{local_name}" if local_name and local_name != device.name else ""
            print(f"{name} {device.address} uuids:{uuid_str}{rssi_str}{local_str}", flush=True)
        return

    target = await find_device(
        args.name,
        args.address,
        args.scan_time,
        require_service=args.require_hr_service,
    )
    if not target:
        print("Device not found. Provide --address or --name and try again.", file=sys.stderr)
        sys.exit(2)

    async with BleakClient(target) as client:
        if not client.is_connected:
            print("Failed to connect.", file=sys.stderr)
            sys.exit(3)

        # Ensure service is present
        services = await client.get_services()
        if HEART_RATE_SERVICE not in [s.uuid.lower() for s in services]:
            print("Heart Rate service not found on device.", file=sys.stderr)
            sys.exit(4)

        print(f"Connected to {target}. Listening for heart rate...", flush=True)

        def handle_notify(_, data: bytearray):
            hr = parse_hr_measurement(data)
            if hr is None:
                return
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] HR={hr} bpm", flush=True)

        await client.start_notify(HEART_RATE_MEASUREMENT, handle_notify)
        try:
            while True:
                await asyncio.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            await client.stop_notify(HEART_RATE_MEASUREMENT)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
