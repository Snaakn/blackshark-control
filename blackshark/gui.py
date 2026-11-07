"""BlackShark Control: main window, tray quick panel and "looking for headset" state."""

import array
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

try:
    gi.require_version("XApp", "1.0")
    from gi.repository import XApp  # noqa: E402
except (ValueError, ImportError):
    XApp = None
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
except (ValueError, ImportError):
    AppIndicator = None
try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell as LayerShell  # noqa: E402
except (ValueError, ImportError):
    LayerShell = None

from . import audio  # noqa: E402
from . import protocol as P  # noqa: E402
from .tray import StatusNotifierItem  # noqa: E402
from .widgets import BatteryRing, EqCurve, ModeWave, Pulse, Waveform, icon  # noqa: E402

CONFIG = os.path.expanduser("~/.config/blackshark-control/settings.json")
ACCENTS = [("Green", "#35a854"), ("Blue", "#1f9ede"), ("Pink", "#e54980"), ("Orange", "#ff7139")]
LOW_BATTERY = 20

MODES = [
    ("off", "Off", "", "Raw microphone signal, no processing.",
     "M0,20 L5,13 L9,27 L14,8 L19,31 L24,12 L29,25 L33,5 L39,33 L44,14 L49,26 L54,9 L59,29 "
     "L65,15 L71,27 L77,11 L83,24 L89,16 L95,25 L100,19"),
    ("BlackShark Natural Mic", "Natural", "", "Removes low rumble and evens out your level. No noise removal.",
     "M0,20 C8,12 14,12 20,19 S30,28 36,21 S46,9 54,18 S64,30 72,21 S84,11 90,19 S97,25 100,20"),
    ("BlackShark Clear Mic", "Clear", "RECOMMENDED",
     "AI noise removal, de-essing and a broadcast-style compressor.",
     "M0,20 L24,20 C30,20 32,5 40,5 S50,35 58,35 S68,9 74,20 L100,20"),
]
MODE_LABEL = {m[0]: m[1] for m in MODES}

MAIN_PRESETS = [("game", "Game"), ("music", "Music"), ("movie", "Movie"), ("custom", "Custom")]
ESPORTS = [(f"esports{i}", f"Esports {i}") for i in range(1, 6)]
PRESET_LABEL = dict(MAIN_PRESETS + ESPORTS)
BUILTIN = ("game", "music", "movie")

IDLE_STEPS = [(0, "Never"), (15, "15 minutes"), (30, "30 minutes"), (45, "45 minutes"), (60, "1 hour")]
PROFILES = [("output:analog-stereo+input:mono-fallback", "Analog + mic"),
            ("output:iec958-stereo+input:mono-fallback", "Digital + mic")]


def load_settings():
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(s):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w") as f:
        json.dump(s, f)


def shade(hex_color, f):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#{:02x}{:02x}{:02x}".format(*(min(255, round(c * f)) for c in (r, g, b)))


def is_kde():
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP", "") + ":" +
               os.environ.get("XDG_SESSION_DESKTOP", "")).lower()
    return ("kde" in desktop or "plasma" in desktop or
            bool(os.environ.get("KDE_FULL_SESSION")) or
            bool(os.environ.get("KDE_SESSION_VERSION")))


def is_wayland():
    display = Gdk.Display.get_default()
    if display is not None:
        return "wayland" in type(display).__name__.lower()
    return bool(os.environ.get("WAYLAND_DISPLAY") or
                os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland")


PLASMA_APPLETSRC = os.path.expanduser("~/.config/plasma-org.kde.plasma.desktop-appletsrc")
PANEL_LOCATIONS = {3: Gtk.PositionType.TOP, 4: Gtk.PositionType.BOTTOM,
                   5: Gtk.PositionType.LEFT, 6: Gtk.PositionType.RIGHT}


def plasma_panel():
    """Best-effort location and thickness of the Plasma panel, for popup placement."""
    try:
        with open(PLASMA_APPLETSRC) as f:
            lines = f.readlines()
    except OSError:
        return Gtk.PositionType.BOTTOM, 44
    panels = []
    current = None
    for line in lines:
        line = line.strip()
        if re.match(r"^\[Containments\]\[\d+\]$", line):
            current = {}
            panels.append(current)
        elif line.startswith("["):
            current = None
        elif current is not None and "=" in line:
            key, value = line.split("=", 1)
            current[key] = value
    for p in panels:
        loc = p.get("location", "")
        if p.get("plugin") != "org.kde.panel" or not loc.isdigit():
            continue
        if int(loc) not in PANEL_LOCATIONS:
            continue
        try:
            thickness = int(p.get("thickness", "44"))
        except ValueError:
            thickness = 44
        return PANEL_LOCATIONS[int(loc)], thickness
    return Gtk.PositionType.BOTTOM, 44


CSS = """
@define-color accent ACCENT;
@define-color accentfill FILL;

.bs, .bs label { font-family: Ubuntu, "Noto Sans", sans-serif; color: #f2f2f4; }
window.bs, .bs-body { background-color: #222226; }
headerbar.bs-head { background-color: #222226; background-image: none; border: none; box-shadow: none;
    min-height: 48px; padding: 0 6px 0 18px; }
.bs-head .apptitle { font-weight: 700; color: #c9c9ce; font-size: 13px; letter-spacing: .02em; }

.card { background-color: #2a2a2f; border-radius: 18px; padding: 16px 18px; }
.hero { padding: 16px 18px;
    background-image: radial-gradient(circle farthest-corner at 50% 36%, alpha(@accent, 0.30) 0%, alpha(@accent, 0) 58%); }
.h1 { font-size: 17px; font-weight: 700; }
.h2 { font-size: 16px; font-weight: 700; }
.big52 { font-size: 38px; font-weight: 700; letter-spacing: -1px; font-feature-settings: "tnum"; }
.bs label.big52.low { color: #ff8a5c; }
.big34 { font-size: 28px; font-weight: 700; font-feature-settings: "tnum"; }
.big22 { font-size: 18px; font-weight: 700; font-feature-settings: "tnum"; }
.big26 { font-size: 26px; font-weight: 700; font-feature-settings: "tnum"; }
.medium { font-weight: 500; }
.bold { font-weight: 700; }
.bs label.dim { color: #a9a9b0; }
.bs label.dim2 { color: #c9c9ce; }
.small { font-size: 12.5px; }
.chip { background-color: rgba(255,255,255,.08); border-radius: 999px; padding: 0 11px; min-height: 26px; font-size: 12.5px; }
.dot { min-width: 8px; min-height: 8px; border-radius: 50%; background-color: @accent; }
.dot.muted { background-color: #ff7139; }
.wavebox, .builtin { background-color: rgba(0,0,0,.22); border-radius: 14px; }
.divider { border-top: 1px solid rgba(255,255,255,.06); padding-top: 10px; }

.bs button { background-image: none; background-color: transparent; border: none; box-shadow: none;
    text-shadow: none; -gtk-icon-shadow: none; color: #f2f2f4; padding: 0; outline-color: rgba(255,255,255,.8); }
.bs button label { color: #f2f2f4; }
.bs button.round { border-radius: 8px; min-width: 40px; min-height: 40px; }
.bs button.round:hover, .bs button.circle:hover { background-color: rgba(255,255,255,.1); }
.bs button.circle { border-radius: 50%; min-width: 40px; min-height: 40px; background-color: rgba(255,255,255,.06); }
.bs button.pill { border-radius: 999px; min-height: 30px; padding: 0 14px; background-color: rgba(255,255,255,.08); }
.bs button.pill:hover { background-color: rgba(255,255,255,.15); }
.bs button.pill.on { background-color: @accentfill; }
.bs button.pill.on label { font-weight: 700; }
.bs button.pill.outline { background-color: transparent; border: 1px solid rgba(255,255,255,.18); }
.bs button.pill.outline:hover { background-color: rgba(255,255,255,.08); }
.bs button.pill.big { min-height: 40px; padding: 0 20px; }
.bs button.pill.big label { font-weight: 700; }
.bs button.test { min-height: 32px; padding: 0 16px; border-radius: 999px; background-color: rgba(255,255,255,.1); }
.bs button.test label { font-weight: 700; }
.bs button.test:hover { background-color: rgba(255,255,255,.16); }
.bs button.test.on { background-color: @accentfill; }
.bs button.opt { border: 2px solid rgba(255,255,255,.06); border-radius: 14px; background-color: rgba(255,255,255,.03); padding: 10px 14px; }
.bs button.opt:hover { background-color: rgba(255,255,255,.07); }
.bs button.opt.on { border-color: @accent; background-color: mix(@accent, #2a2a2f, 0.86); }
.bs button.opt label.tag { font-size: 11.5px; font-weight: 700; letter-spacing: .5px; color: #a9a9b0; }
.bs button.opt.on label.tag { color: #ffffff; }
.bs button.tile { border-radius: 18px; background-color: #2a2a2f; padding: 14px; min-height: 80px; }
.bs button.tile:hover { background-color: #323238; }
.bs button.tile.on { background-color: @accentfill; }
.bs button.tile.on label.dim { color: rgba(255,255,255,.8); }

.bs scale { padding: 6px 0; min-height: 24px; }
.bs scale trough { min-height: 8px; border-radius: 4px; background-color: rgba(255,255,255,.12);
    background-image: none; border: none; box-shadow: none; }
.bs scale highlight { min-height: 8px; border-radius: 4px; background-color: @accent; background-image: none; border: none; }
.bs scale slider { min-width: 24px; min-height: 24px; margin: -8px; border-radius: 50%; background-color: #ffffff;
    background-image: none; border: none; box-shadow: 0 0 0 4px alpha(@accent, 0.35), 0 2px 6px rgba(0,0,0,.5); }

.bs switch { min-width: 52px; min-height: 30px; border-radius: 15px; background-color: rgba(255,255,255,.18);
    background-image: none; border: none; box-shadow: none; font-size: 0; padding: 0; }
.bs switch:checked { background-color: @accent; }
.bs switch slider { min-width: 24px; min-height: 24px; margin: 3px; border-radius: 50%; background-color: #ffffff;
    background-image: none; border: none; box-shadow: 0 1px 3px rgba(0,0,0,.4); }

.step { background-color: #2a2a2f; border-radius: 14px; padding: 12px 14px; }
.stepnum { background-color: rgba(255,255,255,.1); border-radius: 50%; min-width: 26px; min-height: 26px;
    font-weight: 700; font-size: 13px; }
.empty-title { font-size: 26px; font-weight: 700; }

window.qp-win { background-color: transparent; }
.qp { background-color: #26262b; border-radius: 22px; padding: 18px;
    background-image: radial-gradient(circle farthest-side at 15% 10%, alpha(@accent, 0.28) 0%, alpha(@accent, 0) 65%);
    box-shadow: 0 0 0 1px rgba(255,255,255,.08), 0 12px 30px rgba(0,0,0,.55); margin: 20px; }
.qtile { background-color: rgba(255,255,255,.06); border-radius: 16px; padding: 12px 14px; min-height: 52px; }
.bs button.qtile { background-color: rgba(255,255,255,.06); border-radius: 16px; padding: 12px 14px; }
.bs button.qtile:hover { background-color: rgba(255,255,255,.1); }
.bs button.qtile.on { background-color: @accentfill; }
.seggroup { background-color: rgba(0,0,0,.25); border-radius: 14px; padding: 4px; }
.bs button.seg { border-radius: 10px; min-height: 56px; }
.bs button.seg:hover { background-color: rgba(255,255,255,.06); }
.bs button.seg.on { background-color: mix(@accent, #3a3a40, 0.7); }
.bs button.seg.on label { font-weight: 700; }
.sectlabel { font-size: 12.5px; font-weight: 700; }
.bs label.sectlabel { color: #a9a9b0; }
"""


# ---------------------------------------------------------------------------
# Device + audio plumbing
# ---------------------------------------------------------------------------

class Worker(threading.Thread):
    """Owns the HID device; runs jobs off the UI thread and reconnects on loss."""

    def __init__(self, on_link, on_event):
        super().__init__(daemon=True)
        self.jobs = queue.Queue()
        self.on_link = on_link
        self.on_event = on_event
        self.dev = None

    def submit(self, fn, *args, then=None):
        self.jobs.put((fn, args, then))

    def _device(self):
        if self.dev is None:
            self.dev = P.BlackShark()
            self.dev.on_event = lambda cmd, payload: GLib.idle_add(self.on_event, cmd, payload)
        return self.dev

    def run(self):
        while True:
            try:
                fn, args, then = self.jobs.get(timeout=1.0)
            except queue.Empty:
                if self.dev:
                    try:
                        self.dev.poll_events()
                    except OSError:
                        self._drop()
                continue
            try:
                result = fn(self._device(), *args)
                GLib.idle_add(self.on_link, "ok", None)
                if then:
                    GLib.idle_add(then, result)
            except P.HeadsetUnreachable:
                GLib.idle_add(self.on_link, "off", None)
            except P.DeviceNotFound:
                self._drop()
                GLib.idle_add(self.on_link, "nodongle", None)
            except P.DeviceError as e:
                GLib.idle_add(self.on_link, "error", str(e))
            except OSError:
                self._drop()
                GLib.idle_add(self.on_link, "nodongle", None)
            except Exception as e:  # never let the worker die: it is the only path to the headset
                import traceback
                traceback.print_exc()
                GLib.idle_add(self.on_link, "error", f"{type(e).__name__}: {e}")

    def _drop(self):
        if self.dev:
            try:
                self.dev.close()
            except OSError:
                pass
        self.dev = None


class Latest:
    """Runs only the most recent job per key on a background thread (for sliders)."""

    def __init__(self):
        self.pending = {}
        self.cv = threading.Condition()
        threading.Thread(target=self._run, daemon=True).start()

    def put(self, key, fn):
        with self.cv:
            self.pending[key] = fn
            self.cv.notify()

    def _run(self):
        while True:
            with self.cv:
                while not self.pending:
                    self.cv.wait()
                key = next(iter(self.pending))
                fn = self.pending.pop(key)
            try:
                fn()
            except Exception as e:  # keep the thread alive
                print("audio job failed:", e, file=sys.stderr)


class MicMeter:
    """Live input level of the default source (the processed mic when enhancement is on)."""

    def __init__(self, on_level):
        self.on_level = on_level
        self.proc = None

    def start(self):
        if self.proc:
            return
        src = audio._run("pactl", "get-default-source").strip() or "@DEFAULT_SOURCE@"
        try:
            self.proc = subprocess.Popen(
                ["parec", "-d", src, "--raw", "--format=s16le", "--rate=16000", "--channels=1",
                 "--latency-msec=30"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            print("parec not found (install pulseaudio-utils): mic test unavailable", file=sys.stderr)
            return False
        return True
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc = None

    def _read(self, proc):
        chunk = 16000 // 25 * 2
        while proc.poll() is None:
            data = proc.stdout.read(chunk)
            if not data:
                break
            a = array.array("h", data)
            peak = max(abs(x) for x in a) / 32768
            rms = math.sqrt(sum(x * x for x in a) / len(a)) / 32768
            db = 20 * math.log10(max(rms, 1e-6))
            level = max(0.0, min(1.0, (db + 60) / 54))
            GLib.idle_add(self.on_level, level, peak > 0.89)


# ---------------------------------------------------------------------------
# Controller: single source of truth shared by the window, quick panel and tray
# ---------------------------------------------------------------------------

class Controller:
    def __init__(self, gapp):
        self.gapp = gapp
        self.settings = load_settings()
        self.accent = self.settings.get("accent", ACCENTS[0][1])
        self.state = {
            "link": "searching", "battery": None, "charging": False, "muted": False,
            "firmware": None, "sidetone": 0, "preset": "custom", "bands": [0] * 10,
            "idle": None, "dnd": False,
            "mode": "off", "gain": 0, "volume": 0, "profile": None,
        }
        self.views = []
        self.worker = Worker(self._on_link, self._on_device_event)
        self.audio_jobs = Latest()
        self.retry_id = None
        self.low_warned = False
        self.user_touch = {}  # key -> monotonic time of the last local change
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), self.css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._load_css()
        self.worker.start()
        threading.Thread(target=self._watch_pulse, daemon=True).start()
        self.refresh_device()
        self.refresh_audio()
        GLib.timeout_add_seconds(20, self._poll)

    # -- views ----------------------------------------------------------------

    def add_view(self, v):
        self.views.append(v)
        v.render(self.state)

    def _render(self):
        for v in self.views:
            v.render(self.state)
        return False

    def update(self, **kw):
        self.state.update(kw)
        self._render()
        return False

    def _touched(self, key, ms):
        t = self.user_touch.get(key)
        return t is not None and (GLib.get_monotonic_time() - t) / 1000 < ms

    def _touch(self, key):
        self.user_touch[key] = GLib.get_monotonic_time()

    # -- theme ----------------------------------------------------------------

    def _load_css(self):
        css = CSS.replace("ACCENT", self.accent).replace("FILL", shade(self.accent, 0.72))
        self.css.load_from_data(css.encode())

    def set_accent(self, hex_color):
        self.accent = hex_color
        self.settings["accent"] = hex_color
        save_settings(self.settings)
        self._load_css()
        self._render()

    # -- device ---------------------------------------------------------------

    def refresh_device(self):
        def read(d):
            s = d.status()
            s["firmware"] = d.firmware()
            return s
        self.worker.submit(read, then=self._got_status)

    def _got_status(self, s):
        self.update(battery=s["battery"], charging=s["charging"], muted=s["mic_muted"],
                    firmware=s["firmware"], sidetone=s["sidetone"],
                    preset=P.PRESET_NAMES.get(s["preset"], "custom"), bands=s["eq"],
                    idle=s["idle_minutes"], dnd=s["dnd"])
        self._battery_notify()
        return False

    def _poll(self):
        if self.state["link"] == "ok":
            self.worker.submit(lambda d: (d.battery(), d.charging(), d.mic_muted()),
                               then=self._got_battery)
        return True

    def _got_battery(self, r):
        self.update(battery=r[0], charging=r[1], muted=r[2])
        self._battery_notify()
        return False

    def _battery_notify(self):
        pct, charging = self.state["battery"], self.state["charging"]
        if pct is None:
            return
        if pct <= LOW_BATTERY and not charging and not self.low_warned:
            self.low_warned = True
            try:
                subprocess.Popen(["notify-send", "-i", "battery-low", "BlackShark V2 Pro",
                                  f"Headset battery low: {pct}%"])
            except FileNotFoundError:
                pass
        if charging or pct > LOW_BATTERY + 5:
            self.low_warned = False

    def _on_link(self, link, err):
        if link == "error":
            print("device error:", err, file=sys.stderr)
            if self.state["battery"] is not None:
                return False  # one command failed; the headset is still connected
            self.state["error"] = err
        was = self.state["link"]
        if link != was:
            if link != "ok":
                self.state["battery"] = None
            self.update(link=link)
            if link == "ok" and self.state["battery"] is None:
                self.refresh_device()
        if link != "ok" and not self.retry_id:
            self.retry_id = GLib.timeout_add_seconds(5, self._retry)
        return False

    def _retry(self):
        self.retry_id = None
        self.refresh_device()
        return False

    def _on_device_event(self, cmd, payload):
        # The EQ button on the headset cycles presets and reports it.
        if cmd == P.CMD_PRESET_GET and payload and not self._touched("preset", 3000):
            name = P.PRESET_NAMES.get(payload[0])
            if name and name != self.state["preset"]:
                self.update(preset=name)
                self.worker.submit(lambda d: d.eq(), then=lambda b: self.update(bands=b))
        return False

    def set_preset(self, name):
        self._touch("preset")
        self.update(preset=name)
        self.worker.submit(lambda d: (d.set_preset(name), d.eq())[1],
                           then=lambda b: self.update(bands=b))

    def set_bands(self, bands):
        self._touch("preset")
        self.update(preset="custom", bands=list(bands))
        self.worker.submit(lambda d: d.set_eq(bands))

    def set_sidetone(self, level):
        # Coalesce slider drags: each write is ~0.3 s of radio traffic.
        self.update(sidetone=level)
        if self._side_timer:
            GLib.source_remove(self._side_timer)

        def send():
            self._side_timer = None
            lvl = self.state["sidetone"]
            self.worker.submit(lambda d: d.set_sidetone(lvl))
            return False
        self._side_timer = GLib.timeout_add(250, send)

    _side_timer = None

    def set_idle(self, minutes):
        self.update(idle=minutes)
        self.worker.submit(lambda d: d.set_idle_minutes(minutes))

    def set_dnd(self, on):
        self.update(dnd=on)
        self.worker.submit(lambda d: d.set_dnd(on))

    # -- audio ----------------------------------------------------------------

    def refresh_audio(self):
        def read():
            st = dict(mode=audio.current_mic_preset(), gain=audio.mic_volume(),
                      volume=audio.output_volume(), profile=audio.active_profile())
            GLib.idle_add(self._got_audio, st)
        threading.Thread(target=read, daemon=True).start()

    def _got_audio(self, st):
        for key in ("gain", "volume", "mode"):
            if self._touched(key, 2500):
                st.pop(key)
        st = {k: v for k, v in st.items() if v is not None or k == "profile"}
        self.update(**st)
        return False

    def _watch_pulse(self):
        """Follow volume/profile changes made elsewhere (media keys, Sound settings)."""
        pending = [False]

        def fire():
            pending[0] = False
            self.refresh_audio()
            threading.Thread(target=audio.sync_easyeffects_input, daemon=True).start()
            return False
        while True:  # pactl exits when PipeWire restarts; reconnect
            try:
                proc = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True)
            except FileNotFoundError:
                print("pactl not found: volume changes made elsewhere won't show", file=sys.stderr)
                return
            for line in proc.stdout:
                if ("'change'" in line or "'new'" in line) and any(
                        k in line for k in (" sink ", " source ", " card ", " server")):
                    if not pending[0]:
                        pending[0] = True
                        GLib.idle_add(lambda: GLib.timeout_add(300, fire) and False)
            proc.wait()
            time.sleep(3)
            GLib.idle_add(lambda: (self.refresh_audio(), False)[1])

    def set_volume(self, pct):
        self._touch("volume")
        self.state["volume"] = pct
        self.audio_jobs.put("volume", lambda: audio.set_output_volume(pct))
        self._render()

    def set_gain(self, pct):
        self._touch("gain")
        self.state["gain"] = pct
        self.audio_jobs.put("gain", lambda: audio.set_mic_volume(pct))
        self._render()

    def set_mode(self, mode):
        self._touch("mode")
        self.update(mode=mode)

        def job():
            try:
                audio.apply_mic_preset(mode)
            except RuntimeError as e:
                print("voice enhancement:", e, file=sys.stderr)
                self.user_touch.pop("mode", None)
                GLib.idle_add(self.refresh_audio)
        self.audio_jobs.put("mode", job)

    def set_profile(self, name):
        self.update(profile=name)

        def job():
            audio.set_profile(name)
            GLib.idle_add(self.refresh_audio)
        self.audio_jobs.put("profile", job)


# ---------------------------------------------------------------------------
# Small builders
# ---------------------------------------------------------------------------

def label(text="", *classes, xalign=0.0, wrap=False):
    lb = Gtk.Label(label=text, xalign=xalign)
    if wrap:
        lb.set_line_wrap(True)
    for c in classes:
        lb.get_style_context().add_class(c)
    return lb


def styled(widget, *classes):
    for c in classes:
        widget.get_style_context().add_class(c)
    return widget


def set_class(widget, cls, on):
    ctx = widget.get_style_context()
    (ctx.add_class if on else ctx.remove_class)(cls)


def vbox(spacing=0, *children):
    b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    for c in children:
        b.pack_start(c, False, False, 0)
    return b


def hbox(spacing=0, *children):
    b = Gtk.Box(spacing=spacing)
    for c in children:
        b.pack_start(c, False, False, 0)
    return b


def make_scale(lo, hi, on_change):
    s = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
    s.set_draw_value(False)
    s.set_hexpand(True)
    s.set_round_digits(0)
    s._guard = False
    s.connect("value-changed", lambda w: None if w._guard else on_change(int(w.get_value())))
    return s


def set_scale(s, v):
    if v is None or int(s.get_value()) == int(v):
        return
    s._guard = True
    s.set_value(v)
    s._guard = False


def ring_color(ctl, pct):
    return "#ff7139" if pct is not None and pct <= LOW_BATTERY else ctl.accent


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, gapp, ctl):
        super().__init__(application=gapp, title="BlackShark Control")
        self.ctl = ctl
        styled(self, "bs")
        self.set_icon_name("audio-headset")
        self.set_default_size(1060, 930)
        self.connect("delete-event", self._on_close)

        hb = styled(Gtk.HeaderBar(), "bs-head")
        hb.set_show_close_button(True)
        hb.set_decoration_layout(":minimize,maximize,close")
        hb.pack_start(label("BlackShark Control", "apptitle"))
        hb.pack_end(self._menu_button())
        self.set_titlebar(hb)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        styled(self.stack, "bs-body")
        self.add(self.stack)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.add(self._build_main())
        self.stack.add_named(scroll, "main")
        self.stack.add_named(self._build_empty(), "empty")
        self.stack.show_all()
        hb.show_all()

    # -- header menu --------------------------------------------------------------

    def _menu_button(self):
        mb = styled(Gtk.MenuButton(), "round")
        mb.set_tooltip_text("Menu")
        mb.add(icon("menu", 16))
        menu = Gtk.Menu()
        group = None
        for name, hexc in ACCENTS:
            it = Gtk.RadioMenuItem.new_with_label_from_widget(group, f"Accent: {name}")
            group = group or it
            it.set_active(hexc == self.ctl.accent)
            it.connect("toggled", lambda w, h=hexc: w.get_active() and self.ctl.set_accent(h))
            menu.append(it)
        menu.append(Gtk.SeparatorMenuItem())
        refresh = Gtk.MenuItem(label="Re-read headset")
        refresh.connect("activate", lambda *_: self.ctl.refresh_device())
        menu.append(refresh)
        quit_ = Gtk.MenuItem(label="Quit")
        quit_.connect("activate", lambda *_: self.ctl.gapp.quit())
        menu.append(quit_)
        menu.show_all()
        mb.set_popup(menu)
        return mb

    def _on_close(self, *_):
        self.ctl.gapp.window_closed()
        self.hide()
        return True

    # -- main content ---------------------------------------------------------------

    def _build_main(self):
        grid = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
        grid.set_margin_start(16)
        grid.set_margin_end(16)
        grid.set_margin_top(4)
        grid.set_margin_bottom(16)
        grid.attach(self._device_card(), 0, 0, 1, 1)
        grid.attach(self._mic_card(), 1, 0, 1, 1)
        grid.attach(self._voice_card(), 0, 1, 2, 1)
        grid.attach(self._eq_card(), 0, 2, 2, 1)
        grid.attach(self._volume_card(), 0, 3, 1, 1)
        grid.attach(self._tiles(), 1, 3, 1, 1)
        return grid

    def _card(self, *classes, spacing=16):
        return styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing), "card", *classes)

    def _device_card(self):
        c = self._card("hero", spacing=6)
        self.ring = BatteryRing(140, 10, 60, 1.3)
        self.ring.set_halign(Gtk.Align.CENTER)
        c.pack_start(self.ring, False, False, 0)
        self.batt = label("–", "big52", xalign=0.5)
        c.pack_start(self.batt, False, False, 0)
        name = label("BlackShark V2 Pro", "h1", xalign=0.5)
        c.pack_start(name, False, False, 0)
        self.sub = label("", "dim", "small", xalign=0.5)
        c.pack_start(self.sub, False, False, 0)
        chips = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        chips.set_margin_top(4)
        self.mic_dot = styled(Gtk.Box(valign=Gtk.Align.CENTER), "dot")
        self.mic_chip_label = label("Mic live")
        chip1 = styled(hbox(7, self.mic_dot, self.mic_chip_label), "chip")
        self.voice_chip = styled(label("", xalign=0.5), "chip")
        self.eq_chip = styled(label("", xalign=0.5), "chip")
        for ch in (chip1, self.voice_chip, self.eq_chip):
            ch.set_valign(Gtk.Align.CENTER)
            chips.pack_start(ch, False, False, 0)
        c.pack_start(chips, False, False, 0)
        GLib.timeout_add(60, self._breathe)
        return c

    def _breathe(self):
        if not self.get_visible():
            return True
        if self.ctl.state["muted"]:
            self.mic_dot.set_opacity(1)
        else:
            t = GLib.get_monotonic_time() / 1e6
            self.mic_dot.set_opacity(0.55 + 0.45 * (0.5 + 0.5 * math.cos(t / 2.4 * 2 * math.pi)))
        return True

    def _mic_card(self):
        c = self._card(spacing=12)
        head = Gtk.Box(spacing=12)
        head.pack_start(label("Microphone", "h2"), True, True, 0)
        self.test_btn = styled(Gtk.Button(), "test")
        self.test_label = label("Test mic")
        self.test_btn.add(hbox(8, icon("mic", 14, stroke=2.2), self.test_label))
        self.test_btn.set_valign(Gtk.Align.CENTER)
        self.test_btn.connect("clicked", self._toggle_test)
        head.pack_end(self.test_btn, False, False, 0)
        c.pack_start(head, False, False, 0)

        wb = styled(Gtk.Box(), "wavebox")
        self.wave = Waveform()
        wb.pack_start(self.wave, True, True, 0)
        c.pack_start(wb, False, False, 0)
        self.meter = MicMeter(lambda lvl, hot: self.wave.push(lvl, hot))

        gain_head = Gtk.Box()
        gain_head.pack_start(label("Gain", "medium"), True, True, 0)
        self.gain_val = label("", "big22")
        gain_head.pack_end(self.gain_val, False, False, 0)
        self.gain = make_scale(0, 100, self.ctl.set_gain)
        c.pack_start(vbox(2, gain_head, self.gain), False, False, 0)

        row = styled(Gtk.Box(spacing=14), "divider")
        txt = vbox(2, label("Hear yourself", "medium"),
                   label("Your voice in the headphones while you talk", "dim", "small"))
        txt.set_valign(Gtk.Align.CENTER)
        row.pack_start(txt, False, False, 0)
        self.side_scale = make_scale(1, P.SIDETONE_MAX, self._side_level)
        self.side_scale.set_tooltip_text("Level 1–10")
        self.side_scale.set_no_show_all(True)
        row.pack_start(self.side_scale, True, True, 0)
        self.side_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.side_switch.connect("notify::active", self._side_toggle)
        row.pack_end(self.side_switch, False, False, 0)
        c.pack_start(row, False, False, 0)
        self._guard = False
        return c

    def _toggle_test(self, _):
        on = self.meter.proc is None
        if on and not self.meter.start():
            self.test_label.set_text("parec missing")
            return
        if not on:
            self.meter.stop()
        self.wave.set_live(on)
        self.test_label.set_text("Stop test" if on else "Test mic")
        set_class(self.test_btn, "on", on)

    def _side_toggle(self, sw, _):
        if self._guard:
            return
        level = int(self.side_scale.get_value()) or 5
        self.ctl.set_sidetone(level if sw.get_active() else 0)

    def _side_level(self, v):
        if self.side_switch.get_active():
            self.ctl.set_sidetone(v)

    def _voice_card(self):
        c = self._card(spacing=10)
        head = Gtk.Box(spacing=12)
        head.pack_start(label("Voice enhancement", "h2"), True, True, 0)
        head.pack_end(label("Runs on this computer through Easy Effects", "dim", "small"), False, False, 0)
        c.pack_start(head, False, False, 0)
        row = Gtk.Box(spacing=12, homogeneous=True)
        self.mode_btns = {}
        for key, name, tag, desc, wave in MODES:
            b = styled(Gtk.Button(), "opt")
            top = Gtk.Box(spacing=8)
            top.pack_start(label(name, "h2"), True, True, 0)
            top.pack_end(label(tag, "tag"), False, False, 0)
            w = ModeWave(wave, 26)
            d = label(desc, "dim2", "small", wrap=True)
            d.set_max_width_chars(30)
            box = vbox(6, top, w, d)
            box.set_valign(Gtk.Align.START)
            b.add(box)
            b.connect("clicked", lambda _b, k=key: self.ctl.set_mode(k))
            b._wave = w
            self.mode_btns[key] = b
            row.pack_start(b, True, True, 0)
        if not audio.easyeffects_available():
            row.set_sensitive(False)
        c.pack_start(row, False, False, 0)
        return c

    def _eq_card(self):
        c = self._card(spacing=10)
        head = Gtk.Box(spacing=12)
        head.pack_start(label("Equalizer", "h2"), True, True, 0)
        chips = Gtk.Box(spacing=6)
        self.preset_btns = {}
        for key, name in MAIN_PRESETS:
            b = styled(Gtk.Button(label=name), "pill")
            b.connect("clicked", lambda _b, k=key: self.ctl.set_preset(k))
            self.preset_btns[key] = b
            chips.pack_start(b, False, False, 0)
        self.esports_btn = styled(Gtk.MenuButton(), "pill")
        self.esports_label = label("Esports")
        self.esports_btn.add(hbox(6, self.esports_label, icon("chevron", 12)))
        emenu = Gtk.Menu()
        for key, name in ESPORTS:
            it = Gtk.MenuItem(label=name)
            it.connect("activate", lambda _i, k=key: self.ctl.set_preset(k))
            emenu.append(it)
        emenu.show_all()
        self.esports_btn.set_popup(emenu)
        chips.pack_start(self.esports_btn, False, False, 0)
        flat = styled(Gtk.Button(label="Flat"), "pill", "outline")
        flat.connect("clicked", lambda *_: self.ctl.set_bands([0] * 10))
        chips.pack_start(flat, False, False, 0)
        head.pack_end(chips, False, False, 0)
        c.pack_start(head, False, False, 0)

        self.eq_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.curve = EqCurve(self.ctl.set_bands)
        hz = Gtk.Box(homogeneous=True)
        for b in P.EQ_BANDS:
            hz.pack_start(label(b, "dim", "small", xalign=0.5), True, True, 0)
        self.eq_stack.add_named(vbox(4, self.curve, hz), "curve")

        bi = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL), "builtin")
        bi.set_size_request(-1, 190)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                        valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.builtin_title = label("", "h2", xalign=0.5)
        sub = label("It lives in the headset's firmware and can't be read or edited. "
                    "Switch to Custom to drag your own curve.", "dim2", xalign=0.5, wrap=True)
        sub.set_justify(Gtk.Justification.CENTER)
        sub.set_max_width_chars(48)
        go = styled(Gtk.Button(label="Edit Custom Curve"), "pill", "on", "big")
        go.set_halign(Gtk.Align.CENTER)
        go.set_margin_top(6)
        go.connect("clicked", lambda *_: self.ctl.set_preset("custom"))
        for w in (self.builtin_title, sub, go):
            inner.pack_start(w, False, False, 0)
        bi.pack_start(inner, True, True, 0)
        self.eq_stack.add_named(bi, "builtin")
        c.pack_start(self.eq_stack, False, False, 0)
        return c

    def _volume_card(self):
        c = self._card(spacing=4)
        head = Gtk.Box()
        head.pack_start(label("Volume", "h2"), True, True, 0)
        self.vol_val = label("", "big34")
        head.pack_end(self.vol_val, False, False, 0)
        c.pack_start(head, False, False, 0)
        row = Gtk.Box(spacing=12)
        self.vol = make_scale(0, 100, self.ctl.set_volume)
        row.pack_start(icon("speaker", 20, "#a9a9b0"), False, False, 0)
        row.pack_start(self.vol, True, True, 0)
        row.pack_start(icon("speaker-loud", 20, "#a9a9b0"), False, False, 0)
        c.pack_start(row, False, False, 0)
        return c

    def _tile(self, ic, caption, cb):
        b = styled(Gtk.Button(), "tile")
        cap = label(caption, "dim", "small")
        val = label("", "bold")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        img = icon(ic, 20)
        img.set_halign(Gtk.Align.START)
        box.pack_start(img, False, False, 0)
        box.pack_end(vbox(2, cap, val), False, False, 0)
        b.add(box)
        b.connect("clicked", lambda *_: cb())
        b._val = val
        return b

    def _tiles(self):
        g = Gtk.Box(spacing=12, homogeneous=True)
        self.t_off = self._tile("timer", "Auto power-off", self._cycle_off)
        self.t_dnd = self._tile("bluetooth", "Bluetooth", lambda: self.ctl.set_dnd(not self.ctl.state["dnd"]))
        self.t_prof = self._tile("pulse", "Audio profile", self._cycle_profile)
        for t in (self.t_off, self.t_dnd, self.t_prof):
            g.pack_start(t, True, True, 0)
        return g

    def _cycle_off(self):
        cur = self.ctl.state["idle"]
        keys = [k for k, _ in IDLE_STEPS]
        nxt = keys[(keys.index(cur) + 1) % len(keys)] if cur in keys else 0
        self.ctl.set_idle(nxt)

    def _cycle_profile(self):
        available = dict(audio.profiles())
        keys = [k for k, _ in PROFILES if k in available]
        if not keys:
            return
        cur = self.ctl.state["profile"]
        nxt = keys[(keys.index(cur) + 1) % len(keys)] if cur in keys else keys[0]
        self.ctl.set_profile(nxt)

    # -- empty state ---------------------------------------------------------------

    def _build_empty(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                        halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        outer.set_margin_top(24)
        outer.set_margin_bottom(40)
        self.pulse = Pulse()
        self.pulse.set_halign(Gtk.Align.CENTER)
        outer.pack_start(self.pulse, False, False, 0)
        self.empty_title = label("Looking for your headset", "empty-title", xalign=0.5)
        self.empty_title.set_margin_top(4)
        outer.pack_start(self.empty_title, False, False, 0)
        self.empty_sub = label("", "dim2", xalign=0.5, wrap=True)
        self.empty_sub.set_justify(Gtk.Justification.CENTER)
        self.empty_sub.set_max_width_chars(44)
        outer.pack_start(self.empty_sub, False, False, 0)
        steps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        steps.set_margin_top(10)
        steps.set_size_request(380, -1)
        for i, text in enumerate(["Plug the USB dongle into this computer", "Switch the headset on",
                                  "Still nothing? Check that USB access is allowed"], 1):
            num = label(str(i), "stepnum", xalign=0.5)
            num.set_valign(Gtk.Align.CENTER)
            steps.pack_start(styled(hbox(12, num, label(text)), "step"), False, False, 0)
        outer.pack_start(steps, False, False, 0)
        tb = styled(Gtk.Button(label="Troubleshooting…"), "pill", "big")
        tb.set_halign(Gtk.Align.CENTER)
        tb.set_margin_top(10)
        tb.connect("clicked", self._troubleshoot)
        outer.pack_start(tb, False, False, 0)
        return outer

    def _troubleshoot(self, _):
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.INFO,
                              buttons=Gtk.ButtonsType.CLOSE, text="Troubleshooting")
        d.format_secondary_markup(
            "• Make sure the headset is switched on and paired to the dongle.\n"
            "• USB access comes from the udev rule. Re-run <tt>install.sh</tt> from the "
            "blackshark-control folder if you reinstalled your system.\n"
            "• In a terminal, <tt>blackshark-ctl status</tt> shows the exact error.\n"
            "• Unplug the dongle, wait five seconds and plug it back in.")
        d.run()
        d.destroy()

    # -- render -------------------------------------------------------------------

    def render(self, s):
        ok = s["link"] == "ok" and s["battery"] is not None
        self.stack.set_visible_child_name("main" if ok else "empty")
        self.pulse.accent = self.ctl.accent
        if s["link"] == "error":
            self.empty_title.set_text("Can't talk to the headset")
            self.empty_sub.set_text(s.get("error") or "")
        elif s["link"] == "off":
            self.empty_title.set_text("Switch on your headset")
            self.empty_sub.set_text("The dongle is connected. Settings appear here the moment the headset links up.")
        else:
            self.empty_title.set_text("Looking for your headset")
            self.empty_sub.set_text("Settings appear here the moment it connects.")
        if not ok:
            if self.meter.proc:
                self._toggle_test(None)
            return

        pct = s["battery"]
        self.ring.set_level(pct, ring_color(self.ctl, pct))
        self.batt.set_text(f"{pct}%")
        set_class(self.batt, "low", pct <= LOW_BATTERY)
        fw = f" · Firmware {s['firmware']}" if s["firmware"] else ""
        self.sub.set_text(("Charging" if s["charging"] else "Connected") + fw)
        self.mic_chip_label.set_text("Mic muted" if s["muted"] else "Mic live")
        set_class(self.mic_dot, "muted", s["muted"])
        self.voice_chip.set_text(f"{MODE_LABEL.get(s['mode'], 'Off')} voice")
        self.eq_chip.set_text(f"{PRESET_LABEL.get(s['preset'], 'Custom')} EQ")

        self.wave.accent = self.ctl.accent
        set_scale(self.gain, s["gain"])
        self.gain_val.set_text(str(s["gain"]))
        self._guard = True
        self.side_switch.set_active(s["sidetone"] > 0)
        self._guard = False
        if s["sidetone"]:
            set_scale(self.side_scale, s["sidetone"])
        self.side_scale.set_visible(s["sidetone"] > 0)

        for key, b in self.mode_btns.items():
            on = key == s["mode"]
            set_class(b, "on", on)
            b._wave.set_color(self.ctl.accent if on else "#8a8a92")

        preset = s["preset"]
        for key, b in self.preset_btns.items():
            set_class(b, "on", key == preset)
        is_esports = preset in dict(ESPORTS)
        set_class(self.esports_btn, "on", is_esports)
        self.esports_label.set_text(PRESET_LABEL[preset] if is_esports else "Esports")
        if preset in BUILTIN:
            self.builtin_title.set_text(f"{PRESET_LABEL[preset]} is a built-in curve")
            self.eq_stack.set_visible_child_name("builtin")
        else:
            self.curve.accent = self.ctl.accent
            self.curve.set_bands(s["bands"])
            self.curve.set_tooltip_text(
                f"{PRESET_LABEL[preset]} profile. Drag a point to turn it into your Custom curve."
                if is_esports else "Drag a point, scroll over a band, or use the arrow keys. Saved on the headset.")
            self.eq_stack.set_visible_child_name("curve")

        set_scale(self.vol, s["volume"])
        self.vol_val.set_text(str(s["volume"]))
        self.t_off._val.set_text(dict(IDLE_STEPS).get(s["idle"], f"{s['idle']} minutes"))
        self.t_dnd._val.set_text("Do not disturb" if s["dnd"] else "Normal")
        set_class(self.t_dnd, "on", s["dnd"])
        self.t_prof._val.set_text(dict(PROFILES).get(s["profile"], "Other"))


# ---------------------------------------------------------------------------
# Tray quick panel
# ---------------------------------------------------------------------------

class QuickPanel(Gtk.Window):
    def __init__(self, ctl, open_main):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.ctl = ctl
        styled(self, "bs", "qp-win")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)
        self.connect("focus-out-event", lambda *_: self.hide())
        self.connect("key-press-event", lambda w, e: e.keyval == Gdk.KEY_Escape and (self.hide() or True))

        root = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16), "qp")
        root.set_size_request(380, -1)
        self.add(root)

        top = Gtk.Box(spacing=14)
        self.ring = BatteryRing(64, 6, 28, 1.6)
        top.pack_start(self.ring, False, False, 0)
        self.pct = label("–", "big26")
        names = vbox(2, self.pct, label("BlackShark V2 Pro", "medium"))
        names.set_valign(Gtk.Align.CENTER)
        top.pack_start(names, True, True, 0)
        openb = styled(Gtk.Button(), "circle")
        openb.set_tooltip_text("Open BlackShark Control")
        openb.add(icon("open", 16))
        openb.set_valign(Gtk.Align.CENTER)
        openb.connect("clicked", lambda *_: (self.hide(), open_main()))
        top.pack_end(openb, False, False, 0)
        root.pack_start(top, False, False, 0)

        tiles = Gtk.Box(spacing=10, homogeneous=True)
        self.dot = styled(Gtk.Box(valign=Gtk.Align.CENTER), "dot")
        self.mute_val = label("Mic live", "bold")
        t1 = styled(vbox(10, hbox(8, self.dot, label("Mute switch", "dim", "small")), self.mute_val), "qtile")
        self.side_btn = styled(Gtk.Button(), "qtile")
        self.side_val = label("Off", "bold")
        self.side_btn.add(vbox(10, label("Hear yourself", "dim", "small"), self.side_val))
        self.side_btn.connect("clicked", self._toggle_side)
        tiles.pack_start(t1, True, True, 0)
        tiles.pack_start(self.side_btn, True, True, 0)
        root.pack_start(tiles, False, False, 0)

        seg = styled(Gtk.Box(spacing=4, homogeneous=True), "seggroup")
        self.seg_btns = {}
        for key, name, _tag, _desc, wave in MODES:
            b = styled(Gtk.Button(), "seg")
            w = ModeWave(wave, 14, 44)
            w.set_halign(Gtk.Align.CENTER)
            inner = vbox(4, w, label(name, xalign=0.5))
            inner.set_valign(Gtk.Align.CENTER)
            b.add(inner)
            b._wave = w
            b.connect("clicked", lambda _b, k=key: self.ctl.set_mode(k))
            self.seg_btns[key] = b
            seg.pack_start(b, True, True, 0)
        root.pack_start(vbox(8, label("Voice", "sectlabel"), seg), False, False, 0)

        vh = Gtk.Box()
        vh.pack_start(label("Volume", "sectlabel"), True, True, 0)
        self.vol_val = label("", "bold")
        vh.pack_end(self.vol_val, False, False, 0)
        self.vol = make_scale(0, 100, self.ctl.set_volume)
        root.pack_start(vbox(4, vh, self.vol), False, False, 0)

        chips = Gtk.Box(spacing=6)
        self.preset_btns = {}
        for key, name in MAIN_PRESETS:
            b = styled(Gtk.Button(label=name), "pill")
            b.connect("clicked", lambda _b, k=key: self.ctl.set_preset(k))
            self.preset_btns[key] = b
            chips.pack_start(b, False, False, 0)
        root.pack_start(vbox(8, label("Equalizer", "sectlabel"), chips), False, False, 0)
        root.show_all()
        self._last_side = 5
        self._layer_ready = False

    def _toggle_side(self, _):
        self.ctl.set_sidetone(0 if self.ctl.state["sidetone"] else self._last_side)

    def render(self, s):
        pct = s["battery"]
        known = s["link"] == "ok" and pct is not None
        self.ring.set_level(pct or 0, ring_color(self.ctl, pct), known)
        self.pct.set_text(f"{pct}%" if known else ("Off" if s["link"] == "off" else "–"))
        self.mute_val.set_text("Mic muted" if s["muted"] else "Mic live")
        set_class(self.dot, "muted", s["muted"])
        if s["sidetone"]:
            self._last_side = s["sidetone"]
        self.side_val.set_text(f"On · {s['sidetone']}" if s["sidetone"] else "Off")
        set_class(self.side_btn, "on", bool(s["sidetone"]))
        for key, b in self.seg_btns.items():
            on = key == s["mode"]
            set_class(b, "on", on)
            b._wave.set_color("#ffffff" if on else "#8a8a92")
        set_scale(self.vol, s["volume"])
        self.vol_val.set_text(str(s["volume"]))
        for key, b in self.preset_btns.items():
            set_class(b, "on", key == s["preset"])

    def popup_from_tray(self, panel_pos=None, thickness=0):
        """Open next to the panel; used by the Plasma tray, which has no pointer coords."""
        if panel_pos is None:
            panel_pos, thickness = plasma_panel()
        nat = self.get_preferred_size()[1]
        w, h = nat.width, nat.height
        display = Gdk.Display.get_default()
        mon = display.get_primary_monitor()
        wa = mon.get_workarea() if mon else Gdk.Rectangle()
        if panel_pos in (Gtk.PositionType.TOP, Gtk.PositionType.BOTTOM):
            x = wa.x + wa.width - w // 2 - 12
            y = wa.y if panel_pos == Gtk.PositionType.TOP else wa.y + wa.height
        else:
            x = wa.x if panel_pos == Gtk.PositionType.LEFT else wa.x + wa.width
            y = wa.y + h // 2 + 12
        self.popup_at(x, y, panel_pos, thickness)

    def popup_at(self, x, y, panel_pos, panel_thickness=0):
        if is_wayland():
            # Wayland ignores gtk_window_move(); the compositor owns placement.
            # gtk-layer-shell (when installed) gives us a real screen-edge popup.
            self._place_wayland(panel_pos, panel_thickness)
            self.show_all()
            self.present_with_time(Gtk.get_current_event_time())
            return
        self.show_all()
        nat = self.get_preferred_size()[1]
        w, h = nat.width, nat.height
        display = Gdk.Display.get_default()
        mon = display.get_monitor_at_point(x, y) or display.get_primary_monitor()
        wa = mon.get_workarea()
        # The window has a 20px transparent margin for the shadow; let it overlap the panel edge.
        if panel_pos == Gtk.PositionType.TOP:
            py = wa.y - 12
        elif panel_pos == Gtk.PositionType.BOTTOM:
            py = wa.y + wa.height - h + 12
        else:
            py = y - h // 2
        if panel_pos == Gtk.PositionType.LEFT:
            px = wa.x - 12
        elif panel_pos == Gtk.PositionType.RIGHT:
            px = wa.x + wa.width - w + 12
        else:
            px = x - w // 2
        px = max(wa.x - 20, min(px, wa.x + wa.width - w + 20))
        py = max(wa.y - 20, min(py, wa.y + wa.height - h + 20))
        self.move(px, py)
        self.present_with_time(Gtk.get_current_event_time())

    def _place_wayland(self, panel_pos, thickness):
        if LayerShell is None or not LayerShell.is_supported():
            return False
        if not self._layer_ready:
            if self.get_realized():
                return False
            LayerShell.init_for_window(self)
            LayerShell.set_namespace(self, "blackshark-control")
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.ON_DEMAND)
            self._layer_ready = True
        edges = (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                 LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT)
        for edge in edges:
            LayerShell.set_anchor(self, edge, False)
            LayerShell.set_margin(self, edge, 0)
        gap = max(0, thickness) + 8
        if panel_pos == Gtk.PositionType.TOP:
            LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
            LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
            LayerShell.set_margin(self, LayerShell.Edge.TOP, gap)
            LayerShell.set_margin(self, LayerShell.Edge.RIGHT, 8)
        elif panel_pos == Gtk.PositionType.LEFT:
            LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
            LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
            LayerShell.set_margin(self, LayerShell.Edge.TOP, 8)
            LayerShell.set_margin(self, LayerShell.Edge.LEFT, gap)
        elif panel_pos == Gtk.PositionType.RIGHT:
            LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
            LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
            LayerShell.set_margin(self, LayerShell.Edge.TOP, 8)
            LayerShell.set_margin(self, LayerShell.Edge.RIGHT, gap)
        else:  # bottom, the Plasma default
            LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, True)
            LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
            LayerShell.set_margin(self, LayerShell.Edge.BOTTOM, gap)
            LayerShell.set_margin(self, LayerShell.Edge.RIGHT, 8)
        return True


# ---------------------------------------------------------------------------
# Tray icon
# ---------------------------------------------------------------------------

class Tray:
    def __init__(self, ctl, panel, open_main, quit_):
        self.ctl, self.panel = ctl, panel
        self.open_main = open_main
        self.icon = None
        self.ind = None
        self.sni = None
        # Plasma speaks StatusNotifierItem natively; prefer that to a missing
        # libayatana-appindicator. XApp/AppIndicator remain the Cinnamon path.
        if is_kde() or not (XApp or AppIndicator):
            try:
                self.sni = StatusNotifierItem(self._activate, self._open_main, quit_)
            except Exception as e:
                print("tray: StatusNotifierItem unavailable:", e, file=sys.stderr)
        if self.sni:
            return
        menu = Gtk.Menu()
        o = Gtk.MenuItem(label="Open BlackShark Control")
        o.connect("activate", lambda *_: open_main())
        q = Gtk.MenuItem(label="Quit")
        q.connect("activate", lambda *_: quit_())
        menu.append(o)
        menu.append(q)
        menu.show_all()
        if XApp:
            self.icon = XApp.StatusIcon()
            self.icon.set_name("blackshark-control")
            self.icon.set_icon_name("audio-headset")
            self.icon.set_secondary_menu(menu)
            self.icon.connect("button-release-event", self._click)
        elif AppIndicator:
            self.ind = AppIndicator.Indicator.new("blackshark-control", "audio-headset",
                                                  AppIndicator.IndicatorCategory.HARDWARE)
            self.ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            self.ind.set_menu(menu)

    @property
    def available(self):
        return bool(self.sni or self.icon or self.ind)

    def _activate(self, _x, _y, token=None):
        if self.panel.get_visible():
            self.panel.hide()
        else:
            # Plasma supplies an XDG activation token so the popup may take
            # focus on Wayland under its focus-stealing protection.
            self.panel.set_startup_id(token or "")
            panel_pos, thickness = plasma_panel()
            self.panel.popup_from_tray(panel_pos, thickness)

    def _open_main(self, token=None):
        if token:
            self.ctl.gapp.win.set_startup_id(token)
        self.open_main()

    def _click(self, _icon, x, y, button, _time, panel_pos):
        if button == 1:
            if self.panel.get_visible():
                self.panel.hide()
            else:
                self.panel.popup_at(x, y, panel_pos)

    def render(self, s):
        pct = s["battery"]
        ok = s["link"] == "ok" and pct is not None
        text = f"{pct}%" if ok else ""
        detail = (f"{pct}%" + (" · charging" if s["charging"] else "")) if ok \
            else "Not connected"
        tip = f"BlackShark V2 Pro · {detail}"
        name = "battery-full-charging" if ok and s["charging"] else (
            "battery-low" if ok and pct <= LOW_BATTERY else "audio-headset")
        if self.sni:
            overlay = ("battery-full-charging" if s["charging"] else
                       "battery-low" if ok and pct <= LOW_BATTERY else "")
            self.sni.set_state("audio-headset", overlay if ok else "",
                               "BlackShark V2 Pro", detail)
        elif self.icon:
            self.icon.set_label(text)
            self.icon.set_tooltip_text(tip)
            self.icon.set_icon_name(name)
        elif self.ind:
            self.ind.set_label(text, "100%")
            self.ind.set_icon_full(name, tip)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class GApp(Gtk.Application):
    """Single instance: launching again just raises the existing window."""

    def __init__(self, tray_only):
        super().__init__(application_id="io.github.blackshark_control")
        self.tray_only = tray_only
        self.ctl = None

    def do_activate(self):
        if self.ctl is None:
            Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
            self.hold()
            self.ctl = Controller(self)
            self.win = MainWindow(self, self.ctl)
            self.ctl.add_view(self.win)
            self.panel = QuickPanel(self.ctl, self.show_main)
            self.ctl.add_view(self.panel)
            self.tray = Tray(self.ctl, self.panel, self.show_main, self.quit)
            self.ctl.add_view(self.tray)
            if self.tray_only and self.tray.available:
                return
        self.show_main()

    def show_main(self):
        self.win.show()
        self.win.present()
        self.ctl.refresh_device()
        self.ctl.refresh_audio()

    def window_closed(self):
        if self.win.meter.proc:
            self.win._toggle_test(None)
        if not self.tray.available:
            self.quit()


def main():
    tray = "--tray" in sys.argv
    GApp(tray).run([sys.argv[0]])


if __name__ == "__main__":
    main()
