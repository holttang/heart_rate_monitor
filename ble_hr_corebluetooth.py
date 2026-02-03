#!/usr/bin/env python3
"""Connect to Heart Rate service via CoreBluetooth (macOS) and print measurements."""
import argparse
import json
import os
import sys
import time
from typing import Optional
from datetime import datetime

import objc
from Foundation import NSDate, NSRunLoop, NSObject
from CoreBluetooth import (
    CBCentralManager,
    CBManagerStatePoweredOn,
    CBUUID,
)
from libdispatch import DISPATCH_QUEUE_SERIAL, dispatch_queue_create

HR_SERVICE = CBUUID.UUIDWithString_("180D")
HR_MEAS = CBUUID.UUIDWithString_("2A37")


def parse_hr_measurement(data: bytes):
    if not data or len(data) < 2:
        return None
    flags = data[0]
    hr_16bit = flags & 0x01
    if hr_16bit:
        if len(data) < 3:
            return None
        return int.from_bytes(data[1:3], byteorder="little")
    return data[1]


class HRDelegate(NSObject):
    def init(self):
        self = objc.super(HRDelegate, self).init()
        if self is None:
            return None
        self.central = None
        self.peripherals = {}
        self.connected_ids = set()
        self.connecting_ids = set()
        self.name_by_id = {}
        self.connected = False
        self.scanning = False
        self.last_scan_start = None
        self.pending_reconnect = {}
        self.reconnect_interval = 5.0
        self.blocked_ids = {}
        self.blocked_ttl = 60.0
        self.outfile = None
        self.outfh = None
        self.name_filters = []
        self.id_filters = []
        self.preferred_names = []
        self.preferred_grace = 6.0
        self.max_devices = None
        self.scan_all = False
        return self

    @objc.python_method
    def set_output(self, path: Optional[str]):
        self.outfile = path
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)

    @objc.python_method
    def set_filters(self, names, ids, max_devices: Optional[int], scan_all: bool):
        self.name_filters = [n.lower() for n in (names or []) if n]
        self.id_filters = [i.lower() for i in (ids or []) if i]
        self.max_devices = max_devices
        self.scan_all = scan_all

    @objc.python_method
    def set_preferred(self, names, grace: float):
        self.preferred_names = [n.lower() for n in (names or []) if n]
        self.preferred_grace = grace

    @objc.python_method
    def _write_hr(self, bpm: int, source: str, device_id: str):
        if not self.outfile:
            return
        if self.outfh is None:
            self.outfh = open(self.outfile, "a", encoding="utf-8", buffering=1)
        payload = {"ts": time.time(), "bpm": bpm, "source": source, "id": device_id}
        self.outfh.write(json.dumps(payload, ensure_ascii=True) + "\n")

    @objc.python_method
    def _start_scan(self):
        if not self.central or self.scanning:
            return
        self.scanning = True
        services = None if self.scan_all else [HR_SERVICE]
        self.central.scanForPeripheralsWithServices_options_(services, None)
        self.last_scan_start = time.time()

    @objc.python_method
    def _stop_scan(self):
        if not self.central or not self.scanning:
            return
        self.central.stopScan()
        self.scanning = False
        self.last_scan_start = None

    @objc.python_method
    def _schedule_reconnect(self, device_id: str, reason: str):
        if device_id in self.pending_reconnect:
            return
        delay = self.reconnect_interval
        self.pending_reconnect[device_id] = time.time() + delay
        label = self.name_by_id.get(device_id, device_id)
        print(f"{reason} Reconnecting in {delay:.1f}s... ({label})", file=sys.stderr, flush=True)

    @objc.python_method
    def _reset_reconnect(self, device_id: str):
        if device_id in self.pending_reconnect:
            del self.pending_reconnect[device_id]

    @objc.python_method
    def tick(self):
        if not self.pending_reconnect:
            return
        now = time.time()
        if not self.central or self.central.state() != CBManagerStatePoweredOn:
            for device_id in list(self.pending_reconnect.keys()):
                self.pending_reconnect[device_id] = now + self.reconnect_interval
                label = self.name_by_id.get(device_id, device_id)
                print(f"Bluetooth not ready. Reconnecting in {self.reconnect_interval:.1f}s... ({label})",
                      file=sys.stderr, flush=True)
            return

        for device_id, ts in list(self.pending_reconnect.items()):
            if now < ts:
                continue
            if device_id in self.connected_ids:
                del self.pending_reconnect[device_id]
                continue
            if device_id in self.connecting_ids:
                self.pending_reconnect[device_id] = now + self.reconnect_interval
                continue
            peripheral = self.peripherals.get(device_id)
            if peripheral is not None:
                try:
                    peripheral.setDelegate_(self)
                except Exception:
                    pass
                self.connecting_ids.add(device_id)
                label = self.name_by_id.get(device_id, device_id)
                print(f"Reconnect attempt: connect {label}", file=sys.stderr, flush=True)
                self.central.connectPeripheral_options_(peripheral, None)
                self.pending_reconnect[device_id] = now + self.reconnect_interval
                continue
            if self.scanning:
                self._stop_scan()
            print("Reconnect attempt: scan", file=sys.stderr, flush=True)
            self._start_scan()
            self.pending_reconnect[device_id] = now + self.reconnect_interval

    @objc.python_method
    def _match_device(self, peripheral, advertisementData) -> bool:
        device_id = peripheral.identifier().UUIDString()
        if device_id in self.blocked_ids:
            until = self.blocked_ids.get(device_id, 0.0)
            if time.time() < until:
                return False
            del self.blocked_ids[device_id]
        if self.id_filters:
            ok = False
            for want in self.id_filters:
                if want in device_id.lower():
                    ok = True
                    break
            if not ok:
                return False
        if self.name_filters:
            name = (peripheral.name() or "").lower()
            adv_name = ""
            if advertisementData is not None:
                try:
                    adv_name = (advertisementData.get("kCBAdvDataLocalName") or "").lower()
                except Exception:
                    adv_name = ""
            ok = False
            for want in self.name_filters:
                if want in name or want in adv_name:
                    ok = True
                    break
            if not ok:
                return False
        return True

    @objc.python_method
    def _is_preferred(self, peripheral, advertisementData) -> bool:
        if not self.preferred_names:
            return False
        name = (peripheral.name() or "").lower()
        adv_name = ""
        if advertisementData is not None:
            try:
                adv_name = (advertisementData.get("kCBAdvDataLocalName") or "").lower()
            except Exception:
                adv_name = ""
        for want in self.preferred_names:
            if want in name or want in adv_name:
                return True
        return False

    @objc.python_method
    def _should_defer_non_preferred(self) -> bool:
        if not self.preferred_names:
            return False
        if self.last_scan_start is None:
            return False
        return (time.time() - self.last_scan_start) < self.preferred_grace

    @objc.python_method
    def _update_name(self, peripheral, advertisementData):
        device_id = peripheral.identifier().UUIDString()
        adv_name = None
        if advertisementData is not None:
            try:
                adv_name = advertisementData.get("kCBAdvDataLocalName")
            except Exception:
                adv_name = None
        if adv_name:
            self.name_by_id[device_id] = str(adv_name)
            return
        name = peripheral.name()
        if name:
            self.name_by_id[device_id] = str(name)

    @objc.python_method
    def _at_capacity(self) -> bool:
        if self.max_devices is None:
            return False
        count = len(self.connected_ids) + len(self.connecting_ids)
        return count >= self.max_devices

    @objc.python_method
    def _block_device(self, device_id: str, reason: str) -> None:
        self.blocked_ids[device_id] = time.time() + self.blocked_ttl
        if device_id in self.pending_reconnect:
            del self.pending_reconnect[device_id]
        label = self.name_by_id.get(device_id, device_id)
        print(f"{reason} Ignoring {label} for {self.blocked_ttl:.0f}s.", file=sys.stderr, flush=True)

    def centralManagerDidUpdateState_(self, central):
        self.central = central
        if central.state() != CBManagerStatePoweredOn:
            return

        # First try to get already-connected devices exposing Heart Rate service.
        connected = central.retrieveConnectedPeripheralsWithServices_([HR_SERVICE])
        if connected:
            for peripheral in connected:
                if self._at_capacity():
                    break
                if not self._match_device(peripheral, None):
                    continue
                self._connect_peripheral(peripheral, None)

        # Otherwise, scan for devices advertising the Heart Rate service.
        self._start_scan()

    def centralManager_didDiscoverPeripheral_advertisementData_RSSI_(
        self, central, peripheral, advertisementData, rssi
    ):
        device_id = peripheral.identifier().UUIDString()
        self.peripherals[device_id] = peripheral
        self._update_name(peripheral, advertisementData)
        if not self._match_device(peripheral, advertisementData):
            return
        preferred = self._is_preferred(peripheral, advertisementData)
        if not preferred and self._should_defer_non_preferred():
            return
        if self._at_capacity():
            return
        self._connect_peripheral(peripheral, advertisementData)

    def _connect_peripheral(self, peripheral, advertisementData):
        device_id = peripheral.identifier().UUIDString()
        if device_id in self.connected_ids or device_id in self.connecting_ids:
            return
        self.peripherals[device_id] = peripheral
        self._update_name(peripheral, advertisementData)
        try:
            peripheral.setDelegate_(self)
        except Exception:
            pass
        self.connecting_ids.add(device_id)
        self.central.connectPeripheral_options_(peripheral, None)

    def centralManager_didConnectPeripheral_(self, central, peripheral):
        self.connected = True
        device_id = peripheral.identifier().UUIDString()
        self.connected_ids.add(device_id)
        if device_id in self.connecting_ids:
            self.connecting_ids.remove(device_id)
        self._reset_reconnect(device_id)
        peripheral.discoverServices_([HR_SERVICE])

    def centralManager_didFailToConnectPeripheral_error_(self, central, peripheral, error):
        self.connected = False
        device_id = peripheral.identifier().UUIDString()
        if device_id in self.connecting_ids:
            self.connecting_ids.remove(device_id)
        print("Failed to connect.", file=sys.stderr, flush=True)
        self._schedule_reconnect(device_id, "Connect failed.")

    def centralManager_didDisconnectPeripheral_error_(self, central, peripheral, error):
        self.connected = False
        device_id = peripheral.identifier().UUIDString()
        if device_id in self.connected_ids:
            self.connected_ids.remove(device_id)
        print("Disconnected.", file=sys.stderr, flush=True)
        if device_id in self.blocked_ids and time.time() < self.blocked_ids.get(device_id, 0.0):
            return
        self._schedule_reconnect(device_id, "Disconnected.")

    def peripheral_didDiscoverServices_(self, peripheral, error):
        if error is not None:
            device_id = peripheral.identifier().UUIDString()
            self._schedule_reconnect(device_id, "Service discovery failed.")
            return
        found = False
        for service in peripheral.services() or []:
            if service.UUID().isEqual_(HR_SERVICE):
                found = True
                peripheral.discoverCharacteristics_forService_([HR_MEAS], service)
        if not found:
            device_id = peripheral.identifier().UUIDString()
            self._block_device(device_id, "No Heart Rate service.")
            try:
                self.central.cancelPeripheralConnection_(peripheral)
            except Exception:
                pass

    def peripheral_didDiscoverCharacteristicsForService_error_(self, peripheral, service, error):
        if error is not None:
            device_id = peripheral.identifier().UUIDString()
            self._schedule_reconnect(device_id, "Characteristic discovery failed.")
            return
        found = False
        for char in service.characteristics() or []:
            if char.UUID().isEqual_(HR_MEAS):
                found = True
                peripheral.setNotifyValue_forCharacteristic_(True, char)
        if not found:
            device_id = peripheral.identifier().UUIDString()
            self._block_device(device_id, "No Heart Rate characteristic.")
            try:
                self.central.cancelPeripheralConnection_(peripheral)
            except Exception:
                pass

    def peripheral_didUpdateValueForCharacteristic_error_(self, peripheral, characteristic, error):
        if error is not None:
            return
        value = characteristic.value()
        if value is None:
            return
        hr = parse_hr_measurement(bytes(value))
        if hr is None:
            return
        device_id = peripheral.identifier().UUIDString()
        self._update_name(peripheral, None)
        label = self.name_by_id.get(device_id, device_id)
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {label} HR={hr} bpm", flush=True)
        self._write_hr(hr, label, device_id)


def main():
    ap = argparse.ArgumentParser(description="CoreBluetooth heart-rate listener.")
    ap.add_argument("--file", help="Write JSONL heart-rate stream to file")
    ap.add_argument("--truncate", action="store_true", help="Truncate output file before writing")
    ap.add_argument("--name", action="append", help="Substring match on device name (repeatable)")
    ap.add_argument("--id", action="append", help="Substring match on device identifier UUID (repeatable)")
    ap.add_argument("--max-devices", type=int, default=None, help="Maximum number of devices to connect")
    ap.add_argument("--scan-all", action="store_true", help="Scan without Heart Rate service filter")
    ap.add_argument("--sources-file", help="JSON file with preferred source names/addresses")
    ap.add_argument("--preferred-grace", type=float, default=6.0, help="Seconds to prefer sources before others")
    args = ap.parse_args()

    if args.file and args.truncate:
        os.makedirs(os.path.dirname(args.file), exist_ok=True)
        open(args.file, "w", encoding="utf-8").close()

    preferred = []
    if args.sources_file:
        try:
            with open(args.sources_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        name = item.get("name") or item.get("source") or item.get("address")
                        if name:
                            preferred.append(str(name))
                    elif isinstance(item, str):
                        preferred.append(item)
            elif isinstance(data, dict):
                raw = data.get("sources") or data.get("devices") or []
                for item in raw:
                    if isinstance(item, dict):
                        name = item.get("name") or item.get("source") or item.get("address")
                        if name:
                            preferred.append(str(name))
                    elif isinstance(item, str):
                        preferred.append(item)
        except Exception:
            pass

    delegate = HRDelegate.alloc().init()
    delegate.set_output(args.file)
    delegate.set_filters(args.name, args.id, args.max_devices, args.scan_all)
    delegate.set_preferred(preferred, args.preferred_grace)
    queue = dispatch_queue_create(b"ble.hr.corebluetooth", DISPATCH_QUEUE_SERIAL)
    _manager = CBCentralManager.alloc().initWithDelegate_queue_(delegate, queue)

    print("Listening for Heart Rate (CoreBluetooth)...", flush=True)
    loop = NSRunLoop.currentRunLoop()
    try:
        while True:
            loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))
            delegate.tick()
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
