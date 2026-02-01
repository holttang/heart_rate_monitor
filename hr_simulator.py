#!/usr/bin/env python3
"""Generate a simulated heart-rate stream into a local file (JSONL)."""
import argparse
import json
import math
import os
import time
from pathlib import Path
from typing import Optional


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def simulate_stream(
    outfile: str,
    interval: float,
    duration: Optional[float],
    hr_min: int,
    hr_max: int,
    period: float,
):
    os.makedirs(os.path.dirname(outfile), exist_ok=True)

    start = time.time()
    mid = (hr_min + hr_max) / 2.0
    amp = (hr_max - hr_min) / 2.0

    with open(outfile, "a", encoding="utf-8", buffering=1) as f:
        while True:
            now = time.time()
            elapsed = now - start
            if duration is not None and elapsed >= duration:
                break

            # Smooth sinusoidal cycle (default: 4 minutes).
            hr = mid + amp * math.sin(2 * math.pi * elapsed / period)
            hr = int(round(clamp(hr, hr_min, hr_max)))

            payload = {"ts": now, "bpm": hr}
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")
            time.sleep(interval)


def main():
    ap = argparse.ArgumentParser(description="Simulate heart-rate data into a JSONL file.")
    default_file = Path(__file__).resolve().parent / "data" / "hr_stream.jsonl"
    ap.add_argument("--file", default=str(default_file))
    ap.add_argument("--interval", type=float, default=0.5, help="Seconds between samples")
    ap.add_argument("--duration", type=float, default=None, help="Seconds to run (omit for forever)")
    ap.add_argument("--min", dest="hr_min", type=int, default=50, help="Minimum bpm")
    ap.add_argument("--max", dest="hr_max", type=int, default=200, help="Maximum bpm")
    ap.add_argument("--period", type=float, default=240.0, help="Cycle period in seconds")
    args = ap.parse_args()

    simulate_stream(args.file, args.interval, args.duration, args.hr_min, args.hr_max, args.period)


if __name__ == "__main__":
    main()
