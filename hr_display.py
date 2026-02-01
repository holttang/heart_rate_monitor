#!/usr/bin/env python3
"""Display a breathing LED UI driven by heart-rate data read from a local file."""
import argparse
import json
import math
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

import tkinter as tk
import tkinter.font as tkfont


BG = "#f6f1e7"
PANEL = "#fdf9f2"
TEXT = "#1f2937"
MUTED = "#6b7280"
GRID = "#d9d1c6"
GREEN = (34, 197, 94)
ORANGE = (255, 140, 0)
RED = (239, 68, 68)
WHITE = (255, 255, 255)
DARK = (25, 30, 35)
SCORE_COLORS = ["#2563eb", "#ef4444", "#10b981", "#f59e0b"]


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mix_rgb(a: Tuple[int, int, int], b: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    return (
        int(lerp(a[0], b[0], t)),
        int(lerp(a[1], b[1], t)),
        int(lerp(a[2], b[2], t)),
    )


def sanitize_text(text: str) -> str:
    if not text:
        return ""
    return "".join(ch if ord(ch) < 128 else "?" for ch in text)


def theme_from_base(base_rgb: Tuple[int, int, int]) -> dict:
    bg_rgb = mix_rgb(base_rgb, WHITE, 0.55)
    panel_rgb = mix_rgb(base_rgb, WHITE, 0.62)
    grid_rgb = mix_rgb(base_rgb, WHITE, 0.38)
    text_rgb = mix_rgb(base_rgb, DARK, 0.85)
    muted_rgb = mix_rgb(base_rgb, DARK, 0.68)
    line_rgb = mix_rgb(base_rgb, DARK, 0.25)
    return {
        "bg": rgb_to_hex(bg_rgb),
        "panel": rgb_to_hex(panel_rgb),
        "grid": rgb_to_hex(grid_rgb),
        "text": rgb_to_hex(text_rgb),
        "muted": rgb_to_hex(muted_rgb),
        "line": rgb_to_hex(line_rgb),
        "bg_rgb": bg_rgb,
    }


def choose_font(candidates: List[str], size: int, weight: str = "normal") -> Tuple[str, int, str]:
    families = set(tkfont.families())
    for f in candidates:
        if f in families:
            return (f, size, weight)
    return ("Helvetica", size, weight)


class FileTail:
    def __init__(self, path: str):
        self.path = path
        self.pos = 0
        self.inode = None

    def reset(self) -> None:
        self.pos = 0
        self.inode = None

    def read_new_lines(self) -> List[str]:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return []

        if self.inode != st.st_ino or st.st_size < self.pos:
            self.inode = st.st_ino
            self.pos = 0

        with open(self.path, "r", encoding="utf-8") as f:
            f.seek(self.pos)
            lines = f.readlines()
            self.pos = f.tell()
        return lines


class PlayerUI:
    def __init__(
        self,
        parent: tk.Widget,
        source: str,
        display_name: str,
        rate_label_font: Tuple[str, int, str],
        rate_value_font: Tuple[str, int, str],
        subtitle_font: Tuple[str, int, str],
        base_bg: str,
    ):
        self.source = source
        self.display_name = display_name
        self.history: List[Tuple[float, int]] = []
        self.current_hr: Optional[int] = None
        self.last_sample_time: Optional[float] = None
        self.hr_smoothed: Optional[float] = None
        self.intensity_smoothed: float = 0.0
        self.last_beat_time: Optional[float] = None
        self.last_log_time: float = 0.0
        self.last_log_hr: Optional[int] = None
        self.last_state: str = "WAITING"
        self.theme = theme_from_base(GREEN)
        self.bg_rgb = hex_to_rgb(BG)
        self.hidden = False
        self.hidden_since: Optional[float] = None
        self.session_sum: float = 0.0
        self.session_count: int = 0
        self.session_min: Optional[int] = None
        self.session_max: Optional[int] = None
        self.session_scores: List[Tuple[float, float]] = []
        self.last_score_time: Optional[float] = None
        self.score_color: str = SCORE_COLORS[0]

        self.frame = tk.Frame(parent, bg=base_bg)
        self.header = tk.Frame(self.frame, bg=base_bg)
        self.header.pack(fill="x", padx=8, pady=(8, 2))
        self.source_label = tk.Label(
            self.header, text=display_name, bg=base_bg, fg=MUTED, font=rate_label_font
        )
        self.source_label.pack(anchor="w")
        self.rate_value = tk.Label(
            self.header, text="-- bpm", bg=base_bg, fg=TEXT, font=rate_value_font
        )
        self.rate_value.pack(anchor="w", pady=(2, 0))

        self.canvas = tk.Canvas(self.frame, bg=base_bg, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=8, pady=(6, 0))

        self.stats = tk.Label(self.frame, text="Waiting for data...", bg=base_bg, fg=MUTED, font=subtitle_font)
        self.stats.pack(fill="x", padx=8, pady=(6, 8))

        self.led_items = {}

class HRDisplayApp:
    def __init__(
        self,
        root: tk.Tk,
        data_file: str,
        window_secs: int,
        hr_min: int,
        hr_max: int,
        restart_cmd: Optional[str],
        restart_pid_file: Optional[str],
        restart_log: Optional[str],
        listener_log: Optional[str],
        stale_seconds: float,
        source_filter: Optional[List[str]],
        sources_file: Optional[str],
        hide_seconds: float,
        scan_python: Optional[str],
        scan_time: float,
        scan_max_connect: int,
        scan_timeout: float,
    ):
        self.root = root
        self.data_file = data_file
        self.window_secs = window_secs
        self.hr_min = hr_min
        self.hr_max = hr_max
        self.restart_cmd = restart_cmd
        self.restart_pid_file = restart_pid_file
        self.restart_log = restart_log
        self.listener_log = listener_log
        self.stale_seconds = stale_seconds
        self.source_filter = [s.lower() for s in source_filter] if source_filter else None
        self.sources_file = sources_file
        self.hide_seconds = hide_seconds
        self.scan_python = scan_python
        self.scan_time = scan_time
        self.scan_max_connect = scan_max_connect
        self.scan_timeout = scan_timeout
        self.scan_script = os.path.join(os.path.dirname(__file__), "hr_scan_sources.py")
        self.tail = FileTail(data_file)
        self.listener_tail = FileTail(listener_log) if listener_log else None

        self.players: dict = {}
        self.player_order: List[str] = []
        self.last_layout_count = 0

        self.restart_busy = False
        self.restart_log_fh = None
        self.status_override_until: Optional[float] = None
        self.status_override_text: Optional[str] = None
        self.log_lines: List[str] = []
        self.log_max_lines = 7
        self.global_status: str = "WAITING"
        self.saved_sources: List[str] = []
        self.scan_busy = False
        self.scan_forced_logs = False
        self.session_active = False
        self.session_start_time: Optional[float] = None
        self.session_end_time: Optional[float] = None
        self.session_duration: Optional[float] = None
        self.session_result: Optional[str] = None

        self._setup_ui()
        self._load_sources()
        self._apply_restart_sources()
        self._schedule_updates()

    def _mean_hr_for_theme(self) -> Optional[float]:
        values = []
        for player in self.players.values():
            if player.session_count:
                values.append(player.session_sum / player.session_count)
            elif player.current_hr is not None:
                values.append(float(player.current_hr))
        if not values:
            return None
        return sum(values) / len(values)

    def _setup_ui(self) -> None:
        self.root.title("Smart LED Heart Rate")
        self.root.configure(bg=BG)
        self.root.geometry("980x560")
        self.root.minsize(900, 520)

        self.title_font = choose_font(["Avenir Next", "SF Pro Display", "Helvetica Neue", "Arial"], 18, "bold")
        self.subtitle_font = choose_font(["Avenir Next", "SF Pro Text", "Helvetica Neue", "Arial"], 11)
        self.rate_label_font = choose_font(["Avenir Next", "SF Pro Text", "Helvetica Neue", "Arial"], 12, "bold")
        self.rate_value_font = choose_font(["Avenir Next", "SF Pro Display", "Helvetica Neue", "Arial"], 34, "bold")

        self.header_frame = tk.Frame(self.root, bg=BG)
        self.header_frame.pack(fill="x", padx=20, pady=(16, 6))
        self.header_title = tk.Label(
            self.header_frame, text="Smart LED Heart Rate", font=self.title_font, bg=BG, fg=TEXT
        )
        self.header_title.pack(side="left")
        self.header_subtitle = tk.Label(
            self.header_frame,
            text="breathing light + live chart",
            font=self.subtitle_font,
            bg=BG,
            fg=MUTED,
        )
        self.header_subtitle.pack(side="left", padx=(12, 0))
        self.result_label = tk.Label(
            self.header_frame,
            text="",
            font=self.subtitle_font,
            bg=BG,
            fg=MUTED,
        )
        self.result_label.pack(side="left", padx=(12, 0))

        self.controls_frame = tk.Frame(self.header_frame, bg=BG)
        self.controls_frame.pack(side="right")
        self.status_label = tk.Label(
            self.controls_frame, text="WAITING", font=self.subtitle_font, bg=BG, fg=MUTED
        )
        self.status_label.pack(side="left", padx=(0, 12))
        self.scan_button = tk.Button(
            self.controls_frame,
            text="Scan all sources",
            font=self.subtitle_font,
            bg=BG,
            fg=TEXT,
            activebackground=BG,
            activeforeground=TEXT,
            highlightthickness=0,
            relief="groove",
            command=self._scan_sources,
        )
        self.scan_button.pack(side="left", padx=(0, 10))
        self.timer_label = tk.Label(
            self.controls_frame,
            text="Timer: --",
            font=self.subtitle_font,
            bg=BG,
            fg=MUTED,
        )
        self.timer_label.pack(side="left", padx=(0, 8))
        self.timer_minutes_var = tk.StringVar(value="2")
        self.timer_entry = tk.Entry(
            self.controls_frame,
            textvariable=self.timer_minutes_var,
            width=4,
            justify="center",
            font=self.subtitle_font,
            relief="groove",
        )
        self.timer_entry.pack(side="left", padx=(0, 6))
        self.timer_unit = tk.Label(
            self.controls_frame,
            text="min",
            font=self.subtitle_font,
            bg=BG,
            fg=MUTED,
        )
        self.timer_unit.pack(side="left", padx=(0, 8))
        self.timer_start_button = tk.Button(
            self.controls_frame,
            text="Start",
            font=self.subtitle_font,
            bg=BG,
            fg=TEXT,
            activebackground=BG,
            activeforeground=TEXT,
            highlightthickness=0,
            relief="groove",
            command=self._start_timer,
        )
        self.timer_start_button.pack(side="left", padx=(0, 6))
        self.timer_reset_button = tk.Button(
            self.controls_frame,
            text="Reset",
            font=self.subtitle_font,
            bg=BG,
            fg=TEXT,
            activebackground=BG,
            activeforeground=TEXT,
            highlightthickness=0,
            relief="groove",
            command=self._reset_timer,
        )
        self.timer_reset_button.pack(side="left", padx=(0, 10))
        self.inline_var = tk.BooleanVar(value=False)
        self.inline_check = tk.Checkbutton(
            self.controls_frame,
            text="Inline timeline",
            variable=self.inline_var,
            command=self._apply_timeline_layout,
            bg=BG,
            fg=TEXT,
            activebackground=BG,
            activeforeground=TEXT,
            selectcolor=BG,
            font=self.subtitle_font,
        )
        self.inline_check.pack(side="left", padx=(6, 0))
        self.topmost_var = tk.BooleanVar(value=False)
        self.topmost_check = tk.Checkbutton(
            self.controls_frame,
            text="Always on top",
            variable=self.topmost_var,
            command=self._toggle_topmost,
            bg=BG,
            fg=TEXT,
            activebackground=BG,
            activeforeground=TEXT,
            selectcolor=BG,
            font=self.subtitle_font,
        )
        self.topmost_check.pack(side="left", padx=(8, 0))
        self.show_logs_var = tk.BooleanVar(value=True)
        self.show_logs_check = tk.Checkbutton(
            self.controls_frame,
            text="Show logs",
            variable=self.show_logs_var,
            command=self._toggle_logs,
            bg=BG,
            fg=TEXT,
            activebackground=BG,
            activeforeground=TEXT,
            selectcolor=BG,
            font=self.subtitle_font,
        )
        self.show_logs_check.pack(side="left", padx=(8, 0))

        self.main_frame = tk.Frame(self.root, bg=BG)
        self.main_frame.pack(fill="both", expand=True, padx=20, pady=10)
        self.players_frame = tk.Frame(self.main_frame, bg=BG)
        self.players_frame.pack(fill="both", expand=True)
        self.players_grid = tk.Frame(self.players_frame, bg=BG)
        self.players_grid.pack(fill="both", expand=True)
        self.placeholder = tk.Label(
            self.players_frame,
            text="Waiting for data...",
            bg=BG,
            fg=MUTED,
            font=self.subtitle_font,
        )
        self.placeholder.place(relx=0.5, rely=0.5, anchor="center")

        self.inline_view = self._create_session_view(self.players_frame)
        self.inline_view["frame"].pack_forget()
        self.session_window = None
        self.window_view = None

    def _schedule_updates(self) -> None:
        self._update_data()
        self._update_listener_log()
        self._update_breathing()
        self._update_chart()
        self._update_session_view()

    def _set_view(self, mode: str) -> None:
        if mode == "session":
            self.players_frame.pack_forget()
            self.players_frame.pack(fill="both", expand=True)
        else:
            self.inline_view["frame"].pack_forget()

    def _create_session_view(self, parent: tk.Widget) -> dict:
        frame = tk.Frame(parent, bg=BG)
        header = tk.Frame(frame, bg=BG)
        header.pack(fill="x", padx=18, pady=(14, 4))
        title = tk.Label(
            header,
            text="Score timeline",
            font=self.title_font,
            bg=BG,
            fg=TEXT,
        )
        title.pack(side="left")
        subtitle = tk.Label(
            header,
            text="lower score wins",
            font=self.subtitle_font,
            bg=BG,
            fg=MUTED,
        )
        subtitle.pack(side="left", padx=(12, 0))
        canvas = tk.Canvas(frame, bg=BG, highlightthickness=0)
        canvas.pack(fill="both", expand=True, padx=18, pady=(6, 8))
        stats = tk.Label(
            frame,
            text="",
            font=self.subtitle_font,
            bg=BG,
            fg=MUTED,
        )
        stats.pack(fill="x", padx=18, pady=(0, 10))
        result = tk.Label(
            frame,
            text="",
            font=self.title_font,
            bg=BG,
            fg=TEXT,
        )
        result.pack(anchor="w", padx=18, pady=(0, 12))
        return {
            "frame": frame,
            "header": header,
            "title": title,
            "subtitle": subtitle,
            "canvas": canvas,
            "stats": stats,
            "result": result,
        }

    def _load_sources(self) -> None:
        if not self.sources_file:
            return
        try:
            with open(self.sources_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        sources = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("source") or item.get("address")
                    if name:
                        sources.append(str(name))
                elif isinstance(item, str):
                    sources.append(item)
        elif isinstance(data, dict):
            raw = data.get("sources") or data.get("devices") or []
            for item in raw:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("source") or item.get("address")
                    if name:
                        sources.append(str(name))
                elif isinstance(item, str):
                    sources.append(item)
        self.saved_sources = sources
        if sources:
            preview = ", ".join(sanitize_text(s) for s in sources[:4])
            if len(sources) > 4:
                preview += "..."
            self._append_log("Saved sources: " + preview)

    def _apply_restart_sources(self) -> None:
        if not self.sources_file or not self.restart_cmd:
            return
        if "--sources-file" in self.restart_cmd:
            return
        quoted = shlex.quote(self.sources_file)
        self.restart_cmd = f"{self.restart_cmd} --sources-file {quoted}"

    def _toggle_topmost(self) -> None:
        try:
            self.root.attributes("-topmost", bool(self.topmost_var.get()))
        except Exception:
            pass

    def _toggle_logs(self) -> None:
        self.scan_forced_logs = False

    def _apply_timeline_layout(self) -> None:
        if self.inline_var.get():
            if self.session_window is not None:
                try:
                    self.session_window.destroy()
                except Exception:
                    pass
                self.session_window = None
                self.window_view = None
            if self.session_active or self.session_result:
                self.inline_view["frame"].pack(fill="both", expand=True, padx=6, pady=(0, 8))
        else:
            self.inline_view["frame"].pack_forget()
            if self.session_active or self.session_result:
                self._open_session_window()

    def _open_session_window(self) -> None:
        if self.session_window is not None:
            return
        self.session_window = tk.Toplevel(self.root)
        self.session_window.title("Score timeline")
        self.session_window.geometry("980x520")
        self.window_view = self._create_session_view(self.session_window)
        self.window_view["frame"].pack(fill="both", expand=True)

        def on_close():
            self.session_window = None
            self.window_view = None

        self.session_window.protocol("WM_DELETE_WINDOW", on_close)

    def _start_timer(self) -> None:
        try:
            minutes = float(self.timer_minutes_var.get().strip())
        except Exception:
            self._append_log("Invalid minutes")
            return
        if minutes <= 0:
            self._append_log("Minutes must be > 0")
            return
        duration = minutes * 60.0
        now = time.time()
        self.session_active = True
        self.session_start_time = now
        self.session_end_time = now + duration
        self.session_duration = duration
        self.session_result = None
        self.result_label.config(text="")
        if self.inline_var.get():
            self.inline_view["result"].config(text="")
        if self.window_view:
            self.window_view["result"].config(text="")
        self._reset_session_stats()
        self._append_log(f"Timer started: {minutes:.2f} min")
        if self.inline_var.get():
            self.inline_view["frame"].pack(fill="both", expand=True, padx=6, pady=(0, 8))
        else:
            self._open_session_window()

    def _reset_timer(self) -> None:
        self.session_active = False
        self.session_start_time = None
        self.session_end_time = None
        self.session_duration = None
        self.session_result = None
        self.timer_label.config(text="Timer: --")
        self.result_label.config(text="")
        if self.inline_var.get():
            self.inline_view["result"].config(text="")
        if self.window_view:
            self.window_view["result"].config(text="")
        self._reset_session_stats()
        self._append_log("Timer reset")
        self.inline_view["frame"].pack_forget()

    def _reset_session_stats(self) -> None:
        for player in self.players.values():
            player.session_sum = 0.0
            player.session_count = 0
            player.session_min = None
            player.session_max = None
            player.session_scores = []
            player.last_score_time = None

    def _scan_sources(self) -> None:
        if self.scan_busy:
            return
        if not os.path.exists(self.scan_script):
            self._append_log("Scan script missing")
            return
        if not self.sources_file:
            self._append_log("No sources file configured")
            return
        self.scan_busy = True
        if not self.show_logs_var.get():
            self.show_logs_var.set(True)
            self.scan_forced_logs = True
        self.scan_button.config(text="Scanning...", state="disabled")
        self._append_log("Scan started")

        def runner():
            python_bin = self.scan_python
            if not python_bin:
                venv_py = os.path.join(os.path.dirname(__file__), ".venv", "bin", "python")
                python_bin = venv_py if os.path.exists(venv_py) else sys.executable
            cmd = [
                python_bin,
                self.scan_script,
                "--out",
                self.sources_file,
                "--scan-time",
                str(self.scan_time),
                "--max-connect",
                str(self.scan_max_connect),
                "--connect-timeout",
                str(self.scan_timeout),
            ]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, check=False)
                self._scan_result = (result.returncode, result.stdout, result.stderr)
            except Exception as exc:
                self._scan_result = (1, "", str(exc))
            self.root.after(0, self._scan_complete)

        threading.Thread(target=runner, daemon=True).start()

    def _scan_complete(self) -> None:
        self.scan_busy = False
        self.scan_button.config(text="Scan all sources", state="normal")
        code, out, err = getattr(self, "_scan_result", (1, "", ""))
        if code == 0:
            self._append_log("Scan finished")
        else:
            self._append_log("Scan failed")
        if out:
            last_line = out.strip().splitlines()[-1] if out.strip() else ""
            if last_line:
                self._append_log(last_line)
        if err:
            self._append_log(err.splitlines()[-1])
        self._load_sources()
        self._apply_restart_sources()
        if self.scan_forced_logs:
            self.show_logs_var.set(False)
            self.scan_forced_logs = False

    def _finish_timer(self) -> None:
        self.session_active = False
        self.timer_label.config(text="Timer: 00:00")
        order = self.player_order + [s for s in self.players.keys() if s not in self.player_order]
        candidates = [s for s in order if self.players[s].session_count > 0]
        if len(candidates) < 2:
            self.session_result = "Not enough players to score"
            self.result_label.config(text=self.session_result)
            if self.inline_var.get():
                self.inline_view["result"].config(text=self.session_result)
            if self.window_view:
                self.window_view["result"].config(text=self.session_result)
            self._append_log(self.session_result)
            return

        a, b = candidates[0], candidates[1]
        pa = self.players[a]
        pb = self.players[b]

        avg_a = pa.session_sum / pa.session_count
        avg_b = pb.session_sum / pb.session_count
        min_a = pa.session_min if pa.session_min is not None else 0
        min_b = pb.session_min if pb.session_min is not None else 0
        max_a = pa.session_max if pa.session_max is not None else 0
        max_b = pb.session_max if pb.session_max is not None else 0

        score_a = avg_a + min_a + max_a
        score_b = avg_b + min_b + max_b

        if score_a > score_b:
            winner = pb.display_name
            result = f"Winner: {winner} (lower score)"
        elif score_b > score_a:
            winner = pa.display_name
            result = f"Winner: {winner} (lower score)"
        else:
            result = "Tie"

        self.session_result = result
        self.result_label.config(text=result)
        if self.inline_var.get():
            self.inline_view["result"].config(text=result)
        if self.window_view:
            self.window_view["result"].config(text=result)
        self._append_log(result)

    def _ensure_player(self, source: str) -> PlayerUI:
        player = self.players.get(source)
        if player:
            if player.hidden:
                player.hidden = False
                player.hidden_since = None
                if source not in self.player_order:
                    self.player_order.append(source)
                    self._layout_players()
            return player
        display_name = source
        player = PlayerUI(
            self.players_grid,
            source,
            display_name,
            self.rate_label_font,
            self.rate_value_font,
            self.subtitle_font,
            BG,
        )
        player.score_color = SCORE_COLORS[len(self.players) % len(SCORE_COLORS)]
        self.players[source] = player
        self.player_order.append(source)
        self._append_log(f"Detected source: {display_name}")
        self._layout_players()
        return player

    def _hide_player(self, source: str) -> None:
        if source not in self.players:
            return
        player = self.players[source]
        if player.hidden:
            return
        player.hidden = True
        player.hidden_since = time.time()
        if source in self.player_order:
            self.player_order.remove(source)
        player.frame.grid_forget()
        self._append_log(f"{player.display_name}: hidden")
        self._layout_players()

    def _layout_players(self) -> None:
        count = len(self.player_order)
        if count == 0:
            self.placeholder.place(relx=0.5, rely=0.5, anchor="center")
        else:
            self.placeholder.place_forget()

        cols = 1 if count <= 1 else 2
        rows = (count + cols - 1) // cols if count else 1

        for r in range(rows):
            self.players_grid.rowconfigure(r, weight=1)
        for c in range(cols):
            self.players_grid.columnconfigure(c, weight=1)

        for idx, source in enumerate(self.player_order):
            player = self.players[source]
            row = idx // cols
            col = idx % cols
            player.frame.grid(row=row, column=col, sticky="nsew", padx=10, pady=10)

        if count != self.last_layout_count:
            if count <= 1:
                self.root.geometry("980x560")
            elif count == 2:
                self.root.geometry("1200x640")
            else:
                self.root.geometry("1200x760")
            self.last_layout_count = count

    def _update_data(self) -> None:
        lines = self.tail.read_new_lines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                bpm = int(payload.get("bpm"))
                source = payload.get("source") or payload.get("device") or payload.get("id")
            except Exception:
                continue
            if source is None:
                source = "unknown"
            source_str = str(source)
            if self.source_filter:
                if not any(want in source_str.lower() for want in self.source_filter):
                    continue
            player = self._ensure_player(source_str)
            now = time.time()
            player.current_hr = bpm
            player.last_sample_time = now
            player.history.append((now, bpm))
            if self.session_active:
                player.session_sum += bpm
                player.session_count += 1
                if player.session_min is None or bpm < player.session_min:
                    player.session_min = bpm
                if player.session_max is None or bpm > player.session_max:
                    player.session_max = bpm
                if player.session_min is not None and player.session_max is not None:
                    avg = player.session_sum / player.session_count
                    score = avg + player.session_min + player.session_max
                    if player.last_score_time is None or (now - player.last_score_time) >= 1.0:
                        player.session_scores.append((now, score))
                        player.last_score_time = now
            if bpm != player.last_log_hr or (now - player.last_log_time) > 2.0:
                self._append_log(f"{player.display_name}: {bpm} bpm")
                player.last_log_time = now
                player.last_log_hr = bpm

        cutoff = time.time() - self.window_secs
        for player in self.players.values():
            player.history = [(t, v) for (t, v) in player.history if t >= cutoff]

        self.root.after(200, self._update_data)

    def _update_listener_log(self) -> None:
        if not self.listener_tail:
            self.root.after(500, self._update_listener_log)
            return
        lines = self.listener_tail.read_new_lines()
        for line in lines:
            text = line.strip()
            if not text:
                continue
            lower = text.lower()
            if (
                "disconnected" in lower
                or "reconnecting" in lower
                or "reconnect attempt" in lower
                or "failed to connect" in lower
                or "listening for heart rate" in lower
                or "connect failed" in lower
            ):
                self._append_log(text)
        self.root.after(500, self._update_listener_log)

    def _append_log(self, message: str) -> None:
        ts = time.strftime("%H:%M:%S")
        entry = f"[{ts}] {sanitize_text(message)}"
        self.log_lines.append(entry)
        if len(self.log_lines) > self.log_max_lines:
            self.log_lines = self.log_lines[-self.log_max_lines :]

    def _reset_data_state(self) -> None:
        for player in self.players.values():
            player.frame.destroy()
        self.players = {}
        self.player_order = []
        self.last_layout_count = 0
        self._layout_players()

    def _restart_listener(self) -> None:
        if self.restart_busy:
            return
        self.restart_busy = True
        self.status_override_text = "RESTARTING"
        self.status_override_until = time.time() + 2.0
        self._reset_data_state()
        self.tail.reset()
        if self.listener_tail:
            self.listener_tail.reset()
        self._append_log("Manual restart")

        if self.restart_pid_file:
            try:
                with open(self.restart_pid_file, "r", encoding="utf-8") as f:
                    pid = int(f.read().strip() or "0")
                if pid > 0:
                    os.kill(pid, signal.SIGTERM)
            except Exception:
                pass

        if self.restart_cmd:
            try:
                cmd = shlex.split(self.restart_cmd)
                stdout = None
                stderr = None
                if self.restart_log:
                    os.makedirs(os.path.dirname(self.restart_log), exist_ok=True)
                    if self.restart_log_fh:
                        try:
                            self.restart_log_fh.close()
                        except Exception:
                            pass
                    self.restart_log_fh = open(self.restart_log, "a", encoding="utf-8", buffering=1)
                    stdout = self.restart_log_fh
                    stderr = self.restart_log_fh
                proc = subprocess.Popen(cmd, stdout=stdout, stderr=stderr)
                if self.restart_pid_file:
                    with open(self.restart_pid_file, "w", encoding="utf-8") as f:
                        f.write(str(proc.pid))
            except Exception:
                pass

        self.root.after(1500, self._clear_restart_busy)

    def _clear_restart_busy(self) -> None:
        self.restart_busy = False

    def _color_from_hr(self, bpm: int) -> Tuple[int, int, int]:
        t = clamp((bpm - self.hr_min) / (self.hr_max - self.hr_min), 0.0, 1.0)
        if t <= 0.5:
            return mix_rgb(GREEN, ORANGE, t / 0.5)
        return mix_rgb(ORANGE, RED, (t - 0.5) / 0.5)

    def _format_window(self) -> str:
        if self.window_secs >= 3600:
            return f"{self.window_secs / 3600:.1f} h"
        if self.window_secs >= 60:
            return f"{int(self.window_secs / 60)} min"
        return f"{self.window_secs}s"

    def _rolling_avg(self, player: PlayerUI, window_secs: float) -> float:
        cutoff = time.time() - window_secs
        values = [v for (t, v) in player.history if t >= cutoff]
        if values:
            return sum(values) / len(values)
        if player.current_hr is not None:
            return float(player.current_hr)
        return 60.0

    def _update_breathing(self) -> None:
        now = time.time()
        if self.session_active and self.session_end_time is not None:
            remaining = max(0.0, self.session_end_time - now)
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            self.timer_label.config(text=f"Timer: {mins:02d}:{secs:02d}")
            if remaining <= 0.01:
                self._finish_timer()
        elif self.session_result:
            self.timer_label.config(text="Timer: done")
        statuses = []

        visible_sources = list(self.player_order)
        for idx, source in enumerate(visible_sources):
            player = self.players[source]
            gap = time.time() - player.last_sample_time if player.last_sample_time else None
            if (
                idx >= 1
                and self.hide_seconds > 0
                and gap is not None
                and gap > self.hide_seconds
            ):
                self._hide_player(source)
                continue
            stale = player.last_sample_time is None or (now - player.last_sample_time) > self.stale_seconds

            rolling_hr = self._rolling_avg(player, 5.0)
            if player.hr_smoothed is None:
                player.hr_smoothed = float(rolling_hr)
            else:
                player.hr_smoothed += 0.06 * (rolling_hr - player.hr_smoothed)

            hr_display = int(round(player.hr_smoothed))
            bpm_effective = clamp(player.hr_smoothed, self.hr_min, self.hr_max)
            interval = 60.0 / max(1.0, bpm_effective)
            norm = (bpm_effective - self.hr_min) / (self.hr_max - self.hr_min)
            norm = clamp(norm, 0.0, 1.0)
            amp_scale = 0.40 + 0.20 * norm

            if player.last_beat_time is None:
                player.last_beat_time = now
            while now - player.last_beat_time >= interval:
                player.last_beat_time += interval

            phase = (now - player.last_beat_time) / interval if interval > 0 else 0.0
            attack = 0.10 + 0.08 * (1.0 - norm)
            peak_hold = 0.05 + 0.03 * (1.0 - norm)
            peak_floor = 0.70 + 0.08 * (1.0 - norm)
            if phase < attack:
                envelope = phase / attack
            elif phase < attack + peak_hold:
                t = (phase - attack) / peak_hold
                envelope = 1.0 - (1.0 - peak_floor) * (t ** 1.3)
            else:
                decay_t = (phase - attack - peak_hold) / (1.0 - attack - peak_hold)
                decay_speed = 1.8 + 1.6 * norm
                envelope = peak_floor * math.exp(-decay_speed * decay_t)

            raw_intensity = 0.08 + 0.92 * envelope
            if stale:
                raw_intensity = 0.06

            player.intensity_smoothed += 0.28 * (raw_intensity - player.intensity_smoothed)
            intensity = player.intensity_smoothed

            base_rgb = self._color_from_hr(hr_display)
            player.theme = theme_from_base(base_rgb)
            player.bg_rgb = player.theme["bg_rgb"]
            self._apply_player_theme(player)
            led_rgb = mix_rgb(player.bg_rgb, base_rgb, intensity)
            glow_rgb = mix_rgb(player.bg_rgb, base_rgb, intensity * 0.55)

            canvas = player.canvas
            w = canvas.winfo_width() or 420
            h = canvas.winfo_height() or 420
            size = min(w, h)
            cx, cy = w / 2, h / 2
            scale = 0.68 + (0.55 * amp_scale) * intensity
            outer_r = size * 0.36 * scale
            inner_r = size * 0.27 * scale

            if not player.led_items:
                player.led_items["outer"] = canvas.create_oval(
                    0, 0, 0, 0, fill=rgb_to_hex(glow_rgb), outline="", tags="led"
                )
                player.led_items["inner"] = canvas.create_oval(
                    0, 0, 0, 0, fill=rgb_to_hex(led_rgb), outline="", tags="led"
                )
                player.led_items["status"] = canvas.create_text(
                    cx, cy - outer_r - 18, text="", fill=MUTED, font=("Helvetica", 10), tags="led"
                )

            canvas.coords(
                player.led_items["outer"],
                cx - outer_r,
                cy - outer_r,
                cx + outer_r,
                cy + outer_r,
            )
            canvas.coords(
                player.led_items["inner"],
                cx - inner_r,
                cy - inner_r,
                cx + inner_r,
                cy + inner_r,
            )
            canvas.coords(player.led_items["status"], cx, cy - outer_r - 20)
            canvas.itemconfig(player.led_items["outer"], fill=rgb_to_hex(glow_rgb))
            canvas.itemconfig(player.led_items["inner"], fill=rgb_to_hex(led_rgb))

            if self.status_override_until and now < self.status_override_until:
                status = self.status_override_text or "RESTARTING"
            elif player.current_hr is None:
                status = "WAITING"
            elif stale:
                status = "DISCONNECTED"
            else:
                status = "LIVE"

            if status != player.last_state and status in ("DISCONNECTED", "LIVE"):
                if status == "DISCONNECTED":
                    self._append_log(f"{player.display_name}: disconnected")
                else:
                    self._append_log(f"{player.display_name}: connected")
            player.last_state = status

            status_color = player.theme["muted"] if status in ("WAITING", "DISCONNECTED") else player.theme["text"]
            canvas.itemconfig(player.led_items["status"], text=status, fill=status_color)
            player.rate_value.config(text=f"{hr_display} bpm")
            canvas.tag_raise("led")
            statuses.append(status)

        count = len(self.player_order)
        if self.source_filter:
            source_label = "filter: " + ", ".join(sanitize_text(s) for s in self.source_filter)
        elif count == 1:
            source_label = self.players[self.player_order[0]].display_name
        elif count > 1:
            source_label = f"players: {count}"
        else:
            source_label = ""
        if source_label:
            self.header_subtitle.config(text=f"breathing light + live chart | {source_label}")
        else:
            self.header_subtitle.config(text="breathing light + live chart")

        if self.status_override_until and now < self.status_override_until:
            global_status = self.status_override_text or "RESTARTING"
        elif not statuses:
            global_status = "WAITING"
        elif "LIVE" in statuses:
            global_status = "LIVE"
        elif "DISCONNECTED" in statuses:
            global_status = "DISCONNECTED"
        else:
            global_status = "WAITING"
        self.global_status = global_status

        primary_theme = None
        if count == 1 and self.player_order:
            primary_theme = self.players[self.player_order[0]].theme
        self._apply_global_theme(primary_theme)

        self.root.after(33, self._update_breathing)

    def _apply_global_theme(self, theme: Optional[dict]) -> None:
        if theme:
            bg = theme["bg"]
            text = theme["text"]
            muted = theme["muted"]
        else:
            bg = BG
            text = TEXT
            muted = MUTED

        self.root.configure(bg=bg)
        self.header_frame.configure(bg=bg)
        self.header_title.configure(bg=bg, fg=text)
        self.header_subtitle.configure(bg=bg, fg=muted)
        self.result_label.configure(bg=bg, fg=muted)
        self.main_frame.configure(bg=bg)
        self.players_frame.configure(bg=bg)
        self.players_grid.configure(bg=bg)
        self._apply_session_theme(self.inline_view, bg, text, muted)
        if self.window_view:
            self._apply_session_theme(self.window_view, bg, text, muted)
        self.controls_frame.configure(bg=bg)
        status_color = muted if self.global_status in ("WAITING", "DISCONNECTED") else text
        self.status_label.configure(bg=bg, fg=status_color, text=self.global_status)
        self.scan_button.configure(bg=bg, fg=text, activebackground=bg, activeforeground=text)
        self.timer_label.configure(bg=bg, fg=muted)
        self.timer_unit.configure(bg=bg, fg=muted)
        self.timer_entry.configure(bg=bg, fg=text, insertbackground=text, highlightbackground=bg)
        self.timer_start_button.configure(bg=bg, fg=text, activebackground=bg, activeforeground=text)
        self.timer_reset_button.configure(bg=bg, fg=text, activebackground=bg, activeforeground=text)
        self.topmost_check.configure(bg=bg, fg=text, activebackground=bg, activeforeground=text, selectcolor=bg)
        self.show_logs_check.configure(bg=bg, fg=text, activebackground=bg, activeforeground=text, selectcolor=bg)
        self.inline_check.configure(bg=bg, fg=text, activebackground=bg, activeforeground=text, selectcolor=bg)
        self.placeholder.configure(bg=bg, fg=muted)

    def _apply_session_theme(self, view: dict, bg: str, text: str, muted: str) -> None:
        view["frame"].configure(bg=bg)
        view["header"].configure(bg=bg)
        view["title"].configure(bg=bg, fg=text)
        view["subtitle"].configure(bg=bg, fg=muted)
        view["canvas"].configure(bg=bg)
        view["stats"].configure(bg=bg, fg=muted)
        view["result"].configure(bg=bg, fg=text)

    def _apply_player_theme(self, player: PlayerUI) -> None:
        bg = player.theme["bg"]
        text = player.theme["text"]
        muted = player.theme["muted"]
        player.frame.configure(bg=bg)
        player.header.configure(bg=bg)
        player.source_label.configure(bg=bg, fg=muted)
        player.rate_value.configure(bg=bg, fg=text)
        player.canvas.configure(bg=bg)
        player.stats.configure(bg=bg, fg=muted)

    def _update_chart(self) -> None:
        for idx, source in enumerate(self.player_order):
            player = self.players[source]
            canvas = player.canvas
            canvas.delete("chart")
            canvas.delete("log")
            w = canvas.winfo_width() or 480
            h = canvas.winfo_height() or 320

            pad_l, pad_r, pad_t, pad_b = 56, 18, 44, 36
            x0, y0 = pad_l, pad_t
            x1, y1 = w - pad_r, h - pad_b

            canvas.create_rectangle(0, 0, w, h, fill=player.theme["panel"], outline="", tags="chart")

            canvas.create_text(
                x0,
                y0 - 20,
                text=f"Heart Rate (last {self._format_window()})",
                anchor="w",
                fill=player.theme["muted"],
                font=("Helvetica", 10, "bold"),
                tags="chart",
            )

            for i in range(5):
                y = y0 + (y1 - y0) * i / 4.0
                canvas.create_line(x0, y, x1, y, fill=player.theme["grid"], width=1, tags="chart")

            if not player.history:
                canvas.create_text(
                    w / 2,
                    h / 2,
                    text="Waiting for heart-rate data",
                    fill=player.theme["muted"],
                    font=("Helvetica", 12),
                    tags="chart",
                )
                base_stats = "Waiting for data..."
                if player.session_count > 0:
                    avg = player.session_sum / player.session_count
                    base_stats += f" | Avg: {avg:.1f} Min: {player.session_min} Max: {player.session_max}"
                player.stats.config(text=base_stats)
                if idx == 0:
                    self._draw_log_overlay(canvas, player.theme)
                    canvas.tag_raise("log")
                canvas.tag_lower("chart")
                canvas.tag_raise("led")
                continue

            t_now = time.time()
            t_min = t_now - self.window_secs
            values = [v for (_, v) in player.history]
            v_min = max(self.hr_min, min(values) - 5)
            v_max = min(self.hr_max, max(values) + 5)
            if v_max == v_min:
                v_max = v_min + 1

            points = []
            for t, v in player.history:
                x = lerp(x0, x1, (t - t_min) / self.window_secs)
                y = lerp(y1, y0, (v - v_min) / (v_max - v_min))
                points.append((x, y))

            for i in range(1, len(points)):
                canvas.create_line(
                    points[i - 1][0],
                    points[i - 1][1],
                    points[i][0],
                    points[i][1],
                    fill=player.theme["line"],
                    width=2,
                    tags="chart",
                )

            canvas.create_text(
                x0,
                y0 - 4,
                text=f"{int(v_max)} bpm",
                anchor="w",
                fill=player.theme["muted"],
                font=("Helvetica", 10),
                tags="chart",
            )
            canvas.create_text(
                x0,
                y1 + 16,
                text=f"{int(v_min)} bpm",
                anchor="w",
                fill=player.theme["muted"],
                font=("Helvetica", 10),
                tags="chart",
            )

            last_hr = player.current_hr if player.current_hr is not None else values[-1]
            gap = time.time() - player.last_sample_time if player.last_sample_time else None
            stats = f"Now: {last_hr} bpm | Window: {self._format_window()}"
            if gap is not None and gap > self.stale_seconds:
                stats += f" | Last: {int(gap)}s ago"
            if player.session_count > 0:
                avg = player.session_sum / player.session_count
                stats += f" | Avg: {avg:.1f} Min: {player.session_min} Max: {player.session_max}"
            player.stats.config(text=stats)

            if idx == 0:
                self._draw_log_overlay(canvas, player.theme)

            canvas.tag_lower("chart")
            if idx == 0:
                canvas.tag_raise("log")
            canvas.tag_raise("led")

        self.root.after(500, self._update_chart)

    def _update_session_view(self) -> None:
        if not self.session_active and not self.session_result:
            self.root.after(500, self._update_session_view)
            return

        if self.inline_var.get():
            self.inline_view["frame"].pack(fill="both", expand=True, padx=6, pady=(0, 8))
        elif self.session_active or self.session_result:
            self._open_session_window()

        views = []
        if self.inline_var.get():
            views.append(self.inline_view)
        if self.window_view:
            views.append(self.window_view)

        for view in views:
            self._draw_session_view(view)

        self.root.after(500, self._update_session_view)

    def _draw_session_view(self, view: dict) -> None:
        canvas = view["canvas"]
        canvas.delete("all")
        w = canvas.winfo_width() or 640
        h = canvas.winfo_height() or 360

        pad_l, pad_r, pad_t, pad_b = 60, 24, 32, 44
        x0, y0 = pad_l, pad_t
        x1, y1 = w - pad_r, h - pad_b

        mean_hr = self._mean_hr_for_theme()
        base_rgb = self._color_from_hr(int(round(mean_hr))) if mean_hr is not None else GREEN
        theme = theme_from_base(base_rgb)
        self._apply_session_theme(view, theme["bg"], theme["text"], theme["muted"])
        panel = theme["panel"]
        shadow = rgb_to_hex(mix_rgb(hex_to_rgb(theme["panel"]), DARK, 0.08))
        canvas.create_rectangle(18, 18, w - 10, h - 10, fill=shadow, outline="")
        canvas.create_rectangle(12, 12, w - 16, h - 16, fill=panel, outline=theme["grid"])

        now = time.time()
        t_start = self.session_start_time if self.session_start_time else now - 1.0
        t_end = self.session_end_time if (self.session_end_time and not self.session_active) else now
        if t_end <= t_start:
            t_end = t_start + 1.0

        all_scores = []
        for player in self.players.values():
            all_scores.extend([s for (_, s) in player.session_scores])
        if all_scores:
            s_min = min(all_scores)
            s_max = max(all_scores)
            if s_max - s_min < 1e-6:
                s_max = s_min + 1.0
        else:
            s_min, s_max = 0.0, 1.0

        for i in range(5):
            y = y0 + (y1 - y0) * i / 4.0
            canvas.create_line(x0, y, x1, y, fill=theme["grid"], width=1)
        canvas.create_text(
            x0,
            y0 - 10,
            text=f"{s_max:.1f}",
            anchor="w",
            fill=theme["muted"],
            font=("Helvetica", 9),
        )
        canvas.create_text(
            x0,
            y1 + 16,
            text=f"{s_min:.1f}",
            anchor="w",
            fill=theme["muted"],
            font=("Helvetica", 9),
        )

        summary_lines = []
        for player in self.players.values():
            if not player.session_scores:
                continue
            points = []
            for t, score in player.session_scores:
                x = lerp(x0, x1, (t - t_start) / (t_end - t_start))
                y = lerp(y1, y0, (score - s_min) / (s_max - s_min))
                points.append((x, y))
            for i in range(1, len(points)):
                canvas.create_line(
                    points[i - 1][0],
                    points[i - 1][1],
                    points[i][0],
                    points[i][1],
                    fill=player.score_color,
                    width=2,
                )
            if points:
                end_x, end_y = points[-1]
                canvas.create_oval(end_x - 3, end_y - 3, end_x + 3, end_y + 3, fill=player.score_color, outline="")
                label = f"{player.display_name} {player.session_scores[-1][1]:.1f}"
                anchor = "w"
                x_offset = 8
                if end_x > x1 - 140:
                    anchor = "e"
                    x_offset = -8
                canvas.create_text(
                    end_x + x_offset,
                    end_y,
                    text=label,
                    anchor=anchor,
                    fill=player.score_color,
                    font=("Helvetica", 9, "bold"),
                )

            if player.session_count:
                avg = player.session_sum / player.session_count
                if player.session_min is not None and player.session_max is not None:
                    score = avg + player.session_min + player.session_max
                    summary_lines.append(
                        f"{player.display_name}: score {score:.1f} (avg {avg:.1f}, min {player.session_min}, max {player.session_max})"
                    )

        view["stats"].config(text=" | ".join(summary_lines[:3]))
        if self.session_result:
            view["result"].config(text=self.session_result)
        else:
            view["result"].config(text="")

    def _draw_log_overlay(self, canvas: tk.Canvas, theme: dict) -> None:
        if not self.show_logs_var.get():
            return
        if not self.log_lines:
            return
        pad = 10
        line_h = 14
        box_w = 320
        box_h = pad * 2 + line_h * len(self.log_lines)
        x0, y0 = 16, 14
        x1, y1 = x0 + box_w, y0 + box_h
        panel_rgb = hex_to_rgb(theme["panel"])
        bg_base_rgb = hex_to_rgb(theme["bg"])
        grid_rgb = hex_to_rgb(theme["grid"])
        bg_rgb = mix_rgb(panel_rgb, bg_base_rgb, 0.7)
        bg_hex = rgb_to_hex(bg_rgb)
        border_rgb = mix_rgb(grid_rgb, bg_base_rgb, 0.5)
        border_hex = rgb_to_hex(border_rgb)
        canvas.create_rectangle(
            x0,
            y0,
            x1,
            y1,
            fill=bg_hex,
            outline=border_hex,
            width=1,
            tags="log",
        )
        for idx, line in enumerate(self.log_lines):
            y = y0 + pad + idx * line_h
            canvas.create_text(
                x0 + pad,
                y,
                text=line,
                anchor="nw",
                fill=theme["text"],
                font=("Helvetica", 9),
                tags="log",
            )

def main() -> None:
    ap = argparse.ArgumentParser(description="Smart LED display driven by heart-rate data.")
    base_dir = Path(__file__).resolve().parent
    default_file = base_dir / "data" / "hr_stream.jsonl"
    default_sources = base_dir / "data" / "hr_sources.json"
    ap.add_argument("--file", default=str(default_file))
    ap.add_argument("--window", type=int, default=1800, help="History window (seconds)")
    ap.add_argument("--min", dest="hr_min", type=int, default=50)
    ap.add_argument("--max", dest="hr_max", type=int, default=200)
    ap.add_argument("--duration", type=float, default=None, help="Auto-close after N seconds (test)")
    ap.add_argument("--restart-cmd", help="Command to restart the heart-rate listener")
    ap.add_argument("--restart-pid-file", help="PID file for the listener to terminate on restart")
    ap.add_argument("--restart-log", help="Log file for restart command output")
    ap.add_argument("--listener-log", help="Tail listener log file and show in UI")
    ap.add_argument("--stale-seconds", type=float, default=5.0, help="Mark disconnected after N seconds")
    ap.add_argument("--hide-seconds", type=float, default=12.0, help="Hide secondary panel after N seconds")
    ap.add_argument("--sources-file", default=str(default_sources))
    ap.add_argument("--scan-python", help="Python executable used for scanning sources")
    ap.add_argument("--scan-time", type=float, default=12.0, help="Scan duration for sources")
    ap.add_argument("--scan-max-connect", type=int, default=5, help="Max connections when scanning sources")
    ap.add_argument("--scan-timeout", type=float, default=8.0, help="Connect timeout when scanning sources")
    ap.add_argument("--source", action="append", help="Only display data from matching source (repeatable)")
    args = ap.parse_args()

    root = tk.Tk()
    app = HRDisplayApp(
        root,
        args.file,
        args.window,
        args.hr_min,
        args.hr_max,
        args.restart_cmd,
        args.restart_pid_file,
        args.restart_log,
        args.listener_log,
        args.stale_seconds,
        args.source,
        args.sources_file,
        args.hide_seconds,
        args.scan_python,
        args.scan_time,
        args.scan_max_connect,
        args.scan_timeout,
    )

    if args.duration is not None:
        root.after(int(args.duration * 1000), root.destroy)

    root.mainloop()


if __name__ == "__main__":
    main()
