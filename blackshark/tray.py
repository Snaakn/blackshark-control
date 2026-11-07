"""Tray backend for KDE Plasma and other StatusNotifierItem hosts.

KDE Plasma has no XApp or AppIndicator on Wayland.  Instead it shows tray
icons through the freedesktop ``org.kde.StatusNotifierItem`` protocol (the
same one AppIndicator uses under the hood).  This module exports that object
and its ``com.canonical.dbusmenu`` context menu directly over D-Bus, so the
app needs no extra tray library on Plasma.
"""

import os
import sys

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .widgets import icon_pixbuf  # noqa: E402

ITEM_IFACE = "org.kde.StatusNotifierItem"
ITEM_PATH = "/StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"
MENU_PATH = "/MenuBar"
WATCHERS = ("org.kde.StatusNotifierWatcher", "org.freedesktop.StatusNotifierWatcher")

ITEM_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Activate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
    <method name="ProvideXDGActivationToken">
      <arg name="token" type="s" direction="in"/>
    </method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus">
      <arg name="status" type="s"/>
    </signal>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="updatesNeeded" type="ai" direction="out"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{sv})"/>
      <arg name="removedProps" type="a(ias)"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg name="id" type="i"/>
      <arg name="timestamp" type="u"/>
    </signal>
  </interface>
</node>
"""

FALLBACK_ICON_COLOR = "#35a854"
PIXMAP_SIZES = (16, 22, 24, 32, 48, 64)


def _argb32_pixmaps(pixbuf):
    """Serialise a GdkPixbuf as the big-endian ARGB32 images SNI expects."""
    width, height = pixbuf.get_width(), pixbuf.get_height()
    rowstride = pixbuf.get_rowstride()
    channels = pixbuf.get_n_channels()
    has_alpha = pixbuf.get_has_alpha()
    pixels = pixbuf.get_pixels()
    data = bytearray(width * height * 4)
    for y in range(height):
        src = y * rowstride
        dst = y * width * 4
        for x in range(width):
            i = src + x * channels
            r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
            a = pixels[i + 3] if has_alpha else 255
            o = dst + x * 4
            data[o], data[o + 1], data[o + 2], data[o + 3] = a, r, g, b
    return [(width, height, bytes(data))]


def _headset_pixmaps():
    """A themed-icon fallback, for hosts whose icon theme lacks audio-headset."""
    out = []
    try:
        for size in PIXMAP_SIZES:
            out.extend(_argb32_pixmaps(
                icon_pixbuf("headset", size, FALLBACK_ICON_COLOR, 2.0)))
    except Exception:
        return []
    return out


class StatusNotifierItem:
    """A KDE StatusNotifierItem with a DBusMenu context menu."""

    def __init__(self, on_activate, on_open, on_quit):
        self._on_activate_cb = on_activate
        self._on_open_cb = on_open
        self._on_quit_cb = on_quit
        self._conn = None
        self._bus_name = None
        self._item_reg = 0
        self._menu_reg = 0
        self._watches = []
        self._watcher_errors = set()
        self._activation_token = None
        self._revision = 1

        self._status = "Active"
        self._icon_name = "audio-headset"
        self._overlay_name = ""
        self._icon_pixmaps = _headset_pixmaps()
        self._overlay_pixmaps = []
        self._tooltip_title = "BlackShark V2 Pro"
        self._tooltip_text = ""

        self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._request_name()
        self._register_objects()
        self._watch_watchers()

    # -- setup ----------------------------------------------------------------

    def _request_name(self):
        name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        try:
            reply = self._conn.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "RequestName", GLib.Variant("(su)", (name, 4)),
                GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, -1, None)
            code = reply.unpack()[0]
            if code in (1, 4):  # primary owner, or already owner
                self._bus_name = name
        except GLib.Error as e:
            print("tray: could not own a D-Bus name:", e.message, file=sys.stderr)
        if not self._bus_name:
            self._bus_name = self._conn.get_unique_name()

    def _register_objects(self):
        item_info = Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0]
        menu_info = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self._item_reg = self._conn.register_object(
            ITEM_PATH, item_info, self._item_call, self._item_get, None)
        self._menu_reg = self._conn.register_object(
            MENU_PATH, menu_info, self._menu_call, self._menu_get, None)

    def _watch_watchers(self):
        for name in WATCHERS:
            self._watches.append(Gio.bus_watch_name_on_connection(
                self._conn, name, Gio.BusNameWatcherFlags.NONE,
                self._watcher_appeared, self._watcher_vanished))

    def _watcher_appeared(self, connection, name, owner):
        try:
            connection.call_sync(
                name, "/StatusNotifierWatcher", "org.kde.StatusNotifierWatcher",
                "RegisterStatusNotifierItem", GLib.Variant("(s)", (self._bus_name,)),
                None, Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error as e:
            if name not in self._watcher_errors:
                self._watcher_errors.add(name)
                print(f"tray: {name} registration failed: {e.message}", file=sys.stderr)

    def _watcher_vanished(self, connection, name):
        return

    def stop(self):
        for watch in self._watches:
            Gio.bus_unwatch_name(watch)
        self._watches.clear()
        if self._conn:
            if self._item_reg:
                self._conn.unregister_object(self._item_reg)
            if self._menu_reg:
                self._conn.unregister_object(self._menu_reg)
            self._item_reg = self._menu_reg = 0

    # -- public state ---------------------------------------------------------

    def set_state(self, icon_name, overlay_name, tooltip_title, tooltip_text,
                  status="Active"):
        icon_changed = icon_name != self._icon_name
        overlay_changed = overlay_name != self._overlay_name
        tooltip_changed = (tooltip_title != self._tooltip_title
                           or tooltip_text != self._tooltip_text)
        status_changed = status != self._status

        self._icon_name = icon_name
        self._overlay_name = overlay_name
        self._tooltip_title = tooltip_title
        self._tooltip_text = tooltip_text
        self._status = status
        if icon_changed:
            self._icon_pixmaps = _headset_pixmaps()
        if overlay_changed:
            self._overlay_pixmaps = []

        if icon_changed or overlay_changed:
            self._emit("NewIcon")
            self._emit("NewOverlayIcon")
        if tooltip_changed:
            self._emit("NewToolTip")
        if status_changed:
            self._emit("NewStatus", GLib.Variant("(s)", (status,)))

    def _emit(self, signal, params=None):
        if self._conn:
            self._conn.emit_signal(None, ITEM_PATH, ITEM_IFACE, signal, params)

    # -- StatusNotifierItem D-Bus interface -----------------------------------

    def _item_get(self, connection, sender, path, iface, prop):
        values = {
            "Category": GLib.Variant("s", "Hardware"),
            "Id": GLib.Variant("s", "blackshark-control"),
            "Title": GLib.Variant("s", "BlackShark Control"),
            "Status": GLib.Variant("s", self._status),
            "WindowId": GLib.Variant("i", 0),
            "IconThemePath": GLib.Variant("s", ""),
            "IconName": GLib.Variant("s", self._icon_name),
            "IconPixmap": GLib.Variant("a(iiay)", self._icon_pixmaps),
            "OverlayIconName": GLib.Variant("s", self._overlay_name),
            "OverlayIconPixmap": GLib.Variant("a(iiay)", self._overlay_pixmaps),
            "AttentionIconName": GLib.Variant("s", ""),
            "AttentionIconPixmap": GLib.Variant("a(iiay)", []),
            "AttentionMovieName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", (
                "audio-headset", [], self._tooltip_title, self._tooltip_text)),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
        }
        return values.get(prop)

    def _item_call(self, connection, sender, path, iface, method, params, invocation):
        if method == "Activate":
            x, y = params.unpack()
            token, self._activation_token = self._activation_token, None
            GLib.idle_add(self._activate, x, y, token)
        elif method == "ProvideXDGActivationToken":
            self._activation_token = params.unpack()[0]
        # SecondaryActivate, ContextMenu and Scroll intentionally do nothing:
        # Plasma shows the DBusMenu for right click, and left click activates.
        invocation.return_value(None)

    def _activate(self, x, y, token=None):
        self._on_activate_cb(x, y, token)
        return False

    # -- DBusMenu -------------------------------------------------------------

    MENU_ITEMS = {
        0: {"children-display": "submenu"},
        1: {"label": "Open BlackShark Control", "icon-name": "audio-headset"},
        2: {"type": "separator"},
        3: {"label": "Quit", "icon-name": "application-exit"},
    }
    MENU_CHILDREN = {0: [1, 2, 3]}

    def _menu_layout(self, parent_id=0):
        if parent_id not in self.MENU_ITEMS:
            return (parent_id, {}, [])
        props = self.MENU_ITEMS[parent_id]
        children = [self._menu_layout(child)
                    for child in self.MENU_CHILDREN.get(parent_id, [])]
        return (parent_id, props, children)

    def _menu_get(self, connection, sender, path, iface, prop):
        if prop == "Version":
            return GLib.Variant("u", 3)
        if prop == "TextDirection":
            return GLib.Variant("s", "ltr")
        if prop == "Status":
            return GLib.Variant("s", "normal")
        if prop == "IconThemePath":
            return GLib.Variant("as", [])
        return None

    @staticmethod
    def _variant_props(props):
        return {key: GLib.Variant("b", True) if value is True else
                GLib.Variant("s", value)
                for key, value in props.items()}

    def _layout_variant(self, layout):
        item_id, props, children = layout
        props = self._variant_props(props)
        child_variants = [GLib.Variant("(ia{sv}av)", self._layout_variant(c))
                          for c in children]
        return (item_id, props, child_variants)

    def _menu_call(self, connection, sender, path, iface, method, params, invocation):
        if method == "GetLayout":
            parent_id, depth = params.unpack()
            layout = self._layout_variant(self._menu_layout(parent_id))
            invocation.return_value(GLib.Variant(
                "(u(ia{sv}av))", (self._revision, layout)))
        elif method == "GetGroupProperties":
            ids, names = params.unpack()
            items = []
            for item_id in ids:
                item = self._menu_layout(item_id)
                if item_id == item[0]:
                    props = self._variant_props(item[1])
                    if names:
                        props = {k: v for k, v in props.items() if k in names}
                    items.append((item_id, props))
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (items,)))
        elif method == "GetProperty":
            item_id, name = params.unpack()
            item = self._menu_layout(item_id)
            props = self._variant_props(item[1]) if item_id == item[0] else {}
            value = props.get(name, GLib.Variant("s", ""))
            invocation.return_value(GLib.Variant("(v)", (value,)))
        elif method == "Event":
            item_id, event, data, timestamp = params.unpack()
            if event == "clicked":
                self._menu_clicked(item_id)
            invocation.return_value(None)
        elif method == "EventGroup":
            events = params.unpack()[0]
            errors = []
            for item_id, event, data, timestamp in events:
                if event == "clicked":
                    self._menu_clicked(item_id)
                else:
                    errors.append(item_id)
            invocation.return_value(GLib.Variant("(ai)", (errors,)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_value(None)

    def _menu_clicked(self, item_id):
        if item_id == 1:
            GLib.idle_add(self._open)
        elif item_id == 3:
            GLib.idle_add(self._quit)

    def _open(self):
        token, self._activation_token = self._activation_token, None
        self._on_open_cb(token)
        return False

    def _quit(self):
        self._on_quit_cb()
        return False
