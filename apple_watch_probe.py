#!/usr/bin/env python3
"""Probe Apple Watch BLE availability and (optionally) try HR notify."""
import argparse
import asyncio
from datetime import datetime
from typing import Dict, Tuple

from bleak import BleakClient, BleakScanner

HR_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEAS = "00002a37-0000-1000-8000-00805f9b34fb"
APPLE_MFG_ID = 0x004C


def parse_hr_measurement(data: bytearray):
    if not data or len(data) < 2:
        return None
    flags = data[0]
    hr_16bit = flags & 0x01
    if hr_16bit:
        if len(data) < 3:
            return None
        return int.from_bytes(data[1:3], byteorder="little")
    return data[1]


def _name_match(device, adv, needle: str) -> bool:
    if not needle:
        return False
    needle = needle.lower()
    name = (device.name or "").lower()
    local = (getattr(adv, "local_name", None) or "").lower()
    return needle in name or needle in local


def is_apple_watch(device, adv, name_hint: str) -> bool:
    name = (device.name or "").lower()
    local = (getattr(adv, "local_name", None) or "").lower()
    if name_hint and _name_match(device, adv, name_hint):
        return True
    if "watch" in name or "watch" in local:
        return True
    mfg = adv.manufacturer_data or {}
    if APPLE_MFG_ID in mfg:
        return True
    return False


async def scan_devices(timeout: float):
    seen: Dict[str, Tuple[object, object]] = {}

    def detection_callback(device, advertisement_data):
        seen[device.address] = (device, advertisement_data)

    scanner = BleakScanner(detection_callback)
    await scanner.start()
    await asyncio.sleep(timeout)
    await scanner.stop()
    return seen


def describe(device, adv) -> str:
    name = device.name or adv.local_name or "(unknown)"
    local = getattr(adv, "local_name", None)
    uuids = adv.service_uuids or []
    uuid_str = ",".join(uuids) if uuids else "-"
    rssi = getattr(device, "rssi", None)
    rssi_str = f" rssi:{rssi}dBm" if rssi is not None else ""
    mfg = adv.manufacturer_data or {}
    mfg_keys = ",".join([f"{k:04x}:{len(v)}" for k, v in mfg.items()]) if mfg else "-"
    local_str = f" local_name:{local}" if local and local != device.name else ""
    return f"{name} {device.address} uuids:{uuid_str} mfg:{mfg_keys}{rssi_str}{local_str}"


async def connect_and_probe(address: str, label: str, notify: bool, notify_time: float, timeout: float):
    print(f"Connecting to {label} ({address}) ...", flush=True)
    try:
        async with BleakClient(address, timeout=timeout) as client:
            if not client.is_connected:
                print("  Failed to connect.", flush=True)
                return
            print("  Connected.", flush=True)
            if hasattr(client, "get_services"):
                services = await client.get_services()
            else:
                services = client.services
            service_uuids = [s.uuid.lower() for s in (services or [])]
            has_hr = HR_SERVICE in service_uuids
            print(f"  Heart Rate service present: {has_hr}", flush=True)
            if not has_hr:
                return
            if notify:
                print("  Listening for HR notify...", flush=True)

                def handle_notify(_, data: bytearray):
                    hr = parse_hr_measurement(data)
                    if hr is None:
                        return
                    ts = datetime.now().strftime("%H:%M:%S")
                    print(f"  [{ts}] HR={hr} bpm", flush=True)

                await client.start_notify(HR_MEAS, handle_notify)
                await asyncio.sleep(notify_time)
                await client.stop_notify(HR_MEAS)
    except Exception as exc:
        print(f"  Connect error: {exc}", flush=True)


async def main():
    ap = argparse.ArgumentParser(description="Probe Apple Watch BLE/HR availability.")
    ap.add_argument("--scan-time", type=float, default=12.0, help="Scan time seconds")
    ap.add_argument("--name", default="Apple Watch", help="Name hint for Apple Watch")
    ap.add_argument("--address", help="Exact BLE address to connect")
    ap.add_argument("--all", action="store_true", help="Print all devices found")
    ap.add_argument("--connect", action="store_true", help="Try connecting to candidate")
    ap.add_argument("--connect-all", action="store_true", help="Try connecting to all candidates")
    ap.add_argument("--max-connect", type=int, default=3, help="Max candidates to connect")
    ap.add_argument("--connect-timeout", type=float, default=8.0, help="Connect timeout seconds")
    ap.add_argument("--notify", action="store_true", help="Try HR notify if service exists")
    ap.add_argument("--notify-time", type=float, default=10.0, help="Seconds to wait for HR notify")
    args = ap.parse_args()

    print(f"Scanning {args.scan_time:.1f}s...", flush=True)
    devices = await scan_devices(args.scan_time)
    if not devices:
        print("No BLE devices found.", flush=True)
        return

    candidates = []
    for _, (device, adv) in devices.items():
        if args.all or is_apple_watch(device, adv, args.name):
            candidates.append((device, adv))

    if not candidates:
        print("No Apple Watch candidates found.", flush=True)
    else:
        print("Candidates:")
        for device, adv in candidates:
            print("  " + describe(device, adv))

    if not args.connect and not args.connect_all:
        return

    if args.address:
        await connect_and_probe(
            args.address,
            args.address,
            args.notify,
            args.notify_time,
            args.connect_timeout,
        )
        return

    if not candidates:
        print("No target to connect.", flush=True)
        return

    # Prefer stronger RSSI if available.
    candidates.sort(key=lambda x: getattr(x[0], "rssi", -999), reverse=True)
    if not args.connect_all:
        device, _ = candidates[0]
        await connect_and_probe(
            device.address,
            device.name or device.address,
            args.notify,
            args.notify_time,
            args.connect_timeout,
        )
        return

    for idx, (device, _) in enumerate(candidates[: args.max_connect]):
        label = device.name or device.address
        await connect_and_probe(
            device.address,
            label,
            args.notify,
            args.notify_time,
            args.connect_timeout,
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
