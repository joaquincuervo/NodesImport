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
# Main window
# ---------------------------------------------------------------------------

class NodesImportWindow(QtWidgets.QMainWindow):

    def __init__(self) -> None:
        parent = _get_nuke_main_window()
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window)
        self.setWindowTitle("Nodes Import")
        self.resize(1100, 720)

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
        toolbar.setStyleSheet("background-color: #2b2b2b;")
        tb = QtWidgets.QHBoxLayout(toolbar)
        tb.setContentsMargins(6, 4, 6, 4)
        tb.setSpacing(4)

        self.btn_select = QtWidgets.QPushButton("Select Script (.nk / .nk~)")
        self.btn_select.setToolTip("Open a Nuke script to inspect and restore nodes from")
        self.btn_select.setStyleSheet(
            "QPushButton{background:#3a3a3a;color:#ccc;border:1px solid #555;"
            "border-radius:3px;padding:3px 12px;}"
            "QPushButton:hover{background:#4a4a4a;color:#fff;}"
        )
        self.btn_select.clicked.connect(self.on_select_script)

        self.btn_import = QtWidgets.QPushButton("Import Selected Nodes")
        self.btn_import.setToolTip("Paste the selected nodes into the active Nuke session")
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
        self.btn_search_toggle.setToolTip("Search nodes, labels and backdrops  (shows/hides search bar)")
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
        self._search_bar.setStyleSheet("background:#252525; border-bottom:1px solid #333;")
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

        # ── Graph view ────────────────────────────────────────────────────────
        self.graph_view = GraphView(self)
        self._main_layout.addWidget(self.graph_view, 1)

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
        1. btn_info hover → show/hide InfoPanel.
        2. Escape in search box → close search bar.
        3. Any mouse press outside SettingsPanel + ⚙ button → close panel.

        All attribute accesses are guarded with hasattr because the app-level
        event filter is installed during __init__ and can fire before all
        widgets have been assigned to self.
        """
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

        if not filepath:
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
            self.status_bar.showMessage(
                f"Loaded {len(nodes)} node(s) from: {filepath}"
            )

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
        """Save window geometry so it is restored on next open."""
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

        selected_items = [
            i for i in self.graph_view.scene.selectedItems()
            if isinstance(i, (NodeItem, DotItem, BackdropItem))
        ]

        if not selected_items:
            self.status_bar.showMessage(
                "No nodes selected — click or rubber-band select nodes first."
            )
            return

        self.graph_view.import_selected()
        self.status_bar.showMessage(
            f"Import completed — {len(selected_items)} node(s) sent to Nuke."
        )

        if self._settings_panel.close_after_import:
            self.close()

