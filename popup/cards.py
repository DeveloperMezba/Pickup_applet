# -*- coding: utf-8 -*-
"""Item card widgets for the Pickup popup.

Two presentations of the same data:

  GridCard  -- large thumbnail/icon tile, used by the grid layout.
  ListRow   -- compact row with a small icon plus name, size and badge.

Both expose the same small surface so the window code does not care which is
mounted:

    widget            the Gtk.Widget to pack
    item              the backing dict
    set_selected(b)   reflect selection state
    refresh()         re-read name/size/missing state

Drag-out is armed through ``enable_drag(payload_provider)``. The provider
resolves the *current* drag set at drag time, so "selected items drag together"
works without rebuilding widgets. Nothing here opens files: cards are press and
drag targets only. The only click behaviour is the selection checkbox.
"""

import os

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk, Pango  # noqa: E402

import thumbnails  # noqa: E402
from store import KIND_TEXT, human_size, item_size, path_to_uri  # noqa: E402

# Offer URIs first: file managers and browsers prefer it. text/plain is the
# fallback that makes dragging into text fields work.
DRAG_TARGETS = [
    Gtk.TargetEntry.new("text/uri-list", 0, 0),
    Gtk.TargetEntry.new("text/plain;charset=utf-8", 0, 1),
    Gtk.TargetEntry.new("text/plain", 0, 2),
]
# Text items have no URI, so they must not advertise text/uri-list at all
# (otherwise a drop target may pick it and receive nothing).
TEXT_DRAG_TARGETS = [
    Gtk.TargetEntry.new("text/plain;charset=utf-8", 0, 1),
    Gtk.TargetEntry.new("text/plain", 0, 2),
    Gtk.TargetEntry.new("UTF8_STRING", 0, 1),
]


def _escape(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def ellipsize(text, limit=60):
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "\u2026"


def display_name(item):
    if item.get("kind") == KIND_TEXT:
        return ellipsize(item.get("preview") or "(empty)", 46)
    path = item.get("path") or ""
    name = os.path.basename(path.rstrip("/")) or path
    return ellipsize(name, 46)


def subtitle(item):
    """Second line: size for files, character count for text."""
    if item.get("kind") == KIND_TEXT:
        label = "%s chars" % format(item.get("chars") or 0, ",")
        if item.get("truncated"):
            label += " \u00b7 trimmed"
        return label
    if item.get("missing"):
        return "missing \u00b7 " + (os.path.basename(item.get("path") or "") or "file")
    if item.get("is_dir"):
        return "folder"
    size = item_size(item)
    return human_size(size) if size is not None else "file"


def tooltip(item):
    if item.get("kind") == KIND_TEXT:
        preview = item.get("preview") or ""
        return _escape(preview) if preview else "Text item"
    return _escape(item.get("path") or "")


def drag_payload(item, store):
    """(uris, text) for a single item dragged out of the shelf."""
    if item.get("kind") == KIND_TEXT:
        return [], store.get_text(item)
    uri = item.get("uri") or path_to_uri(item.get("path") or "")
    return ([uri] if uri else []), uri


class _CardBase:
    """Shared selection, context-menu and drag-source behaviour."""

    def __init__(self, item, store, on_context, on_select_changed):
        self.item = item
        self.store = store
        self.on_context = on_context
        self.on_select_changed = on_select_changed
        self.selected = False
        self._silent_check = True
        self._drag_active = False
        self.on_drag_state = None
        self._payload_provider = None

    # -- drag-out ------------------------------------------------------------
    def enable_drag(self, payload_provider):
        """Arm drag-out on ``self.widget``.

        ``payload_provider(item)`` returns (uris, text) for a drag starting on
        this card. It is called at drag-begin so multi-select is resolved using
        the live selection rather than the state at build time.
        """
        self._payload_provider = payload_provider
        targets = TEXT_DRAG_TARGETS if self.item.get("kind") == KIND_TEXT else DRAG_TARGETS
        self.widget.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK, targets, Gdk.DragAction.COPY
        )
        # Re-assert the target list: drag_source_set() alone can leave the
        # default text targets in place depending on GTK version.
        self.widget.drag_source_set_target_list(targets)
        self.widget.connect("drag-begin", self._on_drag_begin)
        self.widget.connect("drag-data-get", self._on_drag_data_get)
        self.widget.connect("drag-end", self._on_drag_end)

    def _on_drag_begin(self, widget, context):
        # The drag icon is supplied by GTK from the widget itself; dim the source
        # slightly so it is obvious the drag started.
        self._drag_active = True
        if self.on_drag_state:
            self.on_drag_state(True)

    def _on_drag_data_get(self, widget, context, selection, info, when):
        if not self._payload_provider:
            return
        uris, text = self._payload_provider(self.item)
        if info == 0 and uris:
            selection.set_uris(uris)
        else:
            selection.set_text(text or "", -1)

    def _on_drag_end(self, widget, context):
        self._drag_active = False
        if self.on_drag_state:
            self.on_drag_state(False)

    # -- selection -----------------------------------------------------------
    def set_selected(self, selected):
        self.selected = bool(selected)
        self._sync_selection_style()
        self._silent_check = True
        self.check.set_active(self.selected)
        self._silent_check = False

    def _on_check_toggled(self, check):
        if self._silent_check:
            return
        self.selected = check.get_active()
        self._sync_selection_style()
        if self.on_select_changed:
            self.on_select_changed(self, self.selected)

    def _sync_selection_style(self):
        context = self.widget.get_style_context()
        if self.selected:
            context.add_class("pickup-selected")
        else:
            context.remove_class("pickup-selected")

    # -- pointer -------------------------------------------------------------
    def _on_press(self, widget, event):
        if event.button == 3:
            if self.on_context:
                self.on_context(self, event)
            return True
        return False

    # -- shared refresh ------------------------------------------------------
    def _refresh_labels(self, name_label, meta_label):
        name_label.set_text(display_name(self.item))
        name_label.set_tooltip_markup(tooltip(self.item))
        meta_label.set_text(subtitle(self.item))
        self.pin.set_visible(bool(self.item.get("pinned")))

    def _refresh_state_styles(self, labels=()):
        context = self.widget.get_style_context()
        missing = bool(self.item.get("missing"))
        if missing:
            context.add_class("pickup-missing")
        else:
            context.remove_class("pickup-missing")
        for label in labels:
            label_context = label.get_style_context()
            if missing:
                label_context.add_class("pickup-missing-label")
            else:
                label_context.remove_class("pickup-missing-label")


class GridCard(_CardBase):
    """Tile with a thumbnail (or extension badge) and a two-line caption."""

    THUMB = 104
    WIDTH = 152

    def __init__(self, item, store, on_context, on_select_changed):
        super().__init__(item, store, on_context, on_select_changed)
        self.widget = Gtk.EventBox()
        self.widget.set_visible_window(False)
        self.widget.get_style_context().add_class("pickup-card")
        self.widget.set_size_request(self.WIDTH, -1)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        overlay = Gtk.Overlay()
        self._thumb_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._thumb_holder.set_halign(Gtk.Align.CENTER)
        self._thumb_holder.set_valign(Gtk.Align.CENTER)
        self._thumb_holder.set_size_request(self.WIDTH - 20, self.THUMB)
        overlay.add(self._thumb_holder)

        self.check = Gtk.CheckButton()
        self.check.get_style_context().add_class("pickup-check")
        self.check.set_halign(Gtk.Align.START)
        self.check.set_valign(Gtk.Align.START)
        self.check.set_margin_top(3)
        self.check.set_margin_start(3)
        self.check.set_tooltip_text("Select")
        self.check.connect("toggled", self._on_check_toggled)
        overlay.add_overlay(self.check)

        self.pin = Gtk.Image.new_from_icon_name("view-pin-symbolic", Gtk.IconSize.MENU)
        self.pin.get_style_context().add_class("pickup-pin")
        self.pin.set_halign(Gtk.Align.END)
        self.pin.set_valign(Gtk.Align.START)
        self.pin.set_margin_top(6)
        self.pin.set_margin_end(6)
        self.pin.set_no_show_all(True)
        overlay.add_overlay(self.pin)

        outer.pack_start(overlay, False, False, 8)

        self.name_label = Gtk.Label()
        self.name_label.get_style_context().add_class("pickup-name")
        self.name_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.name_label.set_xalign(0.0)
        self.name_label.set_line_wrap(False)
        self.name_label.set_max_width_chars(1)

        self.meta_label = Gtk.Label()
        self.meta_label.get_style_context().add_class("pickup-meta")
        self.meta_label.set_xalign(0.0)
        self.meta_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.meta_label.set_max_width_chars(1)

        caption = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        caption.set_margin_start(9)
        caption.set_margin_end(9)
        caption.set_margin_bottom(8)
        caption.pack_start(self.name_label, False, False, 0)
        caption.pack_start(self.meta_label, False, False, 0)
        outer.pack_start(caption, False, False, 0)

        self.widget.add(outer)
        self.widget.connect("button-press-event", self._on_press)
        self._render_thumb()
        self._silent_check = False
        self.refresh()

    def _render_thumb(self):
        for child in self._thumb_holder.get_children():
            self._thumb_holder.remove(child)
        pixbuf = None
        if thumbnails.has_source(self.item) and not self.item.get("missing"):
            pixbuf = thumbnails.cached_thumbnail(self.item)
            if pixbuf is None:
                self._request_thumb()
        if pixbuf is not None:
            image = Gtk.Image.new_from_pixbuf(pixbuf)
            image.get_style_context().add_class("pickup-thumb")
            self._thumb_holder.pack_start(image, True, False, 0)
            return
        if self.item.get("kind") == KIND_TEXT:
            self._thumb_holder.pack_start(
                self._make_glyph("text-x-generic-symbolic", "TEXT"), True, True, 0
            )
        elif self.item.get("is_dir"):
            self._thumb_holder.pack_start(
                self._make_glyph("folder-symbolic", "DIR"), True, True, 0
            )
        else:
            label = thumbnails.extension_label(self.item.get("path") or "")
            self._thumb_holder.pack_start(
                self._make_glyph("x-office-document-symbolic", label), True, True, 0
            )

    def _request_thumb(self):
        if getattr(self, "_thumb_requested", False):
            return
        self._thumb_requested = True

        def ready(pixbuf):
            # The card may have been rebuilt (layout switch) while we waited.
            if pixbuf is None or not self._thumb_holder.get_realized() and not self._thumb_holder.get_parent():
                return
            try:
                self._render_thumb()
                self._thumb_holder.show_all()
            except Exception:
                pass

        thumbnails.request_thumbnail(self.item, ready)

    def _make_glyph(self, icon_name, label):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.get_style_context().add_class("pickup-thumb-empty")
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_size_request(76, 76)
        icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DIALOG)
        icon.get_style_context().add_class("pickup-ext-dim")
        box.pack_start(icon, True, True, 0)
        if label:
            text = Gtk.Label(label=label)
            text.get_style_context().add_class("pickup-ext")
            text.set_ellipsize(Pango.EllipsizeMode.END)
            text.set_max_width_chars(7)
            box.pack_start(text, False, False, 0)
        return box

    def refresh(self):
        self._refresh_labels(self.name_label, self.meta_label)
        # Only the meta line gets the red "missing" treatment; the name is
        # greyed out through the .pickup-missing .pickup-name CSS rule.
        self._refresh_state_styles((self.meta_label,))
        self._silent_check = True
        self.check.set_active(self.selected)
        self._silent_check = False


class ListRow(_CardBase):
    """Compact row: icon, name, subtitle, pin, checkbox."""

    ICON = 38

    def __init__(self, item, store, on_context, on_select_changed):
        super().__init__(item, store, on_context, on_select_changed)
        self.widget = Gtk.EventBox()
        self.widget.set_visible_window(False)
        context = self.widget.get_style_context()
        context.add_class("pickup-card")
        context.add_class("pickup-row")

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        row.set_margin_top(5)
        row.set_margin_bottom(5)
        row.set_margin_start(8)
        row.set_margin_end(6)

        self._icon_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._icon_holder.set_size_request(self.ICON, self.ICON)
        self._icon_holder.set_valign(Gtk.Align.CENTER)
        row.pack_start(self._icon_holder, False, False, 0)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text_box.set_valign(Gtk.Align.CENTER)
        self.name_label = Gtk.Label()
        self.name_label.get_style_context().add_class("pickup-name")
        self.name_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.name_label.set_xalign(0.0)
        self.name_label.set_max_width_chars(1)
        self.meta_label = Gtk.Label()
        self.meta_label.get_style_context().add_class("pickup-meta")
        self.meta_label.set_xalign(0.0)
        self.meta_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.meta_label.set_max_width_chars(1)
        text_box.pack_start(self.name_label, False, False, 0)
        text_box.pack_start(self.meta_label, False, False, 0)
        row.pack_start(text_box, True, True, 0)

        self.check = Gtk.CheckButton()
        self.check.get_style_context().add_class("pickup-check")
        self.check.set_valign(Gtk.Align.CENTER)
        self.check.set_tooltip_text("Select")
        self.check.connect("toggled", self._on_check_toggled)

        self.pin = Gtk.Image.new_from_icon_name("view-pin-symbolic", Gtk.IconSize.MENU)
        self.pin.get_style_context().add_class("pickup-pin")
        self.pin.set_valign(Gtk.Align.CENTER)
        self.pin.set_no_show_all(True)

        row.pack_end(self.check, False, False, 0)
        row.pack_end(self.pin, False, False, 2)

        self.widget.add(row)
        self.widget.connect("button-press-event", self._on_press)
        self._render_icon()
        self._silent_check = False
        self.refresh()

    def _render_icon(self):
        for child in self._icon_holder.get_children():
            self._icon_holder.remove(child)
        pixbuf = None
        if thumbnails.has_source(self.item) and not self.item.get("missing"):
            pixbuf = thumbnails.cached_thumbnail(self.item)
            if pixbuf is None:
                self._request_icon()
        if pixbuf is not None:
            image = Gtk.Image.new_from_pixbuf(pixbuf)
            image.set_size_request(self.ICON, self.ICON)
            self._icon_holder.pack_start(image, True, True, 0)
            return
        icon_name = "x-office-document-symbolic"
        if self.item.get("kind") == KIND_TEXT:
            icon_name = "text-x-generic-symbolic"
        elif self.item.get("is_dir"):
            icon_name = "folder-symbolic"
        else:
            mime = thumbnails.item_mime(self.item)
            if mime.startswith("image/"):
                icon_name = "image-x-generic-symbolic"
            elif mime.startswith("video/"):
                icon_name = "video-x-generic-symbolic"
            elif mime.startswith("audio/"):
                icon_name = "audio-x-generic-symbolic"
        image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DND)
        if self.item.get("missing"):
            image.set_opacity(0.4)
        self._icon_holder.pack_start(image, True, True, 0)

    def _request_icon(self):
        if getattr(self, "_thumb_requested", False):
            return
        self._thumb_requested = True

        def ready(pixbuf):
            if pixbuf is None or self._icon_holder.get_parent() is None:
                return
            try:
                self._render_icon()
                self._icon_holder.show_all()
            except Exception:
                pass

        thumbnails.request_thumbnail(self.item, ready)

    def refresh(self):
        self._refresh_labels(self.name_label, self.meta_label)
        self._refresh_state_styles((self.meta_label,))
        self._silent_check = True
        self.check.set_active(self.selected)
        self._silent_check = False
