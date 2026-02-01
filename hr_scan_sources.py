#!/usr/bin/env python3
"""Scan BLE devices and record those exposing Heart Rate service."""
import argparse
import asyncio
import json
import time
from typing import Dict, Tuple

from bleak import BleakClient, BleakScanner

HR_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"


def _name_for(device, adv) -> str:
    name = device.name or ""
    local = getattr(adv, "local_name", None) or ""
    return name or local or device.address


async def scan_devices(timeout: float):
    seen: Dict[str, Tuple[object, object]] = {}

    def detection_callback(device, advertisement_data):
        seen[device.address] = (device, advertisement_data)

    scanner = BleakScanner(detection_callback)
    await scanner.start()
    await asyncio.sleep(timeout)
    await scanner.stop()
    return seen


async def has_hr_service(address: str, timeout: float) -> bool:
    try:
        async with BleakClient(address, timeout=timeout) as client:
            if not client.is_connected:
                return False
            if hasattr(client, "get_services"):
                services = await client.get_services()
            else:
                services = client.services
            return any(s.uuid.lower() == HR_SERVICE for s in (services or []))
    except Exception:
        return False


async def main():
    ap = argparse.ArgumentParser(description="Scan BLE HR sources and save to file.")
    ap.add_argument("--out", required=True, help="Output JSON file")
    ap.add_argument("--scan-time", type=float, default=12.0)
    ap.add_argument("--max-connect", type=int, default=5)
    ap.add_argument("--connect-timeout", type=float, default=8.0)
    args = ap.parse_args()

    devices = await scan_devices(args.scan_time)
    results = {}

    for _, (device, adv) in devices.items():
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if HR_SERVICE in uuids:
            results[device.address] = {
                "name": _name_for(device, adv),
                "address": device.address,
                "ts": time.time(),
                "via": "adv",
            }

    # Try connecting to strongest RSSI devices to verify HR service.
    candidates = []
    for _, (device, adv) in devices.items():
        if device.address in results:
            continue
        rssi = getattr(device, "rssi", None)
        candidates.append((device, adv, rssi if rssi is not None else -999))

    candidates.sort(key=lambda x: x[2], reverse=True)
    for device, adv, _ in candidates[: args.max_connect]:
        ok = await has_hr_service(device.address, args.connect_timeout)
        if ok:
            results[device.address] = {
                "name": _name_for(device, adv),
                "address": device.address,
                "ts": time.time(),
                "via": "connect",
            }

    data = list(results.values())
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(data)} sources to {args.out}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
