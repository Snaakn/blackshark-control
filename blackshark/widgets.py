"""Custom-drawn GTK3 widgets: battery ring, EQ curve, mic waveform, mode waves, SVG icons."""

import math
import re

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

TEXT = (0xF2 / 255, 0xF2 / 255, 0xF4 / 255)
HOT = "#ff7139"

# Stroke icons on a 24x24 grid (same artwork as the design).
ICONS = {
    "headset": '<path d="M4 15v-3a8 8 0 0 1 16 0v3"/><rect x="3" y="13.5" width="4" height="6.5" rx="1.5"/>'
               '<rect x="17" y="13.5" width="4" height="6.5" rx="1.5"/><path d="M5 20c0 1.4 2.5 2 6 2"/>'
               '<circle cx="11.5" cy="22" r=".6" fill="currentColor"/>',
    "headset-small": '<path d="M4 15v-3a8 8 0 0 1 16 0v3"/><rect x="3" y="13.5" width="4" height="6.5" rx="1.5"/>'
                     '<rect x="17" y="13.5" width="4" height="6.5" rx="1.5"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    "speaker": '<path d="M4 9h3l4-4v14l-4-4H4z"/>',
    "speaker-loud": '<path d="M4 9h3l4-4v14l-4-4H4z"/><path d="M15 9.5a3.5 3.5 0 0 1 0 5M17.5 7a7 7 0 0 1 0 10"/>',
    "timer": '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5M9 2h6"/>',
    "bluetooth": '<path d="M7 7l10 10-5 5V2l5 5L7 17"/>',
    "pulse": '<path d="M3 12h3l3-7 4 14 3-7h5"/>',
    "open": '<path d="M14 4h6v6M20 4l-8 8M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    "chevron": '<path d="M6 9l6 6 6-6"/>',
}


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def icon(name, size=16, color="#f2f2f4", stroke=2.0):
    """A Gtk.Image of a stroke icon, rendered crisply for the screen's scale."""
    img = Gtk.Image()
    set_icon(img, name, size, color, stroke)
    return img


def set_icon(img, name, size=16, color="#f2f2f4", stroke=2.0):
    scale = max(1, img.get_scale_factor())
    body = ICONS[name].replace("currentColor", color)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size * scale}" height="{size * scale}" '
           f'viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="{stroke}" '
           f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.write(svg.encode())
    loader.close()
    surface = Gdk.cairo_surface_create_from_pixbuf(loader.get_pixbuf(), scale, None)
    img.set_from_surface(surface)


def icon_pixbuf(name, px, color="#f2f2f4", stroke=2.0):
    """A pixbuf version of one of the stroke icons, for tray/DBus use."""
    return _icon_pixbuf(name, px, color, stroke)


def _icon_pixbuf(name, px, color, stroke):
    body = ICONS[name].replace("currentColor", color)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{px}" height="{px}" viewBox="0 0 24 24" '
           f'fill="none" stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
           f'stroke-linejoin="round">{body}</svg>')
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.write(svg.encode())
    loader.close()
    return loader.get_pixbuf()


class BatteryRing(Gtk.DrawingArea):
    """Battery level as a ring around the headset glyph."""

    def __init__(self, size=220, ring=12, glyph=96, glyph_stroke=1.2):
        super().__init__()
        self.size, self.ring, self.glyph, self.glyph_stroke = size, ring, glyph, glyph_stroke
        self.set_size_request(size, size)
        self.pct = 0
        self.color = "#35a854"
        self.known = False
        self._glyph = None
        self.connect("draw", self._draw)

    def set_level(self, pct, color, known=True):
        self.pct, self.color, self.known = pct, color, known
        self.queue_draw()

    def _draw(self, _w, cr):
        s = self.size
        r = (s - self.ring) / 2 - 2
        cr.set_line_width(self.ring)
        cr.set_source_rgba(1, 1, 1, 0.08)
        cr.arc(s / 2, s / 2, r, 0, 2 * math.pi)
        cr.stroke()
        if self.known and self.pct > 0:
            cr.set_source_rgb(*hex_rgb(self.color))
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            start = -math.pi / 2
            cr.arc(s / 2, s / 2, r, start, start + 2 * math.pi * min(self.pct, 100) / 100)
            cr.stroke()
        scale = max(1, self.get_scale_factor())
        if self._glyph is None:
            self._glyph = Gdk.cairo_surface_create_from_pixbuf(
                _icon_pixbuf("headset" if self.glyph > 40 else "headset-small",
                             self.glyph * scale, "#f2f2f4", self.glyph_stroke), scale, None)
        cr.set_source_surface(self._glyph, (s - self.glyph) / 2, (s - self.glyph) / 2)
        cr.paint()


def _path_cmds(d):
    """Tokenise an absolute M/L/C/S path (the only commands the mode waves use)."""
    toks = re.findall(r"[MLCS]|-?\d*\.?\d+", d)
    out, cmd, nums = [], None, []
    for t in toks:
        if t in "MLCS":
            if cmd:
                out.append((cmd, nums))
            cmd, nums = t, []
        else:
            nums.append(float(t))
    if cmd:
        out.append((cmd, nums))
    return out


class ModeWave(Gtk.DrawingArea):
    """A small waveform glyph drawn from a 100x40 path, stretched to fit."""

    def __init__(self, d, height=44, width=-1):
        super().__init__()
        self.cmds = _path_cmds(d)
        self.color = "#8a8a92"
        self.set_size_request(width, height)
        self.connect("draw", self._draw)

    def set_color(self, c):
        self.color = c
        self.queue_draw()

    def _draw(self, w, cr):
        a = w.get_allocation()
        sx, sy = a.width / 100, a.height / 40
        P = lambda x, y: (x * sx, y * sy)  # noqa: E731
        last_ctrl = None
        cur = (0, 0)
        for cmd, n in self.cmds:
            if cmd == "M":
                cur = (n[0], n[1])
                cr.move_to(*P(*cur))
            elif cmd == "L":
                for i in range(0, len(n), 2):
                    cur = (n[i], n[i + 1])
                    cr.line_to(*P(*cur))
                last_ctrl = None
            elif cmd == "C":
                for i in range(0, len(n), 6):
                    c1, c2, cur = (n[i], n[i + 1]), (n[i + 2], n[i + 3]), (n[i + 4], n[i + 5])
                    cr.curve_to(*P(*c1), *P(*c2), *P(*cur))
                    last_ctrl = c2
            elif cmd == "S":
                for i in range(0, len(n), 4):
                    c1 = (2 * cur[0] - last_ctrl[0], 2 * cur[1] - last_ctrl[1]) if last_ctrl else cur
                    c2, end = (n[i], n[i + 1]), (n[i + 2], n[i + 3])
                    cr.curve_to(*P(*c1), *P(*c2), *P(*end))
                    last_ctrl, cur = c2, end
        cr.set_source_rgb(*hex_rgb(self.color))
        cr.set_line_width(2)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke()


class Waveform(Gtk.DrawingArea):
    """Scrolling bar meter of the microphone level."""

    BARS = 44

    def __init__(self, height=72):
        super().__init__()
        self.set_size_request(-1, height)
        self.levels = [0.0] * self.BARS
        self.hot = [False] * self.BARS
        self.live = False
        self.accent = "#35a854"
        self.connect("draw", self._draw)

    def push(self, level, hot):
        self.levels = self.levels[1:] + [level]
        self.hot = self.hot[1:] + [hot]
        self.queue_draw()

    def set_live(self, on):
        self.live = on
        if not on:
            self.levels = [0.0] * self.BARS
            self.hot = [False] * self.BARS
        self.queue_draw()

    def _draw(self, w, cr):
        a = w.get_allocation()
        bw, gap = 4, 3
        total = self.BARS * bw + (self.BARS - 1) * gap
        x = (a.width - total) / 2
        mid = a.height / 2
        for lvl, hot in zip(self.levels, self.hot):
            h = 6 + lvl * (a.height - 20) if self.live else 6
            if self.live:
                cr.set_source_rgb(*hex_rgb(HOT if hot else self.accent))
            else:
                cr.set_source_rgba(1, 1, 1, 0.14)
            _rounded(cr, x, mid - h / 2, bw, h, 2)
            cr.fill()
            x += bw + gap


def _rounded(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


class EqCurve(Gtk.DrawingArea):
    """10-band equalizer as a smooth curve with draggable nodes.

    Drag a node (or scroll over a column, or use arrow keys) to change a band.
    `on_change(bands)` fires when an edit is committed.
    """

    H = 170
    MAX = 12

    def __init__(self, on_change):
        super().__init__()
        self.on_change = on_change
        self.bands = [0] * 10
        self.accent = "#35a854"
        self.drag = None
        self.hover = None
        self.focus_band = 0
        self.set_size_request(-1, self.H)
        self.set_can_focus(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.SCROLL_MASK
                        | Gdk.EventMask.SMOOTH_SCROLL_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
                        | Gdk.EventMask.KEY_PRESS_MASK)
        self.connect("draw", self._draw)
        self.connect("button-press-event", self._press)
        self.connect("motion-notify-event", self._motion)
        self.connect("button-release-event", self._release)
        self.connect("scroll-event", self._scroll)
        self.connect("leave-notify-event", lambda *_: self._set_hover(None))
        self.connect("key-press-event", self._key)
        self.connect("focus-in-event", lambda *_: self.queue_draw())
        self.connect("focus-out-event", lambda *_: self.queue_draw())

    def set_bands(self, bands):
        if self.drag is None:
            self.bands = list(bands)
            self.queue_draw()

    def _y(self, v):
        return self.H / 2 - (v / self.MAX) * self._amp()

    def _col(self, x):
        w = self.get_allocated_width()
        return max(0, min(9, int(x / (w / 10))))

    def _val(self, y):
        return max(-self.MAX, min(self.MAX, round((self.H / 2 - y) / self._amp() * self.MAX)))

    def _amp(self):
        # Room above the top node for its value label.
        return self.H / 2 - 30

    def _set_hover(self, i):
        if i != self.hover:
            self.hover = i
            self.queue_draw()

    def _press(self, _w, ev):
        if ev.button != 1:
            return False
        self.grab_focus()
        self.drag = self._col(ev.x)
        self.focus_band = self.drag
        self._apply(self.drag, self._val(ev.y))
        return True

    def _motion(self, _w, ev):
        if self.drag is not None:
            self._apply(self.drag, self._val(ev.y))
        else:
            self._set_hover(self._col(ev.x))
        return True

    def _release(self, _w, ev):
        if self.drag is not None and ev.button == 1:
            self.drag = None
            self.on_change(list(self.bands))
        return True

    def _scroll(self, _w, ev):
        i = self._col(ev.x)
        ok, dx, dy = ev.get_scroll_deltas()
        step = 0
        if ev.direction == Gdk.ScrollDirection.UP:
            step = 1
        elif ev.direction == Gdk.ScrollDirection.DOWN:
            step = -1
        elif ok and dy:
            step = -1 if dy > 0 else 1
        if step:
            self._apply(i, self.bands[i] + step)
            self._debounced_commit()
        return True

    def _key(self, _w, ev):
        k = ev.keyval
        i = self.focus_band
        if k in (Gdk.KEY_Left, Gdk.KEY_Right):
            self.focus_band = max(0, min(9, i + (1 if k == Gdk.KEY_Right else -1)))
            self.queue_draw()
            return True
        if k in (Gdk.KEY_Up, Gdk.KEY_Down):
            self._apply(i, self.bands[i] + (1 if k == Gdk.KEY_Up else -1))
            self._debounced_commit()
            return True
        return False

    _commit_id = None

    def _debounced_commit(self):
        if self._commit_id:
            GLib.source_remove(self._commit_id)

        def fire():
            self._commit_id = None
            self.on_change(list(self.bands))
            return False
        self._commit_id = GLib.timeout_add(400, fire)

    def _apply(self, i, v):
        v = max(-self.MAX, min(self.MAX, v))
        if self.bands[i] != v:
            self.bands[i] = v
            self.queue_draw()

    def _draw(self, w, cr):
        W = w.get_allocated_width()
        H = self.H
        sx = W / 1000
        ac = hex_rgb(self.accent)

        # Panel background
        _rounded(cr, 0, 0, W, H, 14)
        cr.set_source_rgba(0, 0, 0, 0.22)
        cr.fill()

        cr.set_line_width(1)
        mid, amp = H / 2, self._amp()
        for y in (mid - amp, mid - amp / 2, mid + amp / 2, mid + amp):
            cr.set_source_rgba(1, 1, 1, 0.05)
            cr.move_to(0, y + .5)
            cr.line_to(W, y + .5)
            cr.stroke()
        cr.set_source_rgba(1, 1, 1, 0.2)
        cr.set_dash([5, 5])
        cr.move_to(0, mid + .5)
        cr.line_to(W, mid + .5)
        cr.stroke()
        cr.set_dash([])

        pts = [((i + 0.5) * 100 * sx, self._y(v)) for i, v in enumerate(self.bands)]

        def trace():
            cr.move_to(0, pts[0][1])
            cr.line_to(*pts[0])
            for i in range(len(pts) - 1):
                p0 = pts[i - 1] if i > 0 else pts[i]
                p1, p2 = pts[i], pts[i + 1]
                p3 = pts[i + 2] if i + 2 < len(pts) else p2
                cr.curve_to(p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6,
                            p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6, *p2)
            cr.line_to(W, pts[-1][1])

        cr.save()
        _rounded(cr, 0, 0, W, H, 14)
        cr.clip()
        trace()
        cr.line_to(W, H)
        cr.line_to(0, H)
        cr.close_path()
        grad = cairo.LinearGradient(0, 0, 0, H)
        grad.add_color_stop_rgba(0, *ac, 0.45)
        grad.add_color_stop_rgba(1, *ac, 0)
        cr.set_source(grad)
        cr.fill()
        trace()
        cr.set_source_rgb(*ac)
        cr.set_line_width(3)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke()
        cr.restore()

        has_focus = self.has_focus()
        for i, (x, y) in enumerate(pts):
            big = i in (self.hover, self.drag)
            r = 9 * (1.15 if big else 1)
            for k in range(6, 0, -1):  # glow
                cr.set_source_rgba(*ac, 0.05)
                cr.arc(x, y, r + k * 2, 0, 2 * math.pi)
                cr.fill()
            cr.set_source_rgb(*ac)
            cr.arc(x, y, r, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgb(1, 1, 1)
            cr.arc(x, y, r - 4, 0, 2 * math.pi)
            cr.fill()
            if has_focus and i == self.focus_band:
                cr.set_source_rgb(1, 1, 1)
                cr.set_line_width(2)
                cr.arc(x, y, r + 4, 0, 2 * math.pi)
                cr.stroke()
            v = self.bands[i]
            label = f"+{v}" if v > 0 else (f"−{abs(v)}" if v < 0 else "0")
            cr.select_font_face("Ubuntu", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(12)
            ext = cr.text_extents(label)
            ly = y - 16
            cr.set_source_rgb(1, 1, 1)
            cr.move_to(x - ext.x_advance / 2, ly)
            cr.show_text(label)


class Pulse(Gtk.DrawingArea):
    """Expanding rings around a headset badge (the "looking for headset" art)."""

    def __init__(self, size=220):
        super().__init__()
        self.size = size
        self.accent = "#35a854"
        self.t0 = GLib.get_monotonic_time()
        self.set_size_request(size, size)
        self._glyph = None
        self.connect("draw", self._draw)
        self._tick = None
        self.connect("map", lambda *_: self._start())
        self.connect("unmap", lambda *_: self._stop())

    def _start(self):
        if not self._tick:
            self._tick = self.add_tick_callback(lambda *_: (self.queue_draw(), True)[1])

    def _stop(self):
        if self._tick:
            self.remove_tick_callback(self._tick)
            self._tick = None

    def _draw(self, w, cr):
        s = self.size
        c = s / 2
        ac = hex_rgb(self.accent)
        t = (GLib.get_monotonic_time() - self.t0) / 1e6
        cr.set_line_width(2)
        for k in range(3):
            p = ((t + k * 0.8) % 2.4) / 2.4
            scale = 0.55 + p * (1.7 - 0.55)
            cr.set_source_rgba(*ac, 0.7 * (1 - p))
            cr.arc(c, c, (s / 2 - 1) * scale / 1.7, 0, 2 * math.pi)
            cr.stroke()
        # badge
        base = hex_rgb("#2a2a2f")
        mixed = tuple(b * 0.78 + a * 0.22 for a, b in zip(ac, base))
        for k in range(8, 0, -1):
            cr.set_source_rgba(*ac, 0.03)
            cr.arc(c, c, 56 + k * 4, 0, 2 * math.pi)
            cr.fill()
        cr.set_source_rgb(*mixed)
        cr.arc(c, c, 56, 0, 2 * math.pi)
        cr.fill()
        scale = max(1, self.get_scale_factor())
        if self._glyph is None:
            self._glyph = Gdk.cairo_surface_create_from_pixbuf(
                _icon_pixbuf("headset", 60 * scale, "#ffffff", 1.4), scale, None)
        cr.set_source_surface(self._glyph, c - 30, c - 30)
        cr.paint()
