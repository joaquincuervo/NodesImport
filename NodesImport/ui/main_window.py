import re
import typing

# ---------------------------------------------------------------------------
# Qt compatibility — supports PySide2 (Nuke ≤15) and PySide6 (Nuke 16+)
# ---------------------------------------------------------------------------
try:
    from PySide6 import QtWidgets, QtCore, QtGui  # type: ignore
    from PySide6 import QtGui as _QtGui_compat      # noqa: F401
    _PYSIDE_VERSION = 6
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui  # type: ignore
    _PYSIDE_VERSION = 2
from NodesImport.core.parsing import parse_nuke_script  # type: ignore
from NodesImport.ui.graph_view import GraphView, NodeItem, DotItem, BackdropItem, _NULL_INPUT  # type: ignore

try:
    import nuke  # type: ignore
except ImportError:
    nuke = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_nuke_main_window() -> typing.Optional[QtWidgets.QWidget]:
    if not nuke:
        return None
    try:
        p = nuke.thisParent()
        if p is not None:
            return p  # type: ignore[return-value]
    except Exception:
        pass
    try:
        active = QtWidgets.QApplication.activeWindow()
        if active is not None:
            return active
    except Exception:
        pass
    try:
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if isinstance(widget, QtWidgets.QMainWindow):
                return widget
    except Exception:
        pass
    return None


def _parse_root_info(filepath: str) -> typing.Dict[str, str]:
    """
    Extract project metadata from the Root block of a .nk file.
    Returns a dict; missing fields return "—".
    """
    info: typing.Dict[str, str] = {
        "resolution":       "—",
        "proxy_resolution": "—",
        "frame_range":      "—",
        "fps":              "—",
        "color_management": "—",
        "working_space":    "—",
    }
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            content = f.read()

        root_m = re.search(r"^Root\s*\{(.*?)\n\}", content, re.MULTILINE | re.DOTALL)
        if not root_m:
            return info
        root = root_m.group(1)

        # Resolution — format "W H x0 y0 W H PAR [label]"
        fmt_m = re.search(r'^\s*format\s+"?(\d+)\s+(\d+)', root, re.MULTILINE)
        if fmt_m:
            info["resolution"] = f"{fmt_m.group(1)} × {fmt_m.group(2)}"

        # Proxy resolution
        pfmt_m = re.search(r'^\s*proxy_format\s+"?(\d+)\s+(\d+)', root, re.MULTILINE)
        if pfmt_m:
            info["proxy_resolution"] = f"{pfmt_m.group(1)} × {pfmt_m.group(2)}"

        # Frame range
        first_m = re.search(r"^\s*first_frame\s+(\d+)", root, re.MULTILINE)
        last_m  = re.search(r"^\s*last_frame\s+(\d+)",  root, re.MULTILINE)
        if first_m and last_m:
            first = int(first_m.group(1))
            last  = int(last_m.group(1))
            info["frame_range"] = f"{first} – {last}  ({last - first + 1} frames)"
        elif first_m:
            info["frame_range"] = f"from {first_m.group(1)}"
        elif last_m:
            info["frame_range"] = f"to {last_m.group(1)}"

        # FPS — Nuke omits this knob when the project uses the default (24 fps).
        fps_m = re.search(r"^\s*fps\s+([\d.]+)", root, re.MULTILINE)
        if fps_m:
            info["fps"] = f"{fps_m.group(1)} fps"
        else:
            info["fps"] = "24 fps"

        # Color management
        cm_m = re.search(r"^\s*colorManagement\s+(\S+)", root, re.MULTILINE)
        if cm_m:
            info["color_management"] = cm_m.group(1)

        # Working space
        ws_m = re.search(r"^\s*workingSpaceLUT\s+(\S+)", root, re.MULTILINE)
        if ws_m:
            info["working_space"] = ws_m.group(1)

    except Exception:
        pass
    return info


def _node_search_text(node: typing.Any) -> str:
    """
    Build the searchable text corpus for a node:
    node name + node type + label value (HTML-stripped) + note text.
    """
    parts = [node.name, node.node_type]

    label_m = re.search(r"^\s*label\s+(.+)", node.content, re.MULTILINE)
    if label_m:
        raw   = label_m.group(1).strip().strip('"').strip("'")
        clean = re.sub(r"<[^>]+>", "", raw)
        clean = clean.replace("\\n", " ").replace("\\t", " ").strip()
        if clean:
            parts.append(clean)

    note_m = re.search(r"^\s*note\s+(.+)", node.content, re.MULTILINE)
    if note_m:
        parts.append(note_m.group(1).strip().strip('"'))

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Info panel
# ---------------------------------------------------------------------------

class InfoPanel(QtWidgets.QFrame):
    """
    Floating script-info panel anchored to the bottom-right.
    Shown on hover of the ⓘ button; hidden when cursor leaves both
    the button and the panel itself (with a short grace delay).
    """

    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        # Mouse tracking so leaveEvent fires reliably
        self.setMouseTracking(True)
        self.setObjectName("InfoPanel")
        self.setStyleSheet("""
            QFrame#InfoPanel {
                background-color: #1e1e1e;
                border: 1px solid #444;
                border-radius: 6px;
            }
            QLabel { background: transparent; }
        """)
        self.setFixedWidth(240)
        self.hide()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)

        title = QtWidgets.QLabel("Script Info")
        title.setStyleSheet("color: #fff; font-weight: bold; font-size: 11px;")
        layout.addWidget(title)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setStyleSheet("QFrame { color: #444; }")
        layout.addWidget(sep)

        self._rows: typing.Dict[str, QtWidgets.QLabel] = {}
        fields = [
            ("resolution",  "Resolution"),
            ("frame_range", "Frame Range"),
            ("fps",         "FPS"),
        ]
        for key, label_text in fields:
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(6)
            lbl = QtWidgets.QLabel(f"{label_text}:")
            lbl.setStyleSheet("color: #888; font-size: 10px;")
            lbl.setFixedWidth(88)
            val = QtWidgets.QLabel("—")
            val.setStyleSheet("color: #ddd; font-size: 10px;")
            val.setWordWrap(True)
            row.addWidget(lbl)
            row.addWidget(val, 1)
            layout.addLayout(row)
            self._rows[key] = val

    def update_info(self, info: typing.Dict[str, str]) -> None:
        for key, lbl in self._rows.items():
            lbl.setText(info.get(key, "—"))
        self.adjustSize()


# ---------------------------------------------------------------------------
# Custom checkbox that shows ✕ when checked instead of a coloured fill
# ---------------------------------------------------------------------------

class _XCheckBox(QtWidgets.QCheckBox):
    """
    Checkbox that draws a plain ✕ inside the indicator when checked.
    Uses paintEvent to draw directly — no image/SVG loading required.
    """

    def __init__(self, text: str = "", parent: typing.Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setStyleSheet(
            "QCheckBox {"
            "  color: #aaa;"
            "  font-size: 10px;"
            "  spacing: 8px;"
            "}"
            "QCheckBox:hover { color: #fff; }"
            # Hide the native indicator — we draw our own in paintEvent
            "QCheckBox::indicator { width: 0px; height: 0px; }"
        )

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        # Draw the text label via Qt's own style engine,
        # but shift its rect right to clear our custom indicator box.
        opt = QtWidgets.QStyleOptionButton()
        self.initStyleOption(opt)
        indicator_space = 14 + 8  # box width + spacing
        opt.rect = self.rect().adjusted(indicator_space, 0, 0, 0)
        p = QtWidgets.QStylePainter(self)
        p.drawControl(QtWidgets.QStyle.CE_CheckBoxLabel, opt)

        # Draw our own 14×14 indicator box on the left
        box = 14
        x   = 0
        y   = (self.height() - box) // 2

        p.setRenderHint(QtGui.QPainter.Antialiasing)

        # Box background + border
        p.setPen(QtGui.QPen(QtGui.QColor("#666"), 1))
        p.setBrush(QtGui.QBrush(QtGui.QColor("#2a2a2a")))
        p.drawRoundedRect(x, y, box, box, 2, 2)

        if self.isChecked():
            # Draw ✕ — two diagonal lines
            margin = 3
            p.setPen(QtGui.QPen(QtGui.QColor("#cccccc"), 1.8,
                                QtCore.Qt.SolidLine,
                                QtCore.Qt.RoundCap))
            p.drawLine(x + margin,       y + margin,
                       x + box - margin, y + box - margin)
            p.drawLine(x + box - margin, y + margin,
                       x + margin,       y + box - margin)

    def sizeHint(self) -> QtCore.QSize:
        sh = super().sizeHint()
        # Add room for our custom indicator (14px) + spacing (8px)
        return QtCore.QSize(sh.width() + 22, max(sh.height(), 20))


# ---------------------------------------------------------------------------
# Shortcuts panel
# ---------------------------------------------------------------------------

class ShortcutsPanel(QtWidgets.QFrame):
    """Floating panel showing all keyboard and mouse shortcuts."""

    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("ShortcutsPanel")
        self.setStyleSheet("""
            QFrame#ShortcutsPanel {
                background-color: #1e1e1e;
                border: 1px solid #444;
                border-radius: 6px;
            }
            QLabel { background: transparent; }
        """)
        self.setFixedWidth(320)
        self.hide()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(4)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Shortcuts")
        title.setStyleSheet("color:#fff; font-weight:bold; font-size:11px;")
        close_btn = QtWidgets.QPushButton("✕")
        close_btn.setFixedSize(18, 18)
        close_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#777;border:none;font-size:11px;}"
            "QPushButton:hover{color:#fff;}"
        )
        close_btn.clicked.connect(self.hide)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(close_btn)
        layout.addLayout(header)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setStyleSheet("QFrame{color:#444;}")
        layout.addWidget(sep)

        def _section(text: str) -> QtWidgets.QLabel:
            lbl = QtWidgets.QLabel(text)
            lbl.setStyleSheet("color:#888; font-size:9px; font-weight:bold;"
                              "margin-top:6px; margin-bottom:2px;")
            return lbl

        def _row(key: str, action: str) -> QtWidgets.QHBoxLayout:
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(8)
            k = QtWidgets.QLabel(key)
            k.setStyleSheet(
                "color:#ddd; font-size:10px; background:#2e2e2e;"
                "border:1px solid #555; border-radius:3px;"
                "padding:1px 6px; font-family:monospace;"
            )
            k.setFixedWidth(130)
            v = QtWidgets.QLabel(action)
            v.setStyleSheet("color:#999; font-size:10px;")
            row.addWidget(k)
            row.addWidget(v, 1)
            return row

        layout.addWidget(_section("NAVIGATION"))
        for key, action in [
            ("F",               "Zoom to selection / all"),
            ("Alt + E",         "Toggle expression / clone links"),
        ]:
            layout.addLayout(_row(key, action))

        layout.addWidget(_section("SELECTION"))
        for key, action in [
            ("Ctrl + A",        "Select all"),
        ]:
            layout.addLayout(_row(key, action))

        layout.addWidget(_section("ACTIONS"))
        for key, action in [
            ("Enter",           "Import selected nodes"),
            ("Ctrl + F",        "Open search"),
            ("Esc",             "Close search"),
            ("Shift + I",       "Open Nodes Import"),
        ]:
            layout.addLayout(_row(key, action))

        layout.addWidget(_section("TABS"))
        for key, action in [
            ("Ctrl + T",          "New tab"),
            ("Ctrl + W",          "Close tab"),
            ("Ctrl + R",          "Rename tab"),
            ("Ctrl + Shift + T",  "Reopen last closed tab"),
            ("Ctrl + Tab",        "Next tab"),
            ("Ctrl + Shift + Tab","Previous tab"),
        ]:
            layout.addLayout(_row(key, action))


# ---------------------------------------------------------------------------
# Settings panel
# ---------------------------------------------------------------------------

class SettingsPanel(QtWidgets.QFrame):
    """Floating settings panel dropped down from the ⚙ button."""

    def __init__(self, parent: QtWidgets.QWidget) -> None:
        # Qt.Popup: floats above all widgets, auto-closes on outside click
        super().__init__(parent)
        self.setObjectName("SettingsPanel")
        self.setStyleSheet("""
            QFrame#SettingsPanel {
                background-color: #1e1e1e;
                border: 1px solid #444;
                border-radius: 6px;
            }
            QLabel { background: transparent; }
        """)
        self.setFixedWidth(260)
        self.hide()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Settings")
        title.setStyleSheet("color: #fff; font-weight: bold; font-size: 11px;")
        close_btn = QtWidgets.QPushButton("✕")
        close_btn.setFixedSize(18, 18)
        close_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#777;border:none;font-size:11px;}"
            "QPushButton:hover{color:#fff;}"
        )
        close_btn.clicked.connect(self.hide)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(close_btn)
        layout.addLayout(header)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setStyleSheet("QFrame { color: #444; }")
        layout.addWidget(sep)

        # ── Close after import checkbox (X marker when checked) ─────────────
        self.chk_close_after_import = _XCheckBox("Close window after importing")
        layout.addWidget(self.chk_close_after_import)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        """When the popup is dismissed (click outside), uncheck the gear button."""
        super().hideEvent(event)
        # Walk up to find the main window and uncheck btn_settings
        p = self.parent()
        while p is not None:
            if hasattr(p, "btn_settings"):
                p.btn_settings.setChecked(False)
                break
            p = p.parent() if hasattr(p, "parent") else None

    @property
    def close_after_import(self) -> bool:
        return self.chk_close_after_import.isChecked()

    @close_after_import.setter
    def close_after_import(self, value: bool) -> None:
        self.chk_close_after_import.setChecked(value)


# ---------------------------------------------------------------------------
# Central widget — closes SettingsPanel on outside click
# ---------------------------------------------------------------------------

# (No _CentralWidget subclass needed — outside-click handled via eventFilter)



# ---------------------------------------------------------------------------
# _ScriptTab  — holds the state for a single tab
# ---------------------------------------------------------------------------

import dataclasses as _dc

@_dc.dataclass
class _ScriptTab:
    """All per-tab state so switching tabs fully restores where you left off."""
    title:      str                          = "New Tab"
    filepath:   typing.Optional[str]         = None
    nodes:      typing.List[typing.Any]      = _dc.field(default_factory=list)
    root_info:  typing.Dict[str, str]        = _dc.field(default_factory=dict)
    # GraphView scene state is kept alive by storing the QGraphicsScene itself
    scene:      typing.Optional[object]      = None   # QtWidgets.QGraphicsScene
    # Viewport position: scene center point + zoom level.
    # This avoids the setTransform/setScene timing bug — we restore via
    # centerOn() + scale() which don't fight Qt's internal viewport updates.
    view_center: typing.Optional[object]     = None   # QtCore.QPointF
    zoom_level:  float                       = 1.0
    # Set to True once the user has manually zoomed/panned in this tab.
    # Until then, switching back always calls frame_all so content is visible.
    user_navigated: bool = False
    # Search state
    search_results: typing.List[object]      = _dc.field(default_factory=list)
    search_index:   int                      = -1
    # User-defined display name (None = use auto-generated title from filename)
    custom_name: typing.Optional[str]        = None


# ---------------------------------------------------------------------------
# Helper event filter for tab rename inline editing
# ---------------------------------------------------------------------------

class _TabRenameFilter(QtCore.QObject):
    """Event filter that commits on FocusOut and cancels on Escape."""

    def __init__(
        self,
        edit: QtWidgets.QLineEdit,
        finish: typing.Callable,
        cancel: typing.Callable,
    ) -> None:
        super().__init__(edit)
        self._finish = finish
        self._cancel = cancel

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() == QtCore.QEvent.FocusOut:
            self._finish()
            return True
        if event.type() == QtCore.QEvent.KeyPress:
            if event.key() == QtCore.Qt.Key_Escape:
                self._cancel()
                return True
        return False


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class NodesImportWindow(QtWidgets.QMainWindow):

    def __init__(self) -> None:
        # No parent — this gives the window its own taskbar entry and
        # prevents it from hiding behind Nuke's windows. Matches the
        # behavior of a plain QWidget() which was confirmed to work:
        # shows in taskbar, minimizes/restores correctly, doesn't float
        # over other apps after Alt+Tab.
        super().__init__()
        self.setWindowTitle("Nodes Import")
        self.resize(1100, 720)

        # Remove tooltip stylesheet — let Nuke's global palette handle it

        self._nodes:          typing.List[typing.Any]          = []
        self._search_results: typing.List[QtWidgets.QGraphicsItem] = []
        self._search_index:   int                              = -1
        self._root_info:      typing.Dict[str, str]            = {}

        # ── Central widget ────────────────────────────────────────────────────
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        self._main_layout = QtWidgets.QVBoxLayout(central)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        # ── Toolbar ───────────────────────────────────────────────────────────
        toolbar = QtWidgets.QWidget()
        toolbar.setFixedHeight(40)
        toolbar.setStyleSheet("background-color:#2b2b2b; QToolTip{background-color:#ffffdc;color:#000000;border:1px solid #c0c060;padding:4px;}")
        tb = QtWidgets.QHBoxLayout(toolbar)
        tb.setContentsMargins(6, 4, 6, 4)
        tb.setSpacing(4)

        self.btn_recent = QtWidgets.QPushButton("🕐")
        self.btn_recent.setFixedSize(32, 30)
        self.btn_recent.setToolTip("Recent scripts")
        self.btn_recent.setStyleSheet(
            "QPushButton{background:#3a3a3a;color:#aaa;border:1px solid #555;"
            "border-radius:3px;font-size:14px;}"
            "QPushButton:hover{background:#4a4a4a;color:#fff;}"
            "QPushButton:menu-indicator{width:0px;}"
        )
        self._recent_menu = QtWidgets.QMenu(self)
        self._recent_menu.setStyleSheet(
            "QMenu{background:#2a2a2a;color:#ccc;border:1px solid #444;"
            "border-radius:4px;padding:4px 0px;}"
            "QMenu::item{padding:5px 16px;font-size:10px;}"
            "QMenu::item:selected{background:#3a3a3a;color:#fff;}"
            "QMenu::item:disabled{color:#555;}"
            "QMenu::separator{height:1px;background:#444;margin:4px 0px;}"
        )
        self.btn_recent.setMenu(self._recent_menu)
        self.btn_recent.clicked.connect(self._show_recent_menu)

        self.btn_select = QtWidgets.QPushButton("Select Script (.nk / .nk~)")
        self.btn_select.setToolTip("Open a Nuke script to inspect and restore nodes from  (Ctrl+T)")
        self.btn_select.setStyleSheet(
            "QPushButton{background:#3a3a3a;color:#ccc;border:1px solid #555;"
            "border-radius:3px;padding:3px 12px;}"
            "QPushButton:hover{background:#4a4a4a;color:#fff;}"
        )
        self.btn_select.clicked.connect(self.on_select_script)

        self.btn_import = QtWidgets.QPushButton("Import Selected Nodes")
        self.btn_import.setToolTip("Paste the selected nodes into the active Nuke session  (Enter)")
        self.btn_import.setStyleSheet(
            "QPushButton{background:#2e5a2e;color:#fff;font-weight:bold;"
            "border:none;border-radius:3px;padding:3px 12px;}"
            "QPushButton:hover{background:#3a7a3a;}"
        )
        self.btn_import.clicked.connect(self.on_import_selected)

        self.btn_settings = QtWidgets.QPushButton("⚙")
        self.btn_settings.setFixedSize(32, 30)
        self.btn_settings.setToolTip("Settings")
        self.btn_settings.setCheckable(True)
        self.btn_settings.setStyleSheet(
            "QPushButton{background:#3a3a3a;color:#aaa;border:1px solid #555;"
            "border-radius:3px;font-size:14px;}"
            "QPushButton:hover{background:#4a4a4a;color:#fff;}"
            "QPushButton:checked{background:#505050;color:#fff;}"
        )
        self.btn_settings.clicked.connect(self._toggle_settings)

        self.btn_search_toggle = QtWidgets.QPushButton("🔍")
        self.btn_search_toggle.setFixedSize(32, 30)
        self.btn_search_toggle.setToolTip("Search nodes, labels and backdrops  (Ctrl+F)")
        self.btn_search_toggle.setCheckable(True)
        self.btn_search_toggle.setStyleSheet(
            "QPushButton{background:#3a3a3a;color:#aaa;border:1px solid #555;"
            "border-radius:3px;font-size:13px;}"
            "QPushButton:hover{background:#4a4a4a;color:#fff;}"
            "QPushButton:checked{background:#505050;color:#fff;}"
        )
        self.btn_search_toggle.clicked.connect(self._toggle_search)

        self.btn_shortcuts = QtWidgets.QPushButton("?")
        self.btn_shortcuts.setFixedSize(32, 30)
        self.btn_shortcuts.setToolTip("Keyboard shortcuts")
        self.btn_shortcuts.setCheckable(True)
        self.btn_shortcuts.setStyleSheet(
            "QPushButton{background:#3a3a3a;color:#aaa;border:1px solid #555;"
            "border-radius:3px;font-size:14px;font-weight:bold;}"
            "QPushButton:hover{background:#4a4a4a;color:#fff;}"
            "QPushButton:checked{background:#505050;color:#fff;}"
        )
        self.btn_shortcuts.clicked.connect(self._toggle_shortcuts)

        tb.addWidget(self.btn_recent)
        tb.addWidget(self.btn_select, 1)
        tb.addWidget(self.btn_import, 2)
        tb.addStretch(1)
        tb.addWidget(self.btn_search_toggle)
        tb.addWidget(self.btn_settings)
        tb.addWidget(self.btn_shortcuts)
        self._main_layout.addWidget(toolbar)

        # ── Search bar (hidden by default) ────────────────────────────────────
        self._search_bar = QtWidgets.QWidget()
        self._search_bar.setFixedHeight(42)
        self._search_bar.setStyleSheet("background:#252525;border-bottom:1px solid #333; QToolTip{background-color:#ffffdc;color:#000000;border:1px solid #c0c060;padding:4px;}")
        self._search_bar.hide()

        sb = QtWidgets.QHBoxLayout(self._search_bar)
        sb.setContentsMargins(8, 4, 8, 4)
        sb.setSpacing(4)
        sb.addStretch(1)

        self._search_edit = QtWidgets.QLineEdit()
        self._search_edit.setPlaceholderText("Search name, label, or backdrop text…")
        self._search_edit.setFixedWidth(300)
        self._search_edit.setStyleSheet(
            "QLineEdit{background:#1e1e1e;color:#eee;border:1px solid #555;"
            "border-radius:3px;padding:3px 8px;}"
            "QLineEdit:focus{border:1px solid #888;}"
        )
        self._search_edit.returnPressed.connect(self._on_search_enter)
        self._search_edit.textChanged.connect(self._on_search_text_changed)
        self._search_edit.installEventFilter(self)

        _btn_style = (
            "QPushButton{background:#3a3a3a;color:#ccc;border:1px solid #555;"
            "border-radius:3px;font-size:14px;font-weight:bold;}"
            "QPushButton:hover{background:#4a4a4a;color:#fff;}"
            "QPushButton:disabled{color:#555;border-color:#333;}"
        )

        self.btn_prev = QtWidgets.QPushButton("‹")
        self.btn_prev.setFixedSize(26, 26)
        self.btn_prev.setToolTip("Previous match")
        self.btn_prev.setStyleSheet(_btn_style)
        self.btn_prev.clicked.connect(self._on_search_prev)
        self.btn_prev.setEnabled(False)

        self.btn_next = QtWidgets.QPushButton("›")
        self.btn_next.setFixedSize(26, 26)
        self.btn_next.setToolTip("Next match")
        self.btn_next.setStyleSheet(_btn_style)
        self.btn_next.clicked.connect(self._on_search_next)
        self.btn_next.setEnabled(False)

        self.btn_search_close = QtWidgets.QPushButton("✕")
        self.btn_search_close.setFixedSize(22, 22)
        self.btn_search_close.setToolTip("Close search")
        self.btn_search_close.setStyleSheet(
            "QPushButton{background:transparent;color:#777;border:none;font-size:11px;}"
            "QPushButton:hover{color:#fff;}"
        )
        self.btn_search_close.clicked.connect(self._close_search)

        self._search_count = QtWidgets.QLabel("")
        self._search_count.setStyleSheet("color:#777;font-size:10px;min-width:100px;")
        self._search_count.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

        sb.addWidget(self._search_edit)
        sb.addWidget(self.btn_prev)
        sb.addWidget(self.btn_next)
        sb.addWidget(self.btn_search_close)
        sb.addWidget(self._search_count)
        self._main_layout.addWidget(self._search_bar)

        # ── Tab bar ───────────────────────────────────────────────────────────
        self._tab_bar = QtWidgets.QTabBar()
        self._tab_bar.setMovable(True)
        self._tab_bar.setTabsClosable(False)
        self._tab_bar.setExpanding(True)
        self._tab_bar.setDrawBase(False)
        self._tab_bar.setUsesScrollButtons(True)
        self._tab_bar.setElideMode(QtCore.Qt.ElideRight)
        self._tab_bar.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )
        self._tab_bar.setStyleSheet("""
            QTabBar { background: transparent; border: none; }
            QTabBar::tab {
                background: #252525;
                color: #888;
                border: 1px solid #333;
                border-bottom: none;
                padding: 4px 4px 4px 10px;
                min-width: 80px;
                max-width: 200px;
                font-size: 10px;
            }
            QTabBar::tab:selected { background: #1e1e1e; color: #eee; border-color: #555; }
            QTabBar::tab:hover:!selected { background: #2e2e2e; color: #bbb; }
            QTabBar::scroller { width: 20px; }
            QTabBar QToolButton { background: #2a2a2a; border: 1px solid #333; color: #888; }
            QTabBar QToolButton:hover { color: #fff; }
        """)

        # + button — to the right of the tab bar
        self._btn_new_tab = QtWidgets.QPushButton("+")
        self._btn_new_tab.setFixedSize(24, 24)
        self._btn_new_tab.setToolTip("New Tab (Ctrl + T)")
        self._btn_new_tab.setStyleSheet(
            "QPushButton{background:transparent;color:#777;border:none;"
            "font-size:15px;font-weight:bold;padding:0px;}"
            "QPushButton:hover{color:#fff;}"
        )
        self._btn_new_tab.clicked.connect(self._new_tab)

        tab_row = QtWidgets.QWidget()
        tab_row.setFixedHeight(30)
        tab_row.setStyleSheet("background:#1a1a1a;border-bottom:1px solid #333; QToolTip{background-color:#ffffdc;color:#000000;border:1px solid #c0c060;padding:4px;}")
        _tr_layout = QtWidgets.QHBoxLayout(tab_row)
        _tr_layout.setContentsMargins(2, 2, 4, 0)
        _tr_layout.setSpacing(4)
        _tr_layout.addWidget(self._tab_bar, 1)
        _tr_layout.addWidget(self._btn_new_tab)
        self._main_layout.addWidget(tab_row)

        # ── Graph view ────────────────────────────────────────────────────────
        self.graph_view = GraphView(self)
        self.graph_view.import_triggered.connect(self.on_import_selected)
        self._main_layout.addWidget(self.graph_view, 1)

        # ── Tab state ─────────────────────────────────────────────────────────
        # Each entry is a _ScriptTab holding that tab's full state.
        # The graph_view is shared — switching tabs swaps its scene.
        self._tabs: typing.List[_ScriptTab] = []
        self._closed_tabs: typing.List[_ScriptTab] = []   # for Ctrl+Shift+T
        self._current_tab_idx: int = -1

        # Wire tab bar signals
        self._tab_bar.currentChanged.connect(self._on_tab_changed)
        self._tab_bar.tabCloseRequested.connect(self._close_tab)
        # Keep _tabs list in sync when user drags to reorder
        self._tab_bar.tabMoved.connect(self._on_tab_moved)

        # ── Status bar ────────────────────────────────────────────────────────
        self.status_bar = QtWidgets.QStatusBar()
        self.status_bar.setStyleSheet("color:#aaa;font-size:10px;")
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready. Select a .nk or .nk~ autosave script to begin.")

        credit = QtWidgets.QLabel(
            '<a href="https://www.linkedin.com/in/joaquincuervo/" '
            'style="color:#555; text-decoration:none;">Created by Joaquin Cuervo</a>'
        )
        credit.setStyleSheet("font-size:10px; padding-right:6px;")
        credit.setOpenExternalLinks(True)
        credit.setCursor(QtCore.Qt.PointingHandCursor)
        self.status_bar.addPermanentWidget(credit)

        # ── Floating panels (parented to central widget so they overlap graph) ─
        self._settings_panel = SettingsPanel(central)

        self._shortcuts_panel = ShortcutsPanel(central)

        # Persist settings with QSettings (stored in OS-native location)
        self._qsettings = QtCore.QSettings("Anthropic", "NodesImport")
        self._recent_files: typing.List[str] = self._qsettings.value(
            "recent_files", [], type=list  # type: ignore[arg-type]
        ) or []
        self._update_recent_menu()
        self._settings_panel.close_after_import = (
            self._qsettings.value("close_after_import", False, type=bool)
        )
        self._settings_panel.chk_close_after_import.stateChanged.connect(
            self._save_settings
        )

        # Geometry is restored in showEvent (after window manager positions it)
        self._geometry_restored = False

        # Ctrl+F shortcut — fires regardless of which child widget has focus.
        # QShortcut lives in QtWidgets in PySide2 and QtGui in PySide6.
        _ShortcutClass = getattr(QtGui, "QShortcut", None) or QtWidgets.QShortcut
        shortcut_search = _ShortcutClass(
            QtGui.QKeySequence("Ctrl+F"), self
        )
        shortcut_search.setContext(QtCore.Qt.WindowShortcut)
        shortcut_search.activated.connect(self._on_ctrl_f)

        # Tab shortcuts
        for seq, slot in [
            ("Ctrl+T",           lambda: self._new_tab(open_picker=True)),
            ("Ctrl+W",           self._close_current_tab),
            ("Ctrl+Shift+T",     self._reopen_last_closed_tab),
            ("Ctrl+Tab",         self._next_tab),
            ("Ctrl+Shift+Tab",   self._prev_tab),
            ("Ctrl+R",           self._rename_current_tab),
        ]:
            sc = _ShortcutClass(QtGui.QKeySequence(seq), self)
            sc.setContext(QtCore.Qt.WindowShortcut)
            sc.activated.connect(slot)

        # Double-click on tab bar triggers rename
        self._tab_bar.setTabsClosable(False)
        self._tab_bar.mouseDoubleClickEvent = self._on_tab_double_click

        # + button opens picker (not just an empty tab)
        self._btn_new_tab.clicked.disconnect()
        self._btn_new_tab.clicked.connect(lambda: self._new_tab(open_picker=True))

        # Middle-click on tab bar closes that tab
        self._tab_bar.installEventFilter(self)

        self._info_panel = InfoPanel(central)
        self._info_panel.installEventFilter(self)

        # ⓘ button — bottom-right corner, always on top
        self.btn_info = QtWidgets.QPushButton("ⓘ")
        self.btn_info.setParent(central)
        self.btn_info.setFixedSize(30, 30)
        self.btn_info.setToolTip("Show script information")
        self.btn_info.setStyleSheet(
            "QPushButton{background:#2b2b2b;color:#777;border:1px solid #444;"
            "border-radius:15px;font-size:15px;}"
            "QPushButton:hover{color:#fff;border-color:#777;background:#333;}"
        )
        # Hover-only: no click needed — show panel on enter, hide on leave
        self.btn_info.installEventFilter(self)
        self.btn_info.raise_()

        # Install app-level filter ONLY for outside-click on settings panel.
        # We use widgetAt() to check what widget is actually under the cursor
        # at press time — this is reliable regardless of coordinate spaces.
        QtWidgets.QApplication.instance().installEventFilter(self)

        # Timer prevents the panel from blinking when cursor moves between
        # the button and the panel (fires hide only after a short grace period)
        self._info_hide_timer = QtCore.QTimer(self)
        self._info_hide_timer.setSingleShot(True)
        self._info_hide_timer.setInterval(120)
        self._info_hide_timer.timeout.connect(self._hide_info_if_not_hovered)

        # Create the first empty tab now that all widgets are initialised.
        # Must be last — _restore_tab_state accesses _info_panel, _search_edit
        # and other widgets that would not exist if called earlier.
        self._new_tab()



    # ── Tab management ────────────────────────────────────────────────────────

    def _make_tab_scene(self) -> QtWidgets.QGraphicsScene:
        """Create a fresh infinite QGraphicsScene for a new tab."""
        scene = QtWidgets.QGraphicsScene()
        scene.setSceneRect(
            -GraphView._INFINITE, -GraphView._INFINITE,
            GraphView._INFINITE * 2, GraphView._INFINITE * 2,
        )
        return scene

    def _update_tab_sizing(self) -> None:
        pass  # tab bar handles its own sizing via setExpanding(True)

    def _resize_tab_inner(self) -> None:
        pass

    def _tab_is_empty(self, idx: int) -> bool:
        """Return True if the tab at idx has no script loaded."""
        if idx < 0 or idx >= len(self._tabs):
            return True
        return self._tabs[idx].filepath is None

    def _find_tab_for_filepath(self, filepath: str) -> int:
        """Return the tab index that already has filepath open, or -1."""
        norm = filepath.replace("\\", "/")
        for i, tab in enumerate(self._tabs):
            if tab.filepath and tab.filepath.replace("\\", "/") == norm:
                return i
        return -1

    def _new_tab(self, open_picker: bool = False) -> None:
        """Create a new empty tab, switch to it, optionally open file picker.

        If open_picker is True and the current tab is already empty, skip
        creating a new tab and just open the picker in the current one —
        avoids accumulating blank tabs.
        """
        if open_picker and self._tab_is_empty(self._current_tab_idx):
            self.on_select_script()
            return

        tab = _ScriptTab()
        tab.scene     = self._make_tab_scene()
        self._tabs.append(tab)

        idx = len(self._tabs) - 1
        self._tab_bar.addTab("New Tab")
        self._tab_bar.setTabToolTip(idx, "")

        # Attach a real QPushButton as the close button so ✕ is always visible.
        # setTabButton is the only approach guaranteed to work in all Nuke builds.
        close_btn = QtWidgets.QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#666;border:none;"
            "font-size:9px;font-weight:bold;padding:0px;}"
            "QPushButton:hover{color:#fff;background:#555;border-radius:3px;}"
        )
        close_btn.setToolTip("Close tab")
        # Capture idx by value via default arg — must be a fresh lambda per tab
        close_btn.clicked.connect(lambda checked=False, b=close_btn: self._close_tab_by_button(b))
        self._tab_bar.setTabButton(idx, QtWidgets.QTabBar.RightSide, close_btn)

        self._update_tab_sizing()
        self._tab_bar.blockSignals(True)
        self._tab_bar.setCurrentIndex(idx)
        self._tab_bar.blockSignals(False)
        self._save_current_tab_state()
        self._current_tab_idx = idx
        self._restore_tab_state(idx)

        if open_picker:
            self.on_select_script()
            # If the user cancelled the picker, the new tab is still empty —
            # close it so we don't accumulate blank tabs.
            if self._tab_is_empty(idx):
                self._close_tab(idx)

    def _on_tab_moved(self, from_idx: int, to_idx: int) -> None:
        """Keep _tabs list in sync when user drags to reorder.

        This is the critical fix for the drag-reorder bug: QTabBar moves the
        visual tab but our _tabs list stays in the old order, so subsequent
        tab switches restore the wrong script. We mirror the move here.
        """
        tab = self._tabs.pop(from_idx)
        self._tabs.insert(to_idx, tab)
        # _current_tab_idx follows the dragged tab
        if self._current_tab_idx == from_idx:
            self._current_tab_idx = to_idx
        elif from_idx < self._current_tab_idx <= to_idx:
            self._current_tab_idx -= 1
        elif to_idx <= self._current_tab_idx < from_idx:
            self._current_tab_idx += 1
        # Refresh orange dots since open-script positions changed


    def _save_current_tab_state(self) -> None:
        """Snapshot graph view state into the current tab."""
        idx = self._current_tab_idx
        if idx < 0 or idx >= len(self._tabs):
            return
        tab                = self._tabs[idx]
        tab.scene          = self.graph_view.scene
        tab.search_results = list(self._search_results)
        tab.search_index   = self._search_index

        # Save the viewport center (in scene coordinates) and zoom level.
        # This is more robust than saving the full QTransform because
        # restoring via centerOn() + scale() doesn't fight with Qt's
        # internal viewport updates triggered by setScene().
        vp_rect = self.graph_view.viewport().rect()
        tab.view_center = self.graph_view.mapToScene(vp_rect.center())
        tab.zoom_level  = self.graph_view.transform().m11()

        if tab.view_center is not None:
            tab.user_navigated = True

    def _restore_tab_state(self, idx: int) -> None:
        """Restore graph view to the state stored in tab[idx]."""
        if idx < 0 or idx >= len(self._tabs):
            return
        tab = self._tabs[idx]
        self.graph_view.setScene(tab.scene)
        self.graph_view.scene = tab.scene

        if tab.user_navigated and tab.view_center is not None:
            # Restore saved center + zoom. Use singleShot so the viewport
            # has finished processing the new scene before we reposition.
            center = QtCore.QPointF(tab.view_center)
            zoom   = tab.zoom_level
            def _apply_view(c=center, z=zoom):
                # Reset transform, apply saved zoom, then center on saved point
                self.graph_view.resetTransform()
                self.graph_view.scale(z, z)
                self.graph_view.centerOn(c)
            QtCore.QTimer.singleShot(0, _apply_view)
        else:
            # Never navigated — frame all so content is immediately visible.
            def _frame_and_mark(tab=tab):
                self.graph_view.frame_all()
                tab.user_navigated = True
            QtCore.QTimer.singleShot(0, _frame_and_mark)

        self._nodes      = tab.nodes
        self._root_info  = tab.root_info
        self._info_panel.update_info(tab.root_info)
        self._search_results = tab.search_results
        self._search_index   = tab.search_index
        self._search_edit.clear()
        self._close_search()
        self._update_count_label(len(self._search_results), None)

    def _on_tab_changed(self, new_idx: int) -> None:
        """Called by QTabBar when the active tab changes."""
        if new_idx == self._current_tab_idx:
            return
        self._save_current_tab_state()
        self._current_tab_idx = new_idx
        self._restore_tab_state(new_idx)

    def _set_tab_title(self, idx: int, title: str, tooltip: str = "") -> None:
        if 0 <= idx < len(self._tabs):
            self._tabs[idx].title = title
        self._tab_bar.setTabText(idx, title)
        self._tab_bar.setTabToolTip(idx, "")

    def _close_tab_by_button(self, btn: QtWidgets.QPushButton) -> None:
        """Find which tab owns this close button and close it."""
        for i in range(self._tab_bar.count()):
            if self._tab_bar.tabButton(i, QtWidgets.QTabBar.RightSide) is btn:
                self._close_tab(i)
                return

    def _close_tab(self, idx: int) -> None:
        """Close a tab. If it is the last one, reset it to empty."""
        if self._tab_bar.count() <= 1:
            # Reset the last tab to empty rather than closing it
            self._tabs[0] = _ScriptTab(
                scene=self._make_tab_scene(),
            )
            self._set_tab_title(0, "New Tab")
            self._current_tab_idx = -1
            self._on_tab_changed(0)
            return

        # Push to closed stack for Ctrl+Shift+T
        self._closed_tabs.append(self._tabs[idx])
        self._tabs.pop(idx)

        # Block signals while removing so _on_tab_changed doesn't fire
        # with a stale _tabs list mid-removal
        self._tab_bar.blockSignals(True)
        self._tab_bar.removeTab(idx)
        self._tab_bar.blockSignals(False)
        self._update_tab_sizing()

        # Determine new active index
        new_idx = min(idx, self._tab_bar.count() - 1)
        self._tab_bar.setCurrentIndex(new_idx)
        self._current_tab_idx = new_idx
        self._restore_tab_state(new_idx)

    def _close_current_tab(self) -> None:
        self._close_tab(self._tab_bar.currentIndex())

    def _rename_current_tab(self) -> None:
        """Ctrl+R — start editing the current tab's name."""
        self._start_tab_rename(self._tab_bar.currentIndex())

    def _on_tab_double_click(self, event: QtGui.QMouseEvent) -> None:
        """Double LEFT click on a tab starts renaming it."""
        if event.button() != QtCore.Qt.LeftButton:
            return
        idx = self._tab_bar.tabAt(event.pos())
        if idx >= 0:
            self._start_tab_rename(idx)

    def _start_tab_rename(self, idx: int) -> None:
        """Show an inline QLineEdit over the tab for renaming."""
        if idx < 0 or idx >= len(self._tabs):
            return

        tab = self._tabs[idx]
        current_text = tab.custom_name or tab.title
        # Strip the ~ suffix for editing — it will be re-added automatically
        current_text = current_text.rstrip()
        if current_text.endswith("~"):
            current_text = current_text[:-1].rstrip()

        # Create a QLineEdit positioned over the tab
        rect = self._tab_bar.tabRect(idx)
        edit = QtWidgets.QLineEdit(self._tab_bar)
        edit.setText(current_text)
        edit.setMaxLength(50)
        edit.selectAll()
        edit.setGeometry(rect)
        edit.setStyleSheet(
            "QLineEdit{background:#1e1e1e;color:#eee;border:1px solid #888;"
            "padding:2px 6px;font-size:10px;}"
        )
        edit.setFocus()
        edit.show()

        # Check if this tab is an autosave
        is_autosave = tab.filepath and tab.filepath.endswith(".nk~")

        def _finish_rename() -> None:
            new_name = edit.text().strip()[:50]
            edit.deleteLater()
            if new_name and new_name != tab.title:
                tab.custom_name = new_name
                display = new_name + (" ~" if is_autosave else "")
                self._tab_bar.setTabText(idx, display)
                if tab.filepath:
                    self._tab_bar.setTabToolTip(idx, "")
            elif not new_name:
                # Cleared name — revert to auto title
                tab.custom_name = None
                self._tab_bar.setTabText(idx, tab.title)
            self._update_recent_menu()
            self.graph_view.setFocus()

        def _cancel_rename() -> None:
            edit.deleteLater()
            self.graph_view.setFocus()

        edit.returnPressed.connect(_finish_rename)

        # Accept on focus loss (click outside)
        def _on_focus_lost(e: QtCore.QEvent) -> bool:
            if e.type() == QtCore.QEvent.FocusOut:
                _finish_rename()
                return True
            if e.type() == QtCore.QEvent.KeyPress and e.key() == QtCore.Qt.Key_Escape:
                _cancel_rename()
                return True
            return False

        edit.installEventFilter(_TabRenameFilter(edit, _finish_rename, _cancel_rename))

    def _reopen_last_closed_tab(self) -> None:
        """Ctrl+Shift+T — reopen most recently closed tab, skipping empty ones."""
        while self._closed_tabs:
            tab = self._closed_tabs.pop()
            # Skip empty/New Tab entries — user doesn't need those back
            if tab.filepath is None:
                continue
            self._tabs.append(tab)
            idx = len(self._tabs) - 1
            display = tab.custom_name or tab.title
            self._tab_bar.addTab(display)
            self._tab_bar.setTabToolTip(idx, "")
            # Add close button
            close_btn = QtWidgets.QPushButton("✕")
            close_btn.setFixedSize(16, 16)
            close_btn.setStyleSheet(
                "QPushButton{background:transparent;color:#666;border:none;"
                "font-size:9px;font-weight:bold;padding:0px;}"
                "QPushButton:hover{color:#fff;background:#555;border-radius:3px;}"
            )
            close_btn.setToolTip("Close tab")
            close_btn.clicked.connect(
                lambda checked=False, b=close_btn: self._close_tab_by_button(b)
            )
            self._tab_bar.setTabButton(idx, QtWidgets.QTabBar.RightSide, close_btn)
            self._update_tab_sizing()
            self._tab_bar.setCurrentIndex(idx)
            return

    def _next_tab(self) -> None:
        """Ctrl+Tab — cycle forward, wraps around."""
        count = self._tab_bar.count()
        if count < 2:
            return
        self._tab_bar.setCurrentIndex((self._tab_bar.currentIndex() + 1) % count)

    def _prev_tab(self) -> None:
        """Ctrl+Shift+Tab — cycle backward, wraps around."""
        count = self._tab_bar.count()
        if count < 2:
            return
        self._tab_bar.setCurrentIndex((self._tab_bar.currentIndex() - 1) % count)

    # ── Layout / resize ───────────────────────────────────────────────────────

    def _on_ctrl_f(self) -> None:
        """Open or focus the search bar (triggered by Ctrl+F shortcut)."""
        if not self._search_bar.isVisible():
            self.btn_search_toggle.setChecked(True)
            self._toggle_search(True)
        else:
            self._search_edit.setFocus()
            self._search_edit.selectAll()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        # Ctrl+F is handled by QShortcut; this fallback catches edge cases
        # where the shortcut context doesn't fire (e.g. no script loaded yet).
        if (
            event.key() == QtCore.Qt.Key_F
            and event.modifiers() == QtCore.Qt.ControlModifier
        ):
            self._on_ctrl_f()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._reposition_overlays()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        # Restore geometry here — after the window manager has placed the window —
        # so that position is actually applied. Only do this once per session.
        if not self._geometry_restored:
            self._geometry_restored = True
            geometry = self._qsettings.value("window_geometry")
            if geometry:
                self.restoreGeometry(geometry)
        self._reposition_overlays()

    def changeEvent(self, event: QtCore.QEvent) -> None:
        """Handle window state changes (minimize/restore)."""
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.WindowStateChange:
            # If the window was minimized and is now being restored,
            # ensure it comes to front properly
            if not (self.windowState() & QtCore.Qt.WindowMinimized):
                QtCore.QTimer.singleShot(0, self._ensure_visible)

    def _ensure_visible(self) -> None:
        """Bring window to front — used after restore from minimized."""
        self.raise_()
        self.activateWindow()

    def _reposition_overlays(self) -> None:
        central = self.centralWidget()
        if central is None:
            return
        cw, ch = central.width(), central.height()
        m = 8

        # ⓘ button — bottom-right
        self.btn_info.move(cw - self.btn_info.width() - m,
                           ch - self.btn_info.height() - m)
        self.btn_info.raise_()

        # Info panel — directly above ⓘ button
        if self._info_panel.isVisible():
            self._info_panel.adjustSize()
            self._info_panel.move(
                cw - self._info_panel.width() - m,
                ch - self._info_panel.height() - self.btn_info.height() - m * 2,
            )
            self._info_panel.raise_()

        # Settings panel — below toolbar, right-aligned to ⚙ button
        if self._settings_panel.isVisible():
            self._place_settings_panel()

        # Shortcuts panel — below toolbar, right-aligned to ? button
        if self._shortcuts_panel.isVisible():
            self._place_shortcuts_panel()

    def _place_settings_panel(self) -> None:
        central = self.centralWidget()
        if not central:
            return
        self._settings_panel.adjustSize()
        # btn_settings is in the toolbar which is a child of central.
        # mapTo(central) gives us coords in central's local space, which is
        # exactly what move() expects since the panel is parented to central.
        btn_pos = self.btn_settings.mapTo(central, QtCore.QPoint(0, 0))
        x = btn_pos.x() + self.btn_settings.width() - self._settings_panel.width()
        y = btn_pos.y() + self.btn_settings.height() + 2
        self._settings_panel.move(x, y)
        self._settings_panel.raise_()

    def _place_shortcuts_panel(self) -> None:
        central = self.centralWidget()
        if not central:
            return
        self._shortcuts_panel.adjustSize()
        btn_pos = self.btn_shortcuts.mapTo(central, QtCore.QPoint(0, 0))
        x = btn_pos.x() + self.btn_shortcuts.width() - self._shortcuts_panel.width()
        y = btn_pos.y() + self.btn_shortcuts.height() + 2
        self._shortcuts_panel.move(x, y)
        self._shortcuts_panel.raise_()

    # ── Panel toggles ─────────────────────────────────────────────────────────

    def _toggle_settings(self, checked: bool) -> None:
        if checked:
            self._shortcuts_panel.hide()
            self.btn_shortcuts.setChecked(False)
            self._close_search()
            self._settings_panel.show()
            self._place_settings_panel()
        else:
            self._settings_panel.hide()

    def _toggle_shortcuts(self, checked: bool) -> None:
        if checked:
            self._settings_panel.hide()
            self.btn_settings.setChecked(False)
            self._close_search()
            self._shortcuts_panel.show()
            self._place_shortcuts_panel()
        else:
            self._shortcuts_panel.hide()

    # ── Info panel hover logic ────────────────────────────────────────────────

    def _show_info(self) -> None:
        """Show the info panel immediately (called on hover enter)."""
        self._info_hide_timer.stop()
        if not self._info_panel.isVisible():
            self._info_panel.show()
            self._reposition_overlays()
        self._info_panel.raise_()

    def _schedule_hide_info(self) -> None:
        """Start the grace timer — panel hides only if cursor has left both
        the button and the panel by the time the timer fires."""
        self._info_hide_timer.start()

    def _hide_info_if_not_hovered(self) -> None:
        """Called by the hide timer — only hides if cursor is outside both."""
        btn_rect   = QtCore.QRect(
            self.btn_info.mapToGlobal(QtCore.QPoint(0, 0)),
            self.btn_info.size(),
        )
        panel_rect = QtCore.QRect(
            self._info_panel.mapToGlobal(QtCore.QPoint(0, 0)),
            self._info_panel.size(),
        )
        cursor = QtGui.QCursor.pos()
        if not btn_rect.contains(cursor) and not panel_rect.contains(cursor):
            self._info_panel.hide()

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        """
        0. Middle-click on tab bar → close that tab.
        1. btn_info hover → show/hide InfoPanel.
        2. Escape in search box → close search bar.
        3. Any mouse press outside SettingsPanel + ⚙ button → close panel.

        All attribute accesses are guarded with hasattr because the app-level
        event filter is installed during __init__ and can fire before all
        widgets have been assigned to self.
        """
        # ── Job 0: middle-click on tab bar closes that tab ───────────────────
        if (
            hasattr(self, "_tab_bar")
            and obj is self._tab_bar
            and event.type() == QtCore.QEvent.MouseButtonPress
            and event.button() == QtCore.Qt.MiddleButton
        ):
            tab_idx = self._tab_bar.tabAt(event.pos())
            if tab_idx >= 0:
                self._close_tab(tab_idx)
            return True

        # ── Job 1: btn_info hover ────────────────────────────────────────────
        if hasattr(self, "btn_info") and obj is self.btn_info:
            if event.type() == QtCore.QEvent.Enter:
                self._show_info()
                return False
            if event.type() == QtCore.QEvent.Leave:
                self._schedule_hide_info()
                return False

        # ── Job 2: Escape in search box → close search ───────────────────────
        if (
            hasattr(self, "_search_edit")
            and obj is self._search_edit
            and event.type() == QtCore.QEvent.KeyPress
            and event.key() == QtCore.Qt.Key_Escape  # type: ignore[union-attr]
        ):
            self._close_search()
            return True

        # ── Job 3: InfoPanel hover — keep panel open while inside ────────────
        if hasattr(self, "_info_panel") and obj is self._info_panel:
            if event.type() == QtCore.QEvent.Enter:
                self._info_hide_timer.stop()
                return False
            if event.type() == QtCore.QEvent.Leave:
                self._schedule_hide_info()
                return False

        # ── Job 4: outside click closes SettingsPanel ────────────────────────
        if (
            event.type() == QtCore.QEvent.MouseButtonPress
            and hasattr(self, "_settings_panel")
            and self._settings_panel.isVisible()
        ):
            try:
                gpos = event.globalPosition().toPoint()
            except AttributeError:
                try:
                    gpos = event.globalPos()
                except AttributeError:
                    gpos = QtGui.QCursor.pos()

            widget_under = QtWidgets.QApplication.widgetAt(gpos)

            # Walk up the parent chain. In PySide6, w.parent() may return a
            # different Python wrapper for the same C++ object, so 'is' can
            # fail. Compare by pointer address via id() on the wrapped object,
            # using a helper that works across PySide6 wrapper re-creation.
            panel_id  = id(self._settings_panel)
            btn_id    = id(self.btn_settings)

            def _same(a: object, b_id: int) -> bool:
                # Try sip/Shiboken pointer comparison first (most reliable),
                # fall back to id() which works when wrappers are stable.
                try:
                    if _PYSIDE_VERSION == 6:
                        import shiboken6 as _shiboken  # type: ignore
                    else:
                        import shiboken2 as _shiboken  # type: ignore
                    return _shiboken.getCppPointer(a)[0] == _shiboken.getCppPointer(
                        self._settings_panel if b_id == panel_id else self.btn_settings
                    )[0]
                except Exception:
                    return id(a) == b_id

            inside = False
            w: typing.Optional[QtCore.QObject] = widget_under
            while w is not None:
                if _same(w, panel_id) or _same(w, btn_id):
                    inside = True
                    break
                w = w.parent()

            if not inside:
                self._settings_panel.hide()
                self.btn_settings.setChecked(False)

        # Also close shortcuts panel on outside click
        if (
            event.type() == QtCore.QEvent.MouseButtonPress
            and hasattr(self, "_shortcuts_panel")
            and self._shortcuts_panel.isVisible()
        ):
            try:
                gpos = event.globalPosition().toPoint()
            except AttributeError:
                try:
                    gpos = event.globalPos()
                except AttributeError:
                    gpos = QtGui.QCursor.pos()

            widget_under = QtWidgets.QApplication.widgetAt(gpos)
            w = widget_under
            while w is not None:
                if w is self._shortcuts_panel or w is self.btn_shortcuts:
                    break
                w = w.parent()
            else:
                self._shortcuts_panel.hide()
                self.btn_shortcuts.setChecked(False)

        return super().eventFilter(obj, event)

    def _toggle_search(self, checked: bool) -> None:
        if checked:
            # Close settings (info panel is hover-only)
            self._settings_panel.hide()
            self.btn_settings.setChecked(False)
            self._search_bar.show()
            self._search_edit.setFocus()
            self._search_edit.selectAll()
        else:
            self._close_search()

    def _close_search(self) -> None:
        self._search_bar.hide()
        self.btn_search_toggle.setChecked(False)
        self._search_edit.clear()
        self._clear_search_state()

    # ── Search ────────────────────────────────────────────────────────────────

    def _on_search_text_changed(self, text: str) -> None:
        self._run_search(text)

    def _on_search_enter(self) -> None:
        if self._search_results:
            # Already have results — just advance to next
            self._search_index = (self._search_index + 1) % len(self._search_results)
            self._focus_current()
        else:
            self._run_search(self._search_edit.text())

    def _on_search_prev(self) -> None:
        if not self._search_results:
            return
        self._search_index = (self._search_index - 1) % len(self._search_results)
        self._focus_current()

    def _on_search_next(self) -> None:
        if not self._search_results:
            return
        self._search_index = (self._search_index + 1) % len(self._search_results)
        self._focus_current()

    def _run_search(self, query: str) -> None:
        """
        Populate _search_results with scene items matching `query`.

        Sort order:
          1. Exact name match (rank 0)
          2. Name starts with query (rank 1)
          3. Everything else (rank 2)
          Within each rank, sort by parse index ascending so
          Blur < Blur1 < Blur2 < Blur3…
        """
        self._clear_search_state()
        query = query.strip()
        if not query or not self._nodes:
            return

        q = query.lower()

        # Build scene-item lookup by node index
        scene_map: typing.Dict[int, QtWidgets.QGraphicsItem] = {}
        for item in self.graph_view.scene.items():
            if isinstance(item, (NodeItem, DotItem, BackdropItem)):
                scene_map[item.node_data.index] = item

        matches: typing.List[typing.Tuple[int, int, QtWidgets.QGraphicsItem]] = []
        for node in self._nodes:
            if q not in _node_search_text(node).lower():
                continue
            item = scene_map.get(node.index)
            if item is None:
                continue
            name_l = node.name.lower()
            if name_l == q:
                rank = 0
            elif name_l.startswith(q):
                rank = 1
            else:
                rank = 2
            matches.append((rank, node.index, item))

        matches.sort(key=lambda x: (x[0], x[1]))
        self._search_results = [item for _, _, item in matches]
        count = len(self._search_results)

        self._update_count_label(count, current=None)
        has = count > 0
        self.btn_prev.setEnabled(has)
        self.btn_next.setEnabled(has)

        if has:
            self._search_index = 0
            self._focus_current()

    def _focus_current(self) -> None:
        if not self._search_results:
            return
        item = self._search_results[self._search_index]
        total = len(self._search_results)
        self._update_count_label(total, current=self._search_index + 1)

        # Select only this item
        for si in self.graph_view.scene.items():
            si.setSelected(False)
        item.setSelected(True)

        # Frame on it with margin
        M = 150
        self.graph_view.fitInView(
            item.sceneBoundingRect().adjusted(-M, -M, M, M),
            QtCore.Qt.KeepAspectRatio,
        )

    def _clear_search_state(self) -> None:
        self._search_results = []
        self._search_index   = -1
        self.btn_prev.setEnabled(False)
        self.btn_next.setEnabled(False)
        self._search_count.setText("")

    def _update_count_label(self, total: int, current: typing.Optional[int]) -> None:
        if total == 0:
            text = "No matches" if self._search_edit.text().strip() else ""
        elif current is not None:
            text = f"{current} / {total} match{'es' if total != 1 else ''}"
        else:
            text = f"{total} match{'es' if total != 1 else ''}"
        self._search_count.setText(text)

    # ── Recent files ──────────────────────────────────────────────────────────

    _MAX_RECENT = 10

    def _add_recent_file(self, filepath: str) -> None:
        """Add filepath to the top of the recent files list and persist it."""
        filepath = filepath.replace("\\", "/")
        if filepath in self._recent_files:
            self._recent_files.remove(filepath)
        self._recent_files.insert(0, filepath)
        self._recent_files = self._recent_files[:self._MAX_RECENT]
        self._qsettings.setValue("recent_files", self._recent_files)
        self._update_recent_menu()

    def _update_recent_menu(self) -> None:
        """Rebuild the recent files dropdown. Show custom names if set."""
        self._recent_menu.clear()
        if not self._recent_files:
            empty = self._recent_menu.addAction("No recent files")
            empty.setEnabled(False)
            return

        # Build lookup: filepath → custom_name from open tabs
        custom_names: typing.Dict[str, str] = {}
        for tab in self._tabs:
            if tab.filepath and tab.custom_name:
                custom_names[tab.filepath.replace("\\", "/")] = tab.custom_name

        for path in self._recent_files:
            norm = path.replace("\\", "/")
            filename = norm.split("/")[-1]
            custom = custom_names.get(norm)
            if custom:
                display = f"{custom} ({filename})"
            else:
                display = filename
            # Truncate to 80 characters
            if len(display) > 80:
                display = display[:77] + "..."
            action = self._recent_menu.addAction(display)
            action.setToolTip(path)
            action.triggered.connect(lambda checked=False, p=path: self._load_script(p, new_tab=True))

        self._recent_menu.addSeparator()
        clear_action = self._recent_menu.addAction("Clear Recent Files")
        clear_action.triggered.connect(self._clear_recent_files)

    def _clear_recent_files(self) -> None:
        self._recent_files = []
        self._qsettings.setValue("recent_files", [])
        self._update_recent_menu()

    def _show_recent_menu(self) -> None:
        """Show the recent files menu below the clock button."""
        btn_pos = self.btn_recent.mapToGlobal(
            QtCore.QPoint(0, self.btn_recent.height())
        )
        self._recent_menu.exec_(btn_pos)

    # ── Script loading ────────────────────────────────────────────────────────

    def on_select_script(self) -> None:
        filepath: typing.Optional[str] = None

        if nuke:
            filepath = nuke.getFilename("Select Script or Autosave", "*.nk *.nk~")
        else:
            result, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Select Script", "", "Nuke Scripts (*.nk *.nk~)"
            )
            filepath = result or None

        # Bring our window back to front — Nuke's file dialog steals focus
        # and since we have no parent, focus returns to Nuke not to us.
        self.raise_()
        self.activateWindow()

        if not filepath:
            return

        self._load_script(filepath)

    def _load_script(self, filepath: str, new_tab: bool = False) -> None:
        """Parse and display a script. Shared by file picker and recent menu.

        If the script is already open in another tab, switch to it instead of
        loading it again. If new_tab=True and the current tab has content,
        open a new tab first.
        """
        import os
        filepath = filepath.replace("\\", "/")

        # ── Duplicate detection: redirect to existing tab ─────────────────────
        existing = self._find_tab_for_filepath(filepath)
        if existing >= 0:
            self._tab_bar.setCurrentIndex(existing)
            self.status_bar.showMessage(f"Already open — switched to existing tab.")
            return

        # ── Smart routing: open new tab if current has content ────────────────
        if new_tab and not self._tab_is_empty(self._current_tab_idx):
            self._tabs.append(_ScriptTab(
                scene=self._make_tab_scene(),
            ))
            new_idx = len(self._tabs) - 1
            self._tab_bar.addTab("New Tab")
            self._tab_bar.setTabToolTip(new_idx, "")
            self._update_tab_sizing()
            self._tab_bar.blockSignals(True)
            self._tab_bar.setCurrentIndex(new_idx)
            self._tab_bar.blockSignals(False)
            self._save_current_tab_state()
            self._current_tab_idx = new_idx
            self._restore_tab_state(new_idx)

        if not os.path.exists(filepath):
            QtWidgets.QMessageBox.warning(
                self, "File Not Found",
                f"The file no longer exists:\n{filepath}",
            )
            # Remove from recent list if it's gone
            if filepath.replace("\\", "/") in self._recent_files:
                self._recent_files.remove(filepath.replace("\\", "/"))
                self._qsettings.setValue("recent_files", self._recent_files)
                self._update_recent_menu()
            return

        self.status_bar.showMessage(f"Parsing: {filepath} …")
        QtWidgets.QApplication.processEvents()

        try:
            nodes = parse_nuke_script(filepath)

            if not nodes:
                QtWidgets.QMessageBox.warning(
                    self, "No Nodes Found",
                    f"The parser returned 0 nodes from:\n{filepath}\n\n"
                    "The file may be empty or use an unsupported format.",
                )
                self.status_bar.showMessage("No nodes found.")
                return

            self._nodes     = nodes
            self._root_info = _parse_root_info(filepath)
            self._info_panel.update_info(self._root_info)
            self._clear_search_state()
            self._search_edit.clear()

            self.graph_view.load_nodes(nodes)

            # Update current tab's stored state and title
            cur = self._current_tab_idx
            if 0 <= cur < len(self._tabs):
                self._tabs[cur].nodes     = nodes
                self._tabs[cur].root_info = self._root_info
                self._tabs[cur].filepath  = filepath
                # Tab title = filename without extension
                import os as _os
                name = _os.path.basename(filepath)
                # Strip .nk or .nk~ extension for display, but keep ~ indicator
                is_autosave = name.endswith(".nk~")
                for ext in (".nk~", ".nk"):
                    if name.endswith(ext):
                        name = name[: -len(ext)]
                        break
                if is_autosave:
                    name += " ~"
                self._set_tab_title(cur, name, filepath)

            self.status_bar.showMessage(
                f"Loaded {len(nodes)} node(s) from: {filepath}"
            )
            # Only add to recent files after a successful load
            self._add_recent_file(filepath)
            # Refresh orange dots on all tabs
                # Refresh recent menu to show dot for newly opened file
            self._update_recent_menu()

        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "Parse Error",
                f"Failed to parse script:\n{filepath}\n\n{exc}",
            )
            self.status_bar.showMessage("Parsing failed.")


    # ── Persistent settings ───────────────────────────────────────────────────

    def _save_settings(self) -> None:
        self._qsettings.setValue(
            "close_after_import",
            self._settings_panel.close_after_import,
        )

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """Close empty tabs, save window geometry."""
        # Remove all empty tabs silently before closing
        for idx in range(self._tab_bar.count() - 1, -1, -1):
            if self._tab_is_empty(idx):
                if self._tab_bar.count() > 1:
                    # Don't push empty tabs to closed stack
                    if idx < len(self._tabs):
                        self._tabs.pop(idx)
                    self._tab_bar.blockSignals(True)
                    self._tab_bar.removeTab(idx)
                    self._tab_bar.blockSignals(False)
        self._qsettings.setValue("window_geometry", self.saveGeometry())
        super().closeEvent(event)

    # ── Import ────────────────────────────────────────────────────────────────

    def on_import_selected(self) -> None:
        if not nuke:
            QtWidgets.QMessageBox.information(
                self, "Nuke Not Available",
                "Import is only available when running inside Nuke.\n"
                "Launch this tool from Nuke's Script Editor or menu.",
            )
            return

        # Only import from the currently active tab's scene
        active_scene = self.graph_view.scene
        selected_items = [
            i for i in active_scene.selectedItems()
            if isinstance(i, (NodeItem, DotItem, BackdropItem))
        ]

        if not selected_items:
            self.status_bar.showMessage(
                "No nodes selected — click or rubber-band select nodes first."
            )
            return

        # ── Expression link check ────────────────────────────────────────────
        # Before importing, scan selected nodes for expressions that reference
        # unselected nodes. If found, offer to include them.
        if self._nodes:
            expr_result = self._check_expression_links(selected_items)
            if expr_result is False:
                # User cancelled
                self.status_bar.showMessage("Import cancelled.")
                return
            # If extra nodes were added to selection, re-read it
            if expr_result is True:
                selected_items = [
                    i for i in active_scene.selectedItems()
                    if isinstance(i, (NodeItem, DotItem, BackdropItem))
                ]

        # ── Stamps anchor detection (pre-import scan) ────────────────────────
        # Detect missing anchors BEFORE import so we have the data ready,
        # but don't block the import itself.
        missing_stamp_anchors: typing.Dict[str, typing.List[str]] = {}
        if self._nodes:
            missing_stamp_anchors = self._find_missing_stamp_anchors(selected_items)

        # Clear any previous color warning
        self.graph_view._last_import_color_warning = None

        self.graph_view.import_selected()

        # Build detailed feedback message from the graph_view's stored counts
        total = getattr(self.graph_view, "_last_import_count", len(selected_items))
        type_counts = getattr(self.graph_view, "_last_import_types", {})

        if type_counts:
            parts = []
            for nt, count in sorted(type_counts.items(), key=lambda x: -x[1]):
                parts.append(f"{count} {nt}")
            breakdown = ", ".join(parts)
            msg = f"✓ Imported {total} node(s): {breakdown}"
        else:
            msg = f"✓ Imported {total} node(s)."

        self.status_bar.showMessage(msg, 8000)

        # ── Post-import color management warning ─────────────────────────────
        color_warning = getattr(self.graph_view, "_last_import_color_warning", None)
        if color_warning:
            if nuke:
                nuke.warning(
                    "[NodesImport] The source script has a different color config "
                    "than your current script. Some imported nodes may have "
                    "default/incorrect values in their colorspace knobs."
                )

        # ── Post-import Stamps anchor warning ────────────────────────────────
        # If imported Stamps had missing Anchors, show a message, select the
        # Anchors in the NodesImport view, and zoom to them.
        if missing_stamp_anchors:
            self._show_missing_anchors(missing_stamp_anchors)
            # Don't close the window — user needs to see the anchors
            return

        if self._settings_panel.close_after_import:
            self.close()

    # ── Expression link detection ────────────────────────────────────────────

    # Patterns that reference another node's knob by name.
    # Group 1 = node name, Group 2 = knob name.
    _EXPR_PATTERNS = [
        re.compile(r"parent\.(\w+)\.(\w+)"),                  # parent.NodeName.knob
        re.compile(r"\{(\w+)\.(\w+)\}"),                      # {NodeName.knob}
        re.compile(r"\{\{[^}]*?(\w+)\.(\w+)"),                # {{NodeName.knob ...}}
        re.compile(r"\[value\s+(\w+)\.(\w+)\]"),              # [value NodeName.knob]
    ]
    _EXPR_SKIP_NAMES = frozenset({
        "parent", "root", "input", "this", "topnode", "node",
    })

    def _check_expression_links(
        self,
        selected_items: typing.List[QtWidgets.QGraphicsItem],
    ) -> typing.Optional[bool]:
        """
        Scan selected nodes for expressions referencing unselected nodes.

        Returns:
          None  — no broken links found, proceed normally.
          True  — user chose "Import All"; extra nodes added to selection.
          False — user chose "Cancel".
        """
        # Build lookup structures
        all_nodes_by_name: typing.Dict[str, typing.Any] = {
            n.name: n for n in self._nodes
        }
        sel_names: typing.Set[str] = set()
        for item in selected_items:
            if hasattr(item, "node_data"):
                sel_names.add(item.node_data.name)

        # Scan each selected node's content for cross-node references.
        # For Group nodes, only scan the header (before first inner Input)
        # because internal references travel with the Group.
        broken_links: typing.List[typing.Tuple[str, str, str]] = []
        seen: typing.Set[typing.Tuple[str, str]] = set()

        for item in selected_items:
            if not hasattr(item, "node_data"):
                continue
            nd = item.node_data

            if nd.node_type.lower() in ("group", "livegroup", "gizmo"):
                header_end = nd.content.find("\nInput {")
                if header_end < 0:
                    header_end = nd.content.find("\n}\n")
                content = nd.content[:header_end] if header_end > 0 else ""
            else:
                content = nd.content

            for pattern in self._EXPR_PATTERNS:
                for m in pattern.finditer(content):
                    ref_name = m.group(1)
                    ref_knob = m.group(2)
                    if ref_name == nd.name:
                        continue
                    if ref_name.lower() in self._EXPR_SKIP_NAMES:
                        continue
                    if ref_name not in all_nodes_by_name:
                        continue
                    if ref_name in sel_names:
                        continue
                    key = (nd.name, ref_name)
                    if key not in seen:
                        seen.add(key)
                        broken_links.append((nd.name, ref_name, ref_knob))

        if not broken_links:
            return None

        # Build dialog — group by target node, then list sources under each
        targets: typing.Dict[str, typing.List[str]] = {}
        for src, tgt, knob in broken_links:
            targets.setdefault(tgt, []).append(src)

        # Build two-column HTML table: Selected nodes | Linked to
        # Group rows by shared target so they're visually together
        rows = ""
        for tgt, sources in targets.items():
            unique_sources = sorted(set(sources))
            for i, src in enumerate(unique_sources):
                # Only show the target name on the first row of each group
                tgt_cell = tgt if i == 0 else ""
                rows += (
                    f'<tr>'
                    f'<td style="padding:2px 16px 2px 8px; color:#ddd;">• {src}</td>'
                    f'<td style="padding:2px 8px; color:#ddd;">{"• " + tgt_cell if tgt_cell else ""}</td>'
                    f'</tr>'
                )

        table_html = (
            f'<table style="margin:8px 0px;">'
            f'<tr>'
            f'<th style="text-align:left; padding:2px 16px 6px 8px; color:#aaa; '
            f'font-weight:bold; border-bottom:1px solid #555;">Selected nodes</th>'
            f'<th style="text-align:left; padding:2px 8px 6px 8px; color:#aaa; '
            f'font-weight:bold; border-bottom:1px solid #555;">Linked to</th>'
            f'</tr>'
            f'{rows}'
            f'</table>'
        )

        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Expression Links")
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setText(
            "Some selected nodes have expressions linked to "
            "nodes outside your selection:"
        )
        box.setInformativeText(table_html)
        btn_all    = box.addButton("Import All", QtWidgets.QMessageBox.AcceptRole)
        btn_anyway = box.addButton("Import Anyway", QtWidgets.QMessageBox.DestructiveRole)
        btn_cancel = box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(btn_all)
        box.exec_()

        clicked = box.clickedButton()
        if clicked is btn_cancel:
            return False

        if clicked is btn_all:
            # Add the missing nodes to the scene selection
            scene_items_by_name: typing.Dict[str, QtWidgets.QGraphicsItem] = {}
            for item in self.graph_view.scene.items():
                if isinstance(item, (NodeItem, DotItem, BackdropItem)):
                    scene_items_by_name[item.node_data.name] = item

            added = 0
            for tgt_name in targets:
                item = scene_items_by_name.get(tgt_name)
                if item is not None and not item.isSelected():
                    item.setSelected(True)
                    added += 1

            if added:
                self.status_bar.showMessage(
                    f"Added {added} expression-linked node(s) to selection."
                )
            return True

        # "Import Anyway" — proceed with original selection
        return None

    # ── Stamps anchor detection ──────────────────────────────────────────────

    def _find_missing_stamp_anchors(
        self,
        selected_items: typing.List[QtWidgets.QGraphicsItem],
    ) -> typing.Dict[str, typing.List[str]]:
        """
        Detect selected Wired Stamps whose Anchors are not in the selection.
        Returns a dict of anchor_name → [stamp_name, ...], empty if none missing.
        """
        all_nodes_by_name: typing.Dict[str, typing.Any] = {
            n.name: n for n in self._nodes
        }
        sel_names: typing.Set[str] = set()
        for item in selected_items:
            if hasattr(item, "node_data"):
                sel_names.add(item.node_data.name)

        missing: typing.Dict[str, typing.List[str]] = {}

        for item in selected_items:
            if not hasattr(item, "node_data"):
                continue
            nd = item.node_data
            if nd.node_type != "PostageStamp":
                continue
            if not re.search(r"identifier\s+.*?T\s+wired", nd.content):
                continue
            anchor_m = re.search(r"^\s*anchor\s+(\S+)", nd.content, re.MULTILINE)
            if not anchor_m:
                continue
            anchor_name = anchor_m.group(1).strip()
            if anchor_name in sel_names:
                continue
            if anchor_name not in all_nodes_by_name:
                continue
            missing.setdefault(anchor_name, []).append(nd.name)

        return missing

    def _show_missing_anchors(
        self,
        missing_anchors: typing.Dict[str, typing.List[str]],
    ) -> None:
        """
        Post-import: show a clean message listing which Stamp titles connect
        to which Anchors, select all upstream nodes from those Anchors in the
        NodesImport graph view, and zoom to them.
        """
        all_nodes_by_name: typing.Dict[str, typing.Any] = {
            n.name: n for n in self._nodes
        }
        by_index: typing.Dict[int, typing.Any] = {
            n.index: n for n in self._nodes
        }

        # Get the Stamp title (display name) for each wired stamp
        def _stamp_title(stamp_name: str) -> str:
            nd = all_nodes_by_name.get(stamp_name)
            if nd:
                m = re.search(r"^\s*title\s+(.+)", nd.content, re.MULTILINE)
                if m:
                    return m.group(1).strip().strip('"')
            return stamp_name

        # Get Anchor title
        def _anchor_title(anchor_name: str) -> str:
            nd = all_nodes_by_name.get(anchor_name)
            if nd:
                m = re.search(r"^\s*title\s+(.+)", nd.content, re.MULTILINE)
                if m:
                    return m.group(1).strip().strip('"')
            return anchor_name

        # Build plain text list — just show unique anchor titles
        # (Stamps and their Anchors always share the same title)
        anchor_title_list = []
        for anchor_name in missing_anchors:
            a_title = _anchor_title(anchor_name)
            anchor_title_list.append(a_title)

        detail = "\n".join(f"  • {t}" for t in sorted(set(anchor_title_list)))

        # Walk upstream from each Anchor (BFS) to collect all upstream nodes
        upstream_indices: typing.Set[int] = set()
        for anchor_name in missing_anchors:
            anchor_nd = all_nodes_by_name.get(anchor_name)
            if anchor_nd is None:
                continue
            queue = [anchor_nd.index]
            while queue:
                idx = queue.pop(0)
                if idx in upstream_indices or idx == _NULL_INPUT:
                    continue
                upstream_indices.add(idx)
                nd = by_index.get(idx)
                if nd is None:
                    continue
                for pi in nd.parent_indices:
                    if pi != _NULL_INPUT and pi not in upstream_indices:
                        queue.append(pi)

        # Select all upstream nodes in the scene, deselect everything else
        scene_items_by_index: typing.Dict[int, QtWidgets.QGraphicsItem] = {}
        for item in self.graph_view.scene.items():
            if isinstance(item, (NodeItem, DotItem, BackdropItem)):
                scene_items_by_index[item.node_data.index] = item

        for item in self.graph_view.scene.items():
            if isinstance(item, (NodeItem, DotItem, BackdropItem)):
                item.setSelected(False)

        selected_items = []
        for idx in upstream_indices:
            item = scene_items_by_index.get(idx)
            if item is not None:
                item.setSelected(True)
                selected_items.append(item)

        # Zoom to fit all selected upstream nodes
        if selected_items:
            bounds = selected_items[0].sceneBoundingRect()
            for item in selected_items[1:]:
                bounds = bounds.united(item.sceneBoundingRect())
            M = 300
            self.graph_view.fitInView(
                bounds.adjusted(-M, -M, M, M), QtCore.Qt.KeepAspectRatio
            )

        # Show message
        QtWidgets.QMessageBox.information(
            self,
            "Stamp Connections",
            f"The imported Stamps are connected to Anchors that were "
            f"not in your selection:\n\n"
            f"{detail}\n\n"
            f"Their upstream nodes are now selected in the graph view "
            f"in case you want to import them too.",
        )


