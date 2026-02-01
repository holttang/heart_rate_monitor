# BLE Heart Rate Broadcast (macOS)

Use Python on macOS to scan BLE heart-rate sources and visualize them as breathing LEDs plus live charts.

## Structure

- `ble_hr_broadcast.py` Scan advertisements and try parsing HR from payloads
- `ble_hr_gatt.py` Subscribe via GATT (recommended when devices support it)
- `ble_hr_corebluetooth.py` CoreBluetooth listener (macOS)
- `hr_simulator.py` Generate simulated HR stream into a local file
- `hr_display.py` UI: breathing LEDs + live charts + score timeline
- `hr_scan_sources.py` Scan & save HR sources for later preferred scanning

## Requirements

- Python 3.9+
- `bleak`
- `tkinter` (for `hr_display.py`; system Python includes it, Homebrew Python needs python-tk)

Install dependencies:

```bash
python3 -m pip install bleak
```

If you use Homebrew Python (`/opt/homebrew/bin/python3.13`), install Tk:

```bash
brew install python-tk@3.13
```

## Usage

Scan advertisements:

```bash
python3 ble_hr_broadcast.py
```

Filter by name:

```bash
python3 ble_hr_broadcast.py --name Mi
```

Filter by address:

```bash
python3 ble_hr_broadcast.py --address XX:XX:XX:XX:XX:XX
```

Scan for N seconds:

```bash
python3 ble_hr_broadcast.py --timeout 30
```

Use GATT (recommended):

```bash
python3 ble_hr_gatt.py --name "Xiaomi Smart Band"
```

Or by address:

```bash
python3 ble_hr_gatt.py --address XX:XX:XX:XX:XX:XX
```

Use CoreBluetooth (macOS):

```bash
python3 ble_hr_corebluetooth.py
```

Save / prefer previously scanned sources:

```bash
./.venv/bin/python ble_hr_corebluetooth.py \
  --sources-file data/hr_sources.json \
  --preferred-grace 6 \
  --scan-all
```

Multiple devices:

```bash
./.venv/bin/python ble_hr_corebluetooth.py \
  --name "Xiaomi Smart Band" \
  --name "Apple Watch" \
  --max-devices 2 \
  --scan-all \
  --file data/hr_stream.jsonl --truncate
```

## Live HR + UI

Stop the simulator, write real HR into a file, then start the UI:

```bash
./.venv/bin/python ble_hr_corebluetooth.py --file data/hr_stream.jsonl --truncate
```

```bash
python3 hr_display.py --file data/hr_stream.jsonl
```

Multi-player view (single window adapts; second panel hides after N seconds without data):

```bash
python3 hr_display.py \
  --file data/hr_stream.jsonl \
  --hide-seconds 12
```

UI controls:
- **Scan all sources**: scans and saves to `data/hr_sources.json`; CoreBluetooth can prioritize these via `--sources-file`.
- **Always on top**: keeps the window above others.
- **Show logs**: toggles detailed log overlay.
- **Inline timeline**: shows the score timeline below the players; otherwise it opens in a new window on Start.
- **Timer**: enter minutes, Start begins a scoring session.
Tip: pass `--listener-log data/hr_corebluetooth.log` to display connection logs in the overlay.

Scoring session:
- Score = average + min + max (computed during the session).
- Lower score wins.
- The score timeline shows both curves with labels at the latest points (source name + current score).

## Simulation + UI

Generate a simulated HR stream:

```bash
python3 hr_simulator.py --file data/hr_stream.jsonl
```

Open the UI:

```bash
python3 hr_display.py --file data/hr_stream.jsonl
```

## Notes

- The broadcast parser looks in `service_data` and `manufacturer_data` for standard HR payloads.
- If no HR appears, the device likely does not embed HR in advertisements. Use GATT (`ble_hr_gatt.py`) instead.
