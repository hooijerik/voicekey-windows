"""
VoiceKey — Push-to-Talk Voice Keyboard (Voxtral)
=================================================
Hold your hotkey, speak, release → text is typed anywhere.

Requirements: sounddevice numpy requests pynput keyboard pyperclip pystray Pillow
"""

import io
import json
import os
import math
import queue
import socket
import struct
import subprocess
import sys
import threading
import time
import wave
import ctypes
from ctypes import wintypes
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Optional-import guards
# ---------------------------------------------------------------------------
try:
    import numpy as np
except ImportError:
    sys.exit("Missing: numpy  →  pip install numpy")

try:
    import sounddevice as sd
except ImportError:
    sys.exit("Missing: sounddevice  →  pip install sounddevice")

try:
    import requests
except ImportError:
    sys.exit("Missing: requests  →  pip install requests")

try:
    from pynput import keyboard as pynput_keyboard
except ImportError:
    sys.exit("Missing: pynput  →  pip install pynput")

try:
    import keyboard as kb
except ImportError:
    sys.exit("Missing: keyboard  →  pip install keyboard")

try:
    import pyperclip
except ImportError:
    sys.exit("Missing: pyperclip  →  pip install pyperclip")

try:
    import pystray
    from pystray import MenuItem, Menu
except ImportError:
    sys.exit("Missing: pystray  →  pip install pystray")

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Missing: Pillow  →  pip install Pillow")

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    sys.exit("Missing: tkinter (usually bundled with Python)")

try:
    import winsound
except ImportError:
    winsound = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_NAME = "VoiceKey"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG = {
    "api_key": "",
    "endpoint": "https://api.mistral.ai/v1/audio/transcriptions",
    "model": "voxtral-mini-latest",
    "hotkey": "right alt",
    "language": "auto",
    "paste_mode": True,
    "sample_rate": 16000,
}

HOTKEY_LIST = [
    "right alt",
    "right ctrl",
    "right shift",
    "f13",
    "f14",
    "f15",
    "pause",
    "scroll lock",
]

LANGUAGE_LIST = ["auto", "en", "nl", "de", "fr", "es", "it", "pt", "pl", "ja", "zh"]

# Maps human-readable hotkey names → pynput Key attribute names
PYNPUT_KEY_MAP = {
    # Many international layouts report Right Alt as AltGr.
    "right alt":    ("alt_r", "alt_gr"),
    "right ctrl":   ("ctrl_r",),
    "right shift":  ("shift_r",),
    "f13":          ("f13",),
    "f14":          ("f14",),
    "f15":          ("f15",),
    "pause":        ("pause",),
    "scroll lock":  ("scroll_lock",),
}

# Icon colours per state
ICON_COLORS = {
    "idle":       (80, 80, 80, 255),
    "recording":  (220, 40, 40, 255),
    "processing": (220, 140, 0, 255),
}

# Absolute floor to ignore empty/micro-tap captures (WAV bytes incl. header).
MIN_AUDIO_BYTES = 800
# If no speech activity was detected, skip very short captures.
MIN_AUDIO_SECONDS_WITHOUT_ACTIVITY = 0.10
CONNECTION_CHECK_INTERVAL = 12
OVERLAY_BRIDGE_ADDR = ("127.0.0.1", 38485)
NO_AUDIO_MESSAGE_DELAY_SECONDS = 5.0
AUDIO_ACTIVITY_THRESHOLD = 0.008
AUDIO_ACTIVITY_LEVEL_THRESHOLD = 0.02
AUDIO_LEVEL_PUSH_INTERVAL_SECONDS = 0.02
AUDIO_LEVEL_NORMALIZATION = 2200.0
AUDIO_LEVEL_NOISE_FLOOR = 0.004
AUDIO_LEVEL_ATTACK = 0.40
AUDIO_LEVEL_RELEASE = 0.18
AUDIO_LEVEL_CURVE = 0.85
READY_CHIME_ALIAS = "SystemAsterisk"
READY_CHIME_COOLDOWN_SECONDS = 0.20
TAURI_OVERLAY_PROCESS_NAME = "voicekey-overlay.exe"
TAURI_OVERLAY_BINARY_NAMES = (
    "voicekey-overlay.exe",
    "VoiceKey Overlay.exe",
)

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _env_flag(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


DEBUG_OVERLAY_STATES = _env_flag("VOICEKEY_DEBUG_OVERLAY")
DEBUG_OVERLAY_VERBOSE = _env_flag("VOICEKEY_DEBUG_OVERLAY_VERBOSE")


def find_tauri_overlay_exe() -> str | None:
    """Return best candidate Tauri overlay executable path, if present."""
    explicit = os.environ.get("VOICEKEY_TAURI_OVERLAY_EXE", "").strip()
    if explicit:
        resolved = os.path.abspath(explicit)
        if os.path.isfile(resolved):
            return resolved

    search_dirs: list[str] = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        search_dirs.extend([exe_dir, os.path.dirname(exe_dir)])
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        search_dirs.extend(
            [
                script_dir,
                os.path.join(script_dir, "overlay-ui", "src-tauri", "target", "release"),
                os.path.join(script_dir, "overlay-ui", "src-tauri", "target", "debug"),
            ]
        )

    seen: set[str] = set()
    for base_dir in search_dirs:
        for binary_name in TAURI_OVERLAY_BINARY_NAMES:
            candidate = os.path.abspath(os.path.join(base_dir, binary_name))
            key = candidate.lower()
            if key in seen:
                continue
            seen.add(key)
            if os.path.isfile(candidate):
                return candidate
    return None


def tauri_overlay_only_enabled() -> bool:
    """Resolve whether native Tk overlay should be disabled in favor of Tauri."""
    raw = os.environ.get("VOICEKEY_TAURI_OVERLAY_ONLY")
    if raw is not None and raw.strip():
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return find_tauri_overlay_exe() is not None


def is_tauri_overlay_process_running() -> bool:
    """Check whether the Tauri overlay process is already running."""
    if os.name != "nt":
        return False
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {TAURI_OVERLAY_PROCESS_NAME}"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return TAURI_OVERLAY_PROCESS_NAME in proc.stdout.lower()
    except Exception:
        return False


def overlay_debug(message: str) -> None:
    if not DEBUG_OVERLAY_STATES:
        return
    stamp = time.strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def get_effective_api_key(cfg: dict) -> str:
    """Return API key stored in Settings/config."""
    return cfg.get("api_key", "").strip()


def sanitize_hotkey(value) -> str:
    """Return a valid hotkey, falling back to default when invalid/empty."""
    key = str(value or "").strip().lower()
    if key in HOTKEY_LIST:
        return key
    return DEFAULT_CONFIG["hotkey"]


def sanitize_language(value) -> str:
    """Return a valid language code, falling back to default when invalid/empty."""
    lang = str(value or "").strip().lower()
    if lang in LANGUAGE_LIST:
        return lang
    return DEFAULT_CONFIG["language"]


def load_config() -> dict:
    """Load config from disk, falling back to defaults for missing keys."""
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
                on_disk = json.load(fh)
            cfg.update(on_disk)
        except Exception:
            pass
    cfg["hotkey"] = sanitize_hotkey(cfg.get("hotkey"))
    cfg["language"] = sanitize_language(cfg.get("language"))
    return cfg


def save_config(cfg: dict) -> None:
    """Persist config to disk."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


# ---------------------------------------------------------------------------
# Runtime status helpers (connection, text target)
# ---------------------------------------------------------------------------

def endpoint_reachable(endpoint: str, timeout: float = 1.5) -> bool:
    """Check if the endpoint host is reachable over TCP."""
    try:
        parsed = urlparse(endpoint or "")
        if not parsed.hostname:
            return False
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((parsed.hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


if os.name == "nt":
    _user32 = ctypes.windll.user32
else:
    _user32 = None


class _GuiThreadInfo(ctypes.Structure):
    """ctypes mirror of the Win32 GUITHREADINFO structure."""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


TEXT_INPUT_CLASSES = {
    "Edit",
    "RichEdit20W",
    "RICHEDIT50W",
    "Scintilla",
}

NON_INPUT_CLASSES = {
    "Shell_TrayWnd",
    "Progman",
    "WorkerW",
    "DV2ControlHost",
}


def _window_class_name(hwnd) -> str:
    """Get a Win32 class name for a window handle."""
    if _user32 is None or not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    if _user32.GetClassNameW(hwnd, buf, 256):
        return buf.value
    return ""


def is_text_input_selected() -> bool | None:
    """
    Best-effort check whether a text input is focused.
    Returns True when likely selected, False when clearly not selected,
    and None when uncertain/unavailable.
    """
    if _user32 is None:
        return None
    try:
        foreground = _user32.GetForegroundWindow()
        if not foreground:
            return None
        thread_id = _user32.GetWindowThreadProcessId(foreground, None)
        info = _GuiThreadInfo()
        info.cbSize = ctypes.sizeof(_GuiThreadInfo)
        if not _user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
            return None
        if info.hwndCaret:
            return True
        focused = info.hwndFocus or foreground
        cls = _window_class_name(focused)
        if cls in TEXT_INPUT_CLASSES:
            return True
        if cls in NON_INPUT_CLASSES:
            return False
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Floating status overlay
# ---------------------------------------------------------------------------

class StatusOverlay:
    """Windows-style floating voice strip aligned with the taskbar area."""

    WINDOW_BG = "#010203"  # chroma key color used for transparent window regions
    SURFACE_FILL = "#2e2e2e"
    SURFACE_STROKE = "#757575"
    SURFACE_TOP_HIGHLIGHT = "#3a3a3a"
    SHADOW_NEAR = "#171717"
    SHADOW_FAR = "#101010"
    WAVE_MAIN = "#ffffff"
    WAVE_SOFT = "#ececec"
    WAVE_DIM = "#cfcfcf"
    BUBBLE_FILL = "#2c2c2c"
    BUBBLE_STROKE = "#757575"
    TEXT = "#ffffff"

    WINDOW_WIDTH = 194
    WINDOW_HEIGHT = 126
    BAR_WIDTH = 160
    BAR_HEIGHT = 47
    BAR_RADIUS = 7
    BUBBLE_WIDTH = 89
    BUBBLE_HEIGHT = 40
    BUBBLE_RADIUS = 7
    LEVEL_ACTIVE_THRESHOLD = 0.05

    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._phase = 0.0
        self._level_filtered = 0.0
        self._native_enabled = not tauri_overlay_only_enabled()
        self._bridge_hide_timer: threading.Timer | None = None
        self._bridge_socket: socket.socket | None = None
        try:
            self._bridge_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except OSError:
            self._bridge_socket = None

    def _bridge_send(self, payload: dict) -> None:
        """Best-effort UDP patch broadcast for the Tauri overlay bridge."""
        if not self._bridge_socket:
            return
        try:
            body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
            self._bridge_socket.sendto(body, OVERLAY_BRIDGE_ADDR)
            if DEBUG_OVERLAY_STATES:
                if DEBUG_OVERLAY_VERBOSE or set(payload.keys()) != {"level"}:
                    overlay_debug(f"overlay-udp {payload}")
        except Exception:
            if DEBUG_OVERLAY_STATES:
                overlay_debug("overlay-udp send failed")

    def _bridge_hide_later(self, delay_ms: int) -> None:
        if self._bridge_hide_timer is not None:
            try:
                self._bridge_hide_timer.cancel()
            except Exception:
                pass
            self._bridge_hide_timer = None
        timer = threading.Timer(max(0.01, delay_ms / 1000.0), lambda: self._bridge_send({"visible": False}))
        timer.daemon = True
        timer.start()
        self._bridge_hide_timer = timer

    def start(self) -> None:
        """Start the UI thread if it is not already running."""
        if not self._native_enabled:
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2)

    def stop(self) -> None:
        """Stop and destroy the overlay."""
        if self._native_enabled:
            self._queue.put(("stop", None))
        self._bridge_send({"visible": False})
        if self._bridge_hide_timer is not None:
            try:
                self._bridge_hide_timer.cancel()
            except Exception:
                pass
            self._bridge_hide_timer = None
        if self._bridge_socket is not None:
            try:
                self._bridge_socket.close()
            except Exception:
                pass
            self._bridge_socket = None

    def show(self) -> None:
        if self._bridge_hide_timer is not None:
            try:
                self._bridge_hide_timer.cancel()
            except Exception:
                pass
            self._bridge_hide_timer = None
        if self._native_enabled:
            self._queue.put(("show", None))
        self._bridge_send({"visible": True})

    def hide(self) -> None:
        if self._native_enabled:
            self._queue.put(("hide", None))
        self._bridge_send({"visible": False})

    def hide_later(self, delay_ms: int = 1500) -> None:
        if self._native_enabled:
            self._queue.put(("hide_later", int(delay_ms)))
        self._bridge_hide_later(int(delay_ms))

    def update(self, **status_values) -> None:
        payload = {k: v for k, v in status_values.items() if v is not None}
        if payload:
            if self._native_enabled:
                self._queue.put(("update", payload))
            self._bridge_send(payload)

    @staticmethod
    def _round_rect_points(x1: float, y1: float, x2: float, y2: float, r: float) -> list[float]:
        return [
            x1 + r, y1,
            x2 - r, y1,
            x2, y1,
            x2, y1 + r,
            x2, y2 - r,
            x2, y2,
            x2 - r, y2,
            x1 + r, y2,
            x1, y2,
            x1, y2 - r,
            x1, y1 + r,
            x1, y1,
        ]

    @staticmethod
    def _draw_round_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, r: float, **kwargs):
        return canvas.create_polygon(
            StatusOverlay._round_rect_points(x1, y1, x2, y2, r),
            smooth=True,
            splinesteps=20,
            **kwargs,
        )

    def _run(self) -> None:
        root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        try:
            root.wm_attributes("-transparentcolor", self.WINDOW_BG)
        except tk.TclError:
            pass
        root.configure(bg=self.WINDOW_BG)

        width = self.WINDOW_WIDTH
        height = self.WINDOW_HEIGHT
        canvas = tk.Canvas(root, width=width, height=height, bg=self.WINDOW_BG, highlightthickness=0, bd=0)
        canvas.pack()

        # Speech strip and shell shadows
        bar_x1 = (width - self.BAR_WIDTH) // 2
        bar_y2 = height - 16
        bar_y1 = bar_y2 - self.BAR_HEIGHT
        bar_x2 = bar_x1 + self.BAR_WIDTH
        self._draw_round_rect(
            canvas,
            bar_x1,
            bar_y1 + 2,
            bar_x2,
            bar_y2 + 2,
            self.BAR_RADIUS + 1,
            fill=self.SHADOW_NEAR,
            outline="",
        )
        self._draw_round_rect(
            canvas,
            bar_x1,
            bar_y1 + 7,
            bar_x2,
            bar_y2 + 7,
            self.BAR_RADIUS + 1,
            fill=self.SHADOW_FAR,
            outline="",
        )
        self._draw_round_rect(
            canvas,
            bar_x1,
            bar_y1,
            bar_x2,
            bar_y2,
            self.BAR_RADIUS,
            fill=self.SURFACE_FILL,
            outline="",
        )
        self._draw_round_rect(
            canvas,
            bar_x1,
            bar_y1,
            bar_x2,
            bar_y2,
            self.BAR_RADIUS,
            fill="",
            outline=self.SURFACE_STROKE,
            width=1,
        )
        canvas.create_line(
            bar_x1 + 8,
            bar_y1 + 7,
            bar_x2 - 8,
            bar_y1 + 7,
            fill=self.SURFACE_TOP_HIGHLIGHT,
            width=1,
        )

        # Teaching-tip bubble (shown only in selected states)
        bubble_w = self.BUBBLE_WIDTH
        bubble_h = self.BUBBLE_HEIGHT
        bubble_x1 = (width - bubble_w) // 2
        bubble_y1 = 14
        bubble_x2 = bubble_x1 + bubble_w
        bubble_y2 = bubble_y1 + bubble_h
        pointer_w = 14
        pointer_h = 7
        pointer_left = (width - pointer_w) // 2
        pointer_right = pointer_left + pointer_w
        pointer_tip_x = width // 2
        pointer_tip_y = bubble_y2 + pointer_h
        bubble_shadow = self._draw_round_rect(
            canvas,
            bubble_x1,
            bubble_y1 + 4,
            bubble_x2,
            bubble_y2 + 4,
            self.BUBBLE_RADIUS,
            fill=self.SHADOW_FAR,
            outline="",
        )
        pointer_shadow = canvas.create_polygon(
            pointer_left,
            bubble_y2 + 4,
            pointer_right,
            bubble_y2 + 4,
            pointer_tip_x,
            pointer_tip_y + 4,
            fill=self.SHADOW_FAR,
            outline="",
        )
        bubble_bg = self._draw_round_rect(
            canvas,
            bubble_x1,
            bubble_y1,
            bubble_x2,
            bubble_y2,
            self.BUBBLE_RADIUS,
            fill=self.BUBBLE_FILL,
            outline="",
        )
        bubble_stroke = self._draw_round_rect(
            canvas,
            bubble_x1,
            bubble_y1,
            bubble_x2,
            bubble_y2,
            self.BUBBLE_RADIUS,
            fill="",
            outline=self.BUBBLE_STROKE,
            width=1,
        )
        bubble_pointer = canvas.create_polygon(
            pointer_left,
            bubble_y2,
            pointer_right,
            bubble_y2,
            pointer_tip_x,
            pointer_tip_y,
            fill=self.BUBBLE_FILL,
            outline="",
        )
        bubble_pointer_stroke = canvas.create_polygon(
            pointer_left,
            bubble_y2,
            pointer_right,
            bubble_y2,
            pointer_tip_x,
            pointer_tip_y,
            fill="",
            outline=self.BUBBLE_STROKE,
        )
        bubble_text = canvas.create_text(
            width // 2,
            bubble_y1 + (bubble_h // 2),
            text="Listening...",
            fill=self.TEXT,
            font=("Segoe UI", 14),
            anchor="center",
        )
        bubble_items = (
            bubble_shadow,
            pointer_shadow,
            bubble_bg,
            bubble_stroke,
            bubble_pointer,
            bubble_pointer_stroke,
            bubble_text,
        )

        # Waves
        wave_dim = canvas.create_line(0, 0, 0, 0, smooth=True, splinesteps=30, width=1, fill=self.WAVE_DIM)
        wave_soft = canvas.create_line(0, 0, 0, 0, smooth=True, splinesteps=30, width=1, fill=self.WAVE_SOFT)
        wave_main = canvas.create_line(0, 0, 0, 0, smooth=True, splinesteps=30, width=1, fill=self.WAVE_MAIN)
        processing_dash = canvas.create_line(0, 0, 0, 0, width=1, fill=self.WAVE_SOFT)

        status = {
            "connection": "checking",
            "listening": "ready",
            "processing": "idle",
            "target": "unknown",
            "message": "",
        }
        level = 0.0
        hide_job = None

        def place_window() -> None:
            x = (root.winfo_screenwidth() - width) // 2
            y = root.winfo_screenheight() - height - 76
            root.geometry(f"{width}x{height}+{x}+{y}")

        def _mode() -> str:
            if status.get("listening") == "error" or status.get("processing") == "error":
                return "error"
            if status.get("connection") == "offline":
                return "error"
            if status.get("target") == "not_selected":
                return "warning"
            if status.get("listening") == "arming":
                return "loading"
            if status.get("processing") == "processing":
                return "processing"
            if status.get("listening") == "listening":
                if self._level_filtered >= self.LEVEL_ACTIVE_THRESHOLD:
                    return "listening_audio"
                return "listening_wait"
            if status.get("processing") == "done":
                return "done"
            return "idle"

        def _bubble_label() -> str | None:
            mode = _mode()
            message = str(status.get("message", "")).strip()
            if status.get("target") == "not_selected":
                return "Select a text box"
            if status.get("connection") == "offline":
                return "No connection"
            if mode == "error":
                return "Try again"
            if message:
                return message
            if mode == "loading":
                return "Starting..."
            if mode == "listening_wait":
                return "Listening..."
            return None

        def _wave_coords(mode: str, depth: float, phase_offset: float = 0.0) -> list[float]:
            x_start = bar_x1 + 2
            x_end = bar_x2 - 2
            width_inner = x_end - x_start
            baseline = bar_y2 - 4
            coords: list[float] = []
            for px in range(0, width_inner + 1, 2):
                t = px / width_inner
                x = x_start + px
                if mode == "listening_audio":
                    left_peak = math.exp(-((t - 0.22) / 0.12) ** 2)
                    mid_peak = math.exp(-((t - 0.56) / 0.22) ** 2)
                    right_tail = math.exp(-((t - 0.84) / 0.11) ** 2)
                    profile = (1.15 * left_peak) + (0.68 * mid_peak) + (0.24 * right_tail)
                    shimmer = 1.0 + 0.06 * math.sin(self._phase * 1.4 + t * 8.0 + phase_offset)
                    amp = ((5.0 + 7.5 * depth) * profile + 1.5) * shimmer
                    y = baseline - amp
                elif mode == "loading":
                    arch = math.sin(math.pi * t) ** 0.92
                    pulse = 1.0 + 0.05 * math.sin(self._phase * 0.8 + phase_offset)
                    y = baseline - ((6.3 + 1.0 * depth) * arch * pulse)
                elif mode == "processing":
                    arch = math.sin(math.pi * t) ** 0.92
                    pulse = 1.0 + 0.05 * math.sin(self._phase * 0.6 + phase_offset)
                    y = baseline - ((6.8 + 1.2 * depth) * arch * pulse)
                elif mode == "listening_wait":
                    arch = math.sin(math.pi * t) ** 0.9
                    skew = 0.82 + 0.18 * math.cos((t - 0.5) * math.pi)
                    breathe = 1.0 + 0.05 * math.sin(self._phase * 0.55 + phase_offset)
                    y = baseline - ((6.6 + 2.2 * depth) * arch * skew * breathe)
                elif mode == "done":
                    arch = math.sin(math.pi * t)
                    y = baseline - (6.0 + 0.8 * math.sin(self._phase * 0.45 + phase_offset)) * arch
                elif mode == "warning":
                    arch = math.sin(math.pi * t) ** 0.9
                    y = baseline - (6.2 + 0.8 * math.sin(self._phase * 1.0 + phase_offset)) * arch
                elif mode == "error":
                    arch = math.sin(math.pi * t) ** 0.9
                    y = baseline - (5.8 + 0.6 * math.sin(self._phase * 1.7 + phase_offset)) * arch
                else:
                    arch = math.sin(math.pi * t) ** 0.9
                    y = baseline - (5.8 + 0.6 * math.sin(self._phase * 0.7 + phase_offset)) * arch
                coords.extend((x, y))
            return coords

        def _render_bubble() -> None:
            label = _bubble_label()
            if label:
                for item in bubble_items:
                    canvas.itemconfigure(item, state="normal")
                canvas.itemconfigure(bubble_text, text=label)
            else:
                for item in bubble_items:
                    canvas.itemconfigure(item, state="hidden")

        def _render_waves() -> None:
            mode = _mode()
            if mode == "error":
                main = "#ffd0d7"
                soft = "#f2a9b5"
                dim = "#cc8f99"
            elif mode == "warning":
                main = "#ffe4b3"
                soft = "#f4cf8f"
                dim = "#dcb874"
            elif mode in {"processing", "loading"}:
                main = "#fafafa"
                soft = "#e6e6e6"
                dim = "#cbcbcb"
            else:
                main = self.WAVE_MAIN
                soft = self.WAVE_SOFT
                dim = self.WAVE_DIM

            canvas.itemconfigure(wave_main, fill=main)
            canvas.itemconfigure(wave_soft, fill=soft)
            canvas.itemconfigure(wave_dim, fill=dim)

            depth = max(0.0, min(1.0, level))
            if mode == "listening_audio":
                depth = max(0.35, depth)
            else:
                depth *= 0.45

            canvas.coords(wave_dim, *_wave_coords(mode, depth * 0.55, 0.85))
            canvas.coords(wave_soft, *_wave_coords(mode, depth * 0.78, 0.45))
            canvas.coords(wave_main, *_wave_coords(mode, depth * 1.00, 0.10))

            if mode in {"processing", "loading"}:
                dash_y = bar_y1 + 17 + 0.25 * math.sin(self._phase * 0.7)
                dash_x1 = (bar_x1 + bar_x2) / 2 - 7
                dash_x2 = dash_x1 + 14
                canvas.coords(processing_dash, dash_x1, dash_y, dash_x2, dash_y)
                canvas.itemconfigure(processing_dash, state="normal")
            else:
                canvas.itemconfigure(processing_dash, state="hidden")

        def _animate() -> None:
            self._phase += 0.24
            self._level_filtered = (self._level_filtered * 0.83) + (max(0.0, min(1.0, level)) * 0.17)
            _render_bubble()
            _render_waves()
            if root.winfo_viewable():
                place_window()
            root.after(33, _animate)

        def show() -> None:
            place_window()
            root.deiconify()
            root.lift()

        def hide() -> None:
            root.withdraw()

        def process_queue() -> None:
            nonlocal hide_job, level
            try:
                while True:
                    command, payload = self._queue.get_nowait()
                    if command == "update":
                        payload = dict(payload)
                        if "level" in payload:
                            try:
                                level = float(payload.pop("level"))
                            except Exception:
                                pass
                        status.update(payload)
                        _render_bubble()
                        _render_waves()
                    elif command == "show":
                        if hide_job is not None:
                            root.after_cancel(hide_job)
                            hide_job = None
                        show()
                    elif command == "hide":
                        if hide_job is not None:
                            root.after_cancel(hide_job)
                            hide_job = None
                        hide()
                    elif command == "hide_later":
                        if hide_job is not None:
                            root.after_cancel(hide_job)
                        hide_job = root.after(max(1, int(payload)), hide)
                    elif command == "stop":
                        if hide_job is not None:
                            root.after_cancel(hide_job)
                        root.destroy()
                        return
            except queue.Empty:
                pass
            root.after(60, process_queue)

        _render_bubble()
        _render_waves()
        self._ready.set()
        root.after(33, _animate)
        root.after(60, process_queue)
        root.mainloop()


# ---------------------------------------------------------------------------
# Windows registry helpers (startup)
# ---------------------------------------------------------------------------

def _get_winreg():
    """Return winreg module or None on non-Windows."""
    try:
        import winreg
        return winreg
    except ImportError:
        return None


def set_startup(enable: bool) -> None:
    """Add/remove VoiceKey from Windows startup registry key."""
    winreg = _get_winreg()
    if winreg is None:
        return
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    exe = sys.executable if not getattr(sys, "frozen", False) else sys.executable
    script = os.path.abspath(__file__) if not getattr(sys, "frozen", False) else ""
    value = f'"{exe}" "{script}"' if script else f'"{exe}"'
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
        ) as reg_key:
            if enable:
                winreg.SetValueEx(reg_key, APP_NAME, 0, winreg.REG_SZ, value)
            else:
                try:
                    winreg.DeleteValue(reg_key, APP_NAME)
                except FileNotFoundError:
                    pass
    except Exception:
        pass


def is_startup_enabled() -> bool:
    """Return True if VoiceKey is in the Windows startup registry."""
    winreg = _get_winreg()
    if winreg is None:
        return False
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as reg_key:
            winreg.QueryValueEx(reg_key, APP_NAME)
            return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Icon generation
# ---------------------------------------------------------------------------

def make_icon(state: str = "idle") -> Image.Image:
    """Generate a 64×64 RGBA PIL image: coloured circle with 'V'."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = ICON_COLORS.get(state, ICON_COLORS["idle"])
    # Draw filled circle
    margin = 4
    draw.ellipse([margin, margin, size - margin, size - margin], fill=color)
    # Draw "V" letter in white
    font = None
    try:
        # Try to load a reasonable font; fall back to default
        font = ImageFont.truetype("arial.ttf", 36)
    except Exception:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
    text = "V"
    if font:
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except AttributeError:
            # Older Pillow
            tw, th = draw.textsize(text, font=font)  # type: ignore[attr-defined]
        tx = (size - tw) // 2
        ty = (size - th) // 2 - 2
        draw.text((tx, ty), text, fill=(255, 255, 255, 255), font=font)
    return img


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def record_to_wav(audio_frames: list, sample_rate: int) -> bytes:
    """Convert a list of numpy int16 chunks into WAV bytes (in-memory)."""
    if not audio_frames:
        return b""
    pcm = np.concatenate(audio_frames, axis=0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16 = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def audio_duration_seconds(audio_frames: list, sample_rate: int) -> float:
    """Return duration of buffered PCM frames in seconds."""
    if not audio_frames or sample_rate <= 0:
        return 0.0
    total_samples = sum(int(frame.shape[0]) for frame in audio_frames)
    return total_samples / float(sample_rate)


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def transcribe(wav_bytes: bytes, cfg: dict) -> str:
    """POST WAV audio to Voxtral API, return transcribed text."""
    headers = {"Authorization": f"Bearer {get_effective_api_key(cfg)}"}
    data = {"model": cfg["model"]}
    if cfg.get("language") and cfg["language"] != "auto":
        data["language"] = cfg["language"]
    files = {"file": ("audio.wav", io.BytesIO(wav_bytes), "audio/wav")}
    resp = requests.post(
        cfg["endpoint"],
        headers=headers,
        data=data,
        files=files,
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    return result.get("text", "").strip()


# ---------------------------------------------------------------------------
# Text output
# ---------------------------------------------------------------------------

def type_text(text: str, paste_mode: bool) -> None:
    """Type or paste text at the current cursor position."""
    if not text:
        return
    if paste_mode:
        pyperclip.copy(text)
        # Small delay so clipboard is ready
        time.sleep(0.05)
        kb.send("ctrl+v")
    else:
        kb.write(text, delay=0.005)


# ---------------------------------------------------------------------------
# Settings window (tkinter, dark theme)
# ---------------------------------------------------------------------------

class SettingsWindow:
    """Dark-themed tkinter settings dialog."""

    BG = "#1e1e1e"
    FG = "#d4d4d4"
    ENTRY_BG = "#2d2d2d"
    ACCENT = "#0e639c"
    BTN_BG = "#3a3a3a"

    def __init__(self, app: "VoiceKeyApp"):
        self.app = app
        self._win: tk.Tk | None = None

    def open(self) -> None:
        if self._win is not None:
            try:
                self._win.lift()
                self._win.focus_force()
                return
            except tk.TclError:
                self._win = None

        cfg = self.app.cfg

        win = tk.Tk()
        self._win = win
        win.title(f"{APP_NAME} Settings")
        win.resizable(False, False)
        win.configure(bg=self.BG)
        win.protocol("WM_DELETE_WINDOW", self._on_close)

        pad = {"padx": 12, "pady": 6}

        def label(text, row):
            tk.Label(win, text=text, bg=self.BG, fg=self.FG, anchor="w").grid(
                row=row, column=0, sticky="w", **pad
            )

        def entry(row, show=None):
            e = tk.Entry(win, bg=self.ENTRY_BG, fg=self.FG, insertbackground=self.FG,
                         relief="flat", width=42, show=show or "")
            e.grid(row=row, column=1, sticky="ew", **pad)
            return e

        def combo(values, row):
            var = tk.StringVar(win)
            c = ttk.Combobox(win, textvariable=var, values=values, state="readonly",
                             width=40)
            c.grid(row=row, column=1, sticky="ew", **pad)
            return var, c

        # Style combobox to match dark theme (best-effort)
        style = ttk.Style(win)
        style.theme_use("clam")
        style.configure("TCombobox",
                        fieldbackground=self.ENTRY_BG,
                        background=self.BTN_BG,
                        foreground=self.FG,
                        selectbackground=self.ACCENT,
                        selectforeground=self.FG)
        style.map("TCombobox",
                  fieldbackground=[("readonly", self.ENTRY_BG)],
                  foreground=[("readonly", self.FG)],
                  selectbackground=[("readonly", self.ACCENT)],
                  selectforeground=[("readonly", self.FG)])

        row = 0

        # API Key
        label("API Key:", row)
        e_apikey = entry(row, show="•")
        e_apikey.insert(0, cfg.get("api_key", ""))
        row += 1

        # Endpoint
        label("Endpoint:", row)
        e_endpoint = entry(row)
        e_endpoint.insert(0, cfg.get("endpoint", DEFAULT_CONFIG["endpoint"]))
        row += 1

        # Model
        label("Model:", row)
        e_model = entry(row)
        e_model.insert(0, cfg.get("model", DEFAULT_CONFIG["model"]))
        row += 1

        # Hotkey
        label("Hotkey:", row)
        v_hotkey, c_hotkey = combo(HOTKEY_LIST, row)
        hotkey_value = sanitize_hotkey(cfg.get("hotkey", DEFAULT_CONFIG["hotkey"]))
        v_hotkey.set(hotkey_value)
        c_hotkey.current(HOTKEY_LIST.index(hotkey_value))
        row += 1

        # Language
        label("Language:", row)
        v_lang, c_lang = combo(LANGUAGE_LIST, row)
        language_value = sanitize_language(cfg.get("language", DEFAULT_CONFIG["language"]))
        v_lang.set(language_value)
        c_lang.current(LANGUAGE_LIST.index(language_value))
        row += 1

        # Paste mode
        v_paste = tk.BooleanVar(win, value=cfg.get("paste_mode", True))
        cb_paste = tk.Checkbutton(win, text="Paste mode (faster)",
                                  variable=v_paste,
                                  bg=self.BG, fg=self.FG,
                                  selectcolor=self.ENTRY_BG,
                                  activebackground=self.BG, activeforeground=self.FG)
        cb_paste.grid(row=row, column=1, sticky="w", **pad)
        row += 1

        # Start with Windows
        v_startup = tk.BooleanVar(win, value=is_startup_enabled())
        cb_startup = tk.Checkbutton(win, text="Start with Windows",
                                    variable=v_startup,
                                    bg=self.BG, fg=self.FG,
                                    selectcolor=self.ENTRY_BG,
                                    activebackground=self.BG, activeforeground=self.FG)
        cb_startup.grid(row=row, column=1, sticky="w", **pad)
        row += 1

        # Buttons
        btn_frame = tk.Frame(win, bg=self.BG)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=12)

        def save():
            new_cfg = dict(cfg)
            new_cfg["api_key"] = e_apikey.get().strip()
            new_cfg["endpoint"] = e_endpoint.get().strip()
            new_cfg["model"] = e_model.get().strip()
            new_cfg["hotkey"] = sanitize_hotkey(v_hotkey.get() or cfg.get("hotkey"))
            new_cfg["language"] = sanitize_language(v_lang.get() or cfg.get("language"))
            new_cfg["paste_mode"] = v_paste.get()
            save_config(new_cfg)
            self.app.cfg = new_cfg
            set_startup(v_startup.get())
            # Restart hotkey listener with new hotkey
            self.app.restart_listener()
            self.app.restart_audio_stream()
            self.app.refresh_connection_status()
            self._on_close()

        tk.Button(btn_frame, text="Save", command=save,
                  bg=self.ACCENT, fg="white", relief="flat",
                  padx=18, pady=4).pack(side="left", padx=6)

        tk.Button(btn_frame, text="Cancel", command=self._on_close,
                  bg=self.BTN_BG, fg=self.FG, relief="flat",
                  padx=18, pady=4).pack(side="left", padx=6)

        win.columnconfigure(1, weight=1)
        win.eval("tk::PlaceWindow . center")
        win.mainloop()
        self._win = None

    def _on_close(self):
        if self._win:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None


# ---------------------------------------------------------------------------
# Core application
# ---------------------------------------------------------------------------

class VoiceKeyApp:
    """Main application: manages tray icon, hotkey listener, recording."""

    def __init__(self):
        self.cfg = load_config()
        # Auto-register for Windows startup on first run (user can disable in Settings)
        if not is_startup_enabled():
            set_startup(True)
        self._state = "idle"
        self._recording = False
        self._down = False          # debounce flag for key-repeat
        self._audio_lock = threading.Lock()
        self._audio_frames: list = []
        self._stream: sd.InputStream | None = None
        self._tray: pystray.Icon | None = None
        self._listener: pynput_keyboard.Listener | None = None
        self._settings = SettingsWindow(self)
        self._lock = threading.Lock()
        self._overlay = StatusOverlay()
        self._connection_stop = threading.Event()
        self._connection_kick = threading.Event()
        self._connection_thread: threading.Thread | None = None
        self._connection_state = "checking"
        self._last_level_push = 0.0
        self._level_smoothed = 0.0
        self._record_started_at = 0.0
        self._heard_audio_in_session = False
        self._no_audio_message_shown = False
        self._listening_armed = False
        self._last_ready_chime_at = 0.0
        self._tauri_overlay_exe = find_tauri_overlay_exe()
        self._tauri_overlay_process: subprocess.Popen | None = None
        self._tauri_overlay_started_by_app = False

    # ------------------------------------------------------------------
    # State / icon management
    # ------------------------------------------------------------------

    def _set_state(self, state: str) -> None:
        """Update internal state and refresh tray icon + tooltip."""
        self._state = state
        if DEBUG_OVERLAY_STATES:
            overlay_debug(f"app-state {state}")
        labels = {
            "idle":       f"{APP_NAME} — Idle",
            "recording":  f"{APP_NAME} — Recording...",
            "processing": f"{APP_NAME} — Processing...",
        }
        tooltip = labels.get(state, APP_NAME)
        if self._tray:
            self._tray.icon = make_icon(state)
            self._tray.title = tooltip
        if state == "processing":
            self._overlay.update(listening="ready", processing="processing", message="Processing...")
        else:
            self._overlay.update(listening="ready", message="")
        if state in {"recording", "processing"}:
            self._overlay.show()
        else:
            self._overlay.hide()

    def _target_status(self) -> str:
        """Return overlay token for focused text-target state."""
        target = is_text_input_selected()
        if target is True:
            return "selected"
        if target is False:
            return "not_selected"
        return "unknown"

    def _start_connection_monitor(self) -> None:
        """Start background endpoint reachability checks."""
        if self._connection_thread and self._connection_thread.is_alive():
            return
        self._connection_stop.clear()
        self._connection_kick.clear()
        self._connection_thread = threading.Thread(target=self._connection_loop, daemon=True)
        self._connection_thread.start()

    def _connection_loop(self) -> None:
        while not self._connection_stop.is_set():
            endpoint = self.cfg.get("endpoint", DEFAULT_CONFIG["endpoint"])
            online = endpoint_reachable(endpoint)
            self._connection_state = "online" if online else "offline"
            self._overlay.update(connection=self._connection_state)
            self._connection_kick.clear()
            self._connection_kick.wait(CONNECTION_CHECK_INTERVAL)

    def refresh_connection_status(self) -> None:
        """Request an immediate connection re-check."""
        self._connection_kick.set()

    # ------------------------------------------------------------------
    # Hotkey listener
    # ------------------------------------------------------------------

    def _resolve_pynput_keys(self, hotkey_name: str) -> tuple:
        """Return one or more pynput Key objects for the given hotkey name."""
        attrs = PYNPUT_KEY_MAP.get(str(hotkey_name or "").lower(), ())
        if isinstance(attrs, str):
            attrs = (attrs,)
        resolved = []
        for attr in attrs:
            key_obj = getattr(pynput_keyboard.Key, attr, None)
            if key_obj is not None:
                resolved.append(key_obj)
        return tuple(resolved)

    def _on_press(self, key) -> None:
        """Called by pynput on any key press."""
        if self._down:
            return  # debounce repeated key-down events
        targets = self._resolve_pynput_keys(self.cfg.get("hotkey", "right alt"))
        if not targets:
            return
        if key in targets:
            self._down = True
            self._start_recording()

    def _on_release(self, key) -> None:
        """Called by pynput on any key release."""
        targets = self._resolve_pynput_keys(self.cfg.get("hotkey", "right alt"))
        if not targets:
            return
        if key in targets and self._down:
            self._down = False
            self._stop_recording()

    def start_listener(self) -> None:
        """Start the pynput keyboard listener in a daemon thread."""
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
        self._listener = pynput_keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()

    def restart_listener(self) -> None:
        """Stop and restart the listener (called after hotkey config change)."""
        self._down = False
        self.start_listener()

    def _ensure_audio_stream(self) -> bool:
        """Open the microphone stream for active recording."""
        if self._stream is not None:
            return True
        try:
            sr = int(self.cfg.get("sample_rate", DEFAULT_CONFIG["sample_rate"]))
            stream = sd.InputStream(
                samplerate=sr,
                channels=1,
                dtype="int16",
                callback=self._audio_callback,
                latency="low",
            )
            stream.start()
            self._stream = stream
            return True
        except Exception as exc:
            self._stream = None
            self._overlay.update(listening="error", processing="error", message="")
            self._overlay.hide_later(2800)
            self._notify_error(f"Microphone error: {exc}")
            return False

    def _stop_audio_stream(self) -> None:
        """Stop and close the microphone stream."""
        stream = self._stream
        self._stream = None
        if stream:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def restart_audio_stream(self) -> None:
        """Apply audio setting changes by closing any active stream."""
        self._stop_audio_stream()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _audio_callback(self, indata: np.ndarray, frames: int,
                        time_info, status) -> None:
        """sounddevice callback while the hotkey-held recording stream is active."""
        chunk = indata.copy()
        with self._audio_lock:
            if self._recording:
                if not self._listening_armed:
                    self._listening_armed = True
                    self._record_started_at = time.monotonic()
                    self._overlay.update(listening="listening", processing="idle", message="Listening...")
                    self._play_ready_chime()
                self._audio_frames.append(chunk)
                rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
                raw_level = max(0.0, min(1.0, rms / AUDIO_LEVEL_NORMALIZATION))
                if raw_level < AUDIO_LEVEL_NOISE_FLOOR:
                    raw_level = 0.0

                # Shape and smooth the signal so motion tracks speech naturally without abrupt jumps.
                target_level = raw_level ** AUDIO_LEVEL_CURVE
                previous_level = self._level_smoothed
                blend = AUDIO_LEVEL_ATTACK if target_level > previous_level else AUDIO_LEVEL_RELEASE
                level = previous_level + (target_level - previous_level) * blend
                self._level_smoothed = max(0.0, min(1.0, level))

                heard_audio = (
                    raw_level > AUDIO_ACTIVITY_THRESHOLD
                    or self._level_smoothed >= AUDIO_ACTIVITY_LEVEL_THRESHOLD
                )

                if (not self._heard_audio_in_session) and heard_audio:
                    self._heard_audio_in_session = True
                    if self._no_audio_message_shown:
                        self._overlay.update(message="Listening...")
                        self._no_audio_message_shown = False
                elif (
                    (not self._heard_audio_in_session)
                    and (not self._no_audio_message_shown)
                    and (self._level_smoothed < AUDIO_ACTIVITY_LEVEL_THRESHOLD)
                    and ((time.monotonic() - self._record_started_at) >= NO_AUDIO_MESSAGE_DELAY_SECONDS)
                ):
                    self._overlay.update(message="No audio detected")
                    self._no_audio_message_shown = True

                now = time.monotonic()
                if now - self._last_level_push >= AUDIO_LEVEL_PUSH_INTERVAL_SECONDS:
                    self._overlay.update(level=self._level_smoothed)
                    self._last_level_push = now

    def _start_recording(self) -> None:
        with self._lock:
            if self._recording:
                return
            self._recording = True
            self._last_level_push = 0.0
            self._level_smoothed = 0.0
            self._record_started_at = time.monotonic()
            self._heard_audio_in_session = False
            self._no_audio_message_shown = False
            self._listening_armed = False
            with self._audio_lock:
                self._audio_frames = []
        self._set_state("recording")
        self._overlay.update(
            connection=self._connection_state,
            listening="arming",
            processing="idle",
            target=self._target_status(),
            level=0.0,
            message="Starting...",
        )
        with self._lock:
            if not self._ensure_audio_stream():
                self._recording = False
                self._set_state("idle")
                return

    def _stop_recording(self) -> None:
        with self._lock:
            if not self._recording:
                return
            self._recording = False

        with self._audio_lock:
            frames = list(self._audio_frames)
            heard_audio = self._heard_audio_in_session
            self._audio_frames = []
        self._stop_audio_stream()

        # Run transcription in a background daemon thread
        t = threading.Thread(
            target=self._transcribe_and_type,
            args=(frames, heard_audio),
            daemon=True,
        )
        t.start()

    # ------------------------------------------------------------------
    # Transcription & typing
    # ------------------------------------------------------------------

    def _transcribe_and_type(self, frames: list, heard_audio: bool) -> None:
        """Background: convert frames to WAV, transcribe, then type text."""
        self._set_state("processing")
        self._overlay.update(connection=self._connection_state, target=self._target_status(), level=0.0)
        try:
            sr = int(self.cfg.get("sample_rate", 16000))
            duration = audio_duration_seconds(frames, sr)
            wav_bytes = record_to_wav(frames, sr)

            if len(wav_bytes) < MIN_AUDIO_BYTES:
                self._overlay.update(processing="done")
                self._set_state("idle")
                return
            if (not heard_audio) and (duration < MIN_AUDIO_SECONDS_WITHOUT_ACTIVITY):
                self._overlay.update(processing="done")
                self._set_state("idle")
                return

            if not get_effective_api_key(self.cfg):
                self._notify_error(
                    "No API key set. Open Settings from the tray icon and save your API key."
                )
                self._overlay.update(processing="error")
                self._set_state("idle")
                return

            text = transcribe(wav_bytes, self.cfg)
            if text:
                time.sleep(0.1)
                target = self._target_status()
                self._overlay.update(target=target)
                if target == "not_selected":
                    self._notify_error("No text box selected. Click a text field and try again.")
                type_text(text, self.cfg.get("paste_mode", True))
            self._overlay.update(processing="done", target=self._target_status())
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else "?"
            self._overlay.update(processing="error")
            self._notify_error(f"API error {code}: {exc.response.text[:120] if exc.response else exc}")
        except requests.ConnectionError:
            self._connection_state = "offline"
            self._overlay.update(connection="offline", processing="error")
            self._notify_error("Network error - check internet connection.")
            self.refresh_connection_status()
        except Exception as exc:
            self._overlay.update(processing="error")
            self._notify_error(f"Transcription failed: {exc}")
        finally:
            self._set_state("idle")

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def _play_ready_chime(self) -> None:
        """Play a short chime when the microphone stream is ready."""
        if os.name != "nt" or winsound is None:
            return
        now = time.monotonic()
        if now - self._last_ready_chime_at < READY_CHIME_COOLDOWN_SECONDS:
            return
        self._last_ready_chime_at = now
        try:
            winsound.PlaySound(
                READY_CHIME_ALIAS,
                winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
        except Exception:
            try:
                winsound.MessageBeep()
            except Exception:
                pass

    def _notify_error(self, message: str) -> None:
        """Show a tray notification."""
        if self._tray:
            try:
                self._tray.notify(message, title=f"{APP_NAME} — Error")
            except Exception:
                pass  # notify not supported on all platforms

    # ------------------------------------------------------------------
    # Tray menu callbacks
    # ------------------------------------------------------------------

    def _start_tauri_overlay(self) -> None:
        if not self._tauri_overlay_exe:
            return
        if is_tauri_overlay_process_running():
            return
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._tauri_overlay_process = subprocess.Popen(
                [self._tauri_overlay_exe],
                cwd=os.path.dirname(self._tauri_overlay_exe),
                creationflags=flags,
            )
            self._tauri_overlay_started_by_app = True
        except Exception as exc:
            overlay_debug(f"overlay launch failed: {exc}")

    def _stop_tauri_overlay(self) -> None:
        proc = self._tauri_overlay_process
        if not proc or not self._tauri_overlay_started_by_app:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        finally:
            self._tauri_overlay_process = None
            self._tauri_overlay_started_by_app = False

    def _open_settings(self, icon=None, item=None) -> None:
        t = threading.Thread(target=self._settings.open, daemon=True)
        t.start()

    def _quit(self, icon=None, item=None) -> None:
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
        self._stop_audio_stream()
        self._connection_stop.set()
        self._overlay.stop()
        self._stop_tauri_overlay()
        if self._tray:
            self._tray.stop()
        os._exit(0)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Build tray icon and start the application."""
        self._start_tauri_overlay()
        self._overlay.start()
        self._overlay.update(
            connection="checking",
            listening="ready",
            processing="idle",
            target=self._target_status(),
            message=f"Hold {sanitize_hotkey(self.cfg.get('hotkey', DEFAULT_CONFIG['hotkey']))} to talk",
        )
        self._overlay.hide()
        self._start_connection_monitor()
        self.refresh_connection_status()

        # Prompt for API key on first run
        if not get_effective_api_key(self.cfg):
            threading.Thread(target=self._first_run_prompt, daemon=True).start()

        self.start_listener()

        icon_image = make_icon("idle")
        menu = Menu(
            MenuItem("Settings", self._open_settings),
            Menu.SEPARATOR,
            MenuItem("Quit", self._quit),
        )
        self._tray = pystray.Icon(
            APP_NAME,
            icon=icon_image,
            title=f"{APP_NAME} — Idle",
            menu=menu,
        )
        self._tray.run()

    def _first_run_prompt(self) -> None:
        """Show a reminder to configure the API key."""
        time.sleep(2)
        self._notify_error(
            "Welcome! Open Settings from the tray icon and save your API key."
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = VoiceKeyApp()
    app.run()

