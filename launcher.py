#!/usr/bin/env python3
"""Golf simulator launcher: a full-screen menu for choosing which simulator to boot.

Reads launcher.json (next to this script), shows one tile per simulator and, when
one is picked, runs that simulator's start-up steps with live progress.

Keys: 1-9 pick a tile, arrows + Enter also work, Esc exits to the desktop.
"""
import argparse
import json
import math
import os
import shutil
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

VERSION = "0.1.0"
FROZEN = getattr(sys, "frozen", False)  # running from the packaged build
# The launcher's own files (example configs, logos). Relative paths resolve from here.
BASE_DIR = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent


def parse_args():
    parser = argparse.ArgumentParser(description="Full-screen menu for booting golf simulators.")
    parser.add_argument("--config", help="config file (default: launcher.json in the data folder)")
    parser.add_argument("--select", metavar="SIM[:ACTION]", help="boot this sim immediately, e.g. gspro or gspro:range")
    parser.add_argument("--windowed", action="store_true", help="open in a normal window instead of full screen")
    parser.add_argument("--dry-run", action="store_true", help="walk through the steps without starting anything")
    parser.add_argument("--version", action="version", version=VERSION)
    return parser.parse_args()


# Before Qt loads: --help and --version shouldn't need a graphics stack.
ARGS = parse_args() if __name__ == "__main__" else None

try:
    # QtWidgets must come first: importing QtGui first under Homebrew's Python on Linux
    # loads Homebrew's libxkbcommon next to the system's -x11 one, which segfaults.
    from PySide6.QtWidgets import (  # noqa: I001
        QApplication, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
        QProgressBar, QPushButton, QSizePolicy, QStackedLayout, QVBoxLayout, QWidget,
    )
    from PySide6.QtCore import QRectF, Qt, QTimer, Signal
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    import steps
except ImportError as error:
    hint = "" if FROZEN else "\nRun 'python3 install.py --deps-only', then start it with .venv/bin/python launcher.py"
    sys.exit(f"{error}{hint}")

STYLE = """
#root { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0B1210, stop:1 #13201A); }
QLabel { background: transparent; }
QPushButton { outline: none; }
#card { background: #17221C; border: 3px solid #24322A; border-radius: 18px; }
#card:hover, #card:focus { background: #1E2B24; border-color: #F2F5F3; }
#action { background: #17221C; border: 3px solid #24322A; border-radius: 14px; }
#action:hover, #action:focus { background: #1E2B24; border-color: #F2F5F3; }
#ghost { color: #A9B8B0; background: transparent; border: 2px solid #2C3B33; border-radius: 24px;
         font-size: 20px; padding: 11px 26px; }
#ghost:hover, #ghost:focus { border-color: #F2F5F3; }
#badge { background: #0E1512; border-radius: 10px; padding: 4px 16px; }
"""

GLYPHS = {
    "running": ("▸", "#F2F5F3"),
    "done": ("✓", "#5BD08A"),
    "skipped": ("–", "#6B7E73"),
    "warn": ("!", "#E8B33A"),
    "failed": ("✕", "#F06A6A"),
}


def text_style(size, color="#F2F5F3", bold=False):
    return f"font-size: {size}px; color: {color}; font-weight: {'bold' if bold else 'normal'};"


def make_label(text, size, color="#F2F5F3", bold=False, wrap=False):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)  # config text must never be parsed as markup
    label.setStyleSheet(text_style(size, color, bold))
    label.setWordWrap(wrap)
    label.setAttribute(Qt.WA_TransparentForMouseEvents)
    return label


def make_badge(text, size):
    badge = make_label(text, size, "#8FA398", True)
    badge.setObjectName("badge")
    badge.setStyleSheet(f"#badge {{ {text_style(size, '#8FA398', True)} }}")
    return badge


def dim(widget):
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.45)
    widget.setGraphicsEffect(effect)


class TileArt(QWidget):
    """A tile's logo, centred in a padded box so wide logos don't touch the edges
    and tall ones leave room for the title. No logo: a large, faint initial."""

    def __init__(self, image, initial, accent):
        super().__init__()
        self.pixmap = QPixmap(image) if image and os.path.exists(image) else QPixmap()
        self.initial = initial
        self.accent = accent
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        area = QRectF(self.rect()).adjusted(0, 48, 0, 0)
        if self.pixmap.isNull():
            color = QColor(self.accent)
            color.setAlphaF(0.14)
            painter.setPen(color)
            font = QFont(self.font())
            font.setPixelSize(min(260, self.height()))
            font.setWeight(QFont.Black)
            painter.setFont(font)
            painter.drawText(QRectF(self.rect()), Qt.AlignRight | Qt.AlignBottom, self.initial)
            return
        box = area.adjusted(area.width() / 8, area.height() / 6, -area.width() / 8, -area.height() / 6)
        scale = min(box.width() / self.pixmap.width(), box.height() / self.pixmap.height())
        width, height = self.pixmap.width() * scale, self.pixmap.height() * scale
        target = QRectF(box.center().x() - width / 2, box.center().y() - height / 2, width, height)
        painter.drawPixmap(target, self.pixmap, QRectF(self.pixmap.rect()))


class Launcher(QWidget):
    on_ui = Signal(object)  # the boot thread passes functions to run on the UI thread

    def __init__(self, config, args):
        super().__init__()
        self.config = config
        self.args = args
        self.log_file = data_dir() / "launcher.log"
        if self.log_file.exists() and self.log_file.stat().st_size > 1_000_000:
            self.log_file.unlink()
        self.runner = steps.Runner(BASE_DIR, args.dry_run, self.log, self.hide_launcher)
        self.sims = config.get("sims", [])
        self.fullscreen = config.get("fullscreen", True) and not args.windowed
        self.busy = False
        self.hidden = False
        self.auto_boot_at = None
        self.auto_boot_sim = None
        self.cards = []
        self.action_keys = {}
        self.step_line = None
        self.on_ui.connect(self.call_on_ui)

        self.setObjectName("root")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet(STYLE)
        self.setWindowTitle("Golf Simulator")
        icon = steps.expand_path(config.get("icon"), BASE_DIR)
        if icon and os.path.exists(icon):
            self.setWindowIcon(QIcon(icon))

        self.stack = QStackedLayout(self)
        self.stack.addWidget(self.build_menu())
        self.stack.addWidget(self.build_run_view())

        self.timer = QTimer(self, interval=250, timeout=self.tick)
        self.timer.start()
        self.tick()
        QApplication.instance().installEventFilter(self)

    def call_on_ui(self, function):
        function()

    def log(self, message):
        try:
            with self.log_file.open("a", encoding="utf-8") as out:
                out.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}\n")
        except OSError:
            pass

    # --- building the window ---------------------------------------------------

    def build_menu(self):
        menu = QWidget()
        layout = QVBoxLayout(menu)
        layout.setContentsMargins(72, 44, 72, 40)

        self.subtitle_base = self.config.get("subtitle", "Choose a simulator to start")
        if self.args.dry_run:
            self.subtitle_base += "   (dry run - nothing will start)"
        self.clock = make_label("", 30, "#8FA398")
        self.subtitle = make_label(self.subtitle_base, 24, "#8FA398")
        heading = QVBoxLayout()
        heading.addWidget(make_label(self.config.get("title", "Golf Simulator"), 58, bold=True))
        heading.addWidget(self.subtitle)
        top = QHBoxLayout()
        top.addLayout(heading, 1)
        top.addWidget(self.clock, 0, Qt.AlignTop)
        layout.addLayout(top)
        layout.addSpacing(12)

        tiles = QHBoxLayout()
        tiles.setSpacing(32)
        most_actions = max((len(sim.get("actions") or []) for sim in self.sims), default=0)
        for index, sim in enumerate(self.sims):
            tiles.addLayout(self.build_column(index, sim, most_actions), 1)
        layout.addLayout(tiles, 1)
        layout.addSpacing(14)

        hints = [f"Press 1-{len(self.sims)} or tap a simulator"]
        for sim in self.sims:
            for action in sim.get("actions") or []:
                if action.get("key"):
                    hints.append(f"{action['key']} for {sim['name']} {action['label']}")
        hints.append("Esc for desktop")
        self.default_hint = "   ·   ".join(hints)
        self.hint = make_label(self.default_hint, 20, "#6B7E73", wrap=True)
        close_all = self.make_ghost("Close all sim apps", self.close_all)
        leave = self.make_ghost("Exit to desktop", self.close)
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        bottom.addWidget(self.hint, 1)
        bottom.addWidget(close_all)
        bottom.addWidget(leave)
        layout.addLayout(bottom)
        return menu

    def make_ghost(self, text, handler):
        button = QPushButton(text, objectName="ghost", cursor=Qt.PointingHandCursor)
        button.clicked.connect(lambda: handler())
        return button

    def build_column(self, index, sim, most_actions):
        ok, status = steps.sim_state(sim, BASE_DIR)
        accent = sim.get("accent", steps.DEFAULT_ACCENT)
        column = QVBoxLayout()
        column.setSpacing(12)
        card = self.build_card(index, sim, ok, status, accent)
        self.cards.append((card, sim))
        column.addWidget(card, 1)

        actions = sim.get("actions") or []
        for action in actions:
            column.addWidget(self.build_action(sim, action, ok, accent))
        # Columns with fewer actions get spacers so all tiles stay the same height.
        for _ in range(most_actions - len(actions)):
            column.addSpacing(76 + column.spacing())
        return column

    def build_card(self, index, sim, ok, status, accent):
        card = QPushButton(objectName="card", cursor=Qt.PointingHandCursor)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        card.setEnabled(ok)
        card.clicked.connect(lambda: self.start_sim(sim))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(0)

        bar = QFrame()
        bar.setFixedHeight(10)
        bar.setAttribute(Qt.WA_TransparentForMouseEvents)
        bar.setStyleSheet(f"background: {accent}; border-top-left-radius: 15px; border-top-right-radius: 15px;")
        layout.addWidget(bar)

        art_area = QGridLayout()
        art_area.setContentsMargins(32, 26, 32, 12)
        image = steps.expand_path(sim.get("image"), BASE_DIR)
        art_area.addWidget(TileArt(image, sim["name"][:1], accent), 0, 0)
        art_area.addWidget(make_badge(str(index + 1), 24), 0, 0, Qt.AlignLeft | Qt.AlignTop)
        layout.addLayout(art_area, 1)

        text = QVBoxLayout()
        text.setContentsMargins(32, 0, 32, 36)
        text.setSpacing(6)
        text.addWidget(make_label(sim["name"], 56, bold=True, wrap=True))
        text.addWidget(make_label(sim.get("subtitle", ""), 24, "#A9B8B0", wrap=True))
        text.addSpacing(10)
        text.addWidget(make_label(status, 20, "#6B7E73" if ok else "#E8B33A", wrap=True))
        layout.addLayout(text)

        if not ok:
            dim(card)
        return card

    def build_action(self, sim, action, ok, accent):
        button = QPushButton(objectName="action", cursor=Qt.PointingHandCursor)
        button.setFixedHeight(76)
        button.setStyleSheet(f"#action {{ border-color: {accent}; }} #action:hover, #action:focus {{ border-color: #F2F5F3; }}")
        button.setEnabled(ok)
        button.clicked.connect(lambda: self.start_sim(sim, action))
        row = QHBoxLayout(button)
        row.setContentsMargins(28, 0, 22, 0)
        row.addWidget(make_label(action["label"], 26, bold=True), 1)
        key = str(action.get("key", "")).upper()
        if key:
            row.addWidget(make_badge(key, 20), 0, Qt.AlignVCenter)
            self.action_keys[key] = (sim, action, button)
        if not ok:
            dim(button)
        return button

    def build_run_view(self):
        view = QWidget()
        outer = QHBoxLayout(view)
        panel = QWidget()
        panel.setFixedWidth(760)
        layout = QVBoxLayout(panel)
        self.run_accent = QFrame()
        self.run_accent.setFixedSize(120, 6)
        self.run_title = make_label("", 50, bold=True, wrap=True)
        self.run_progress = QProgressBar(textVisible=False)
        self.run_progress.setFixedHeight(8)
        self.run_steps = QVBoxLayout()
        self.run_steps.setSpacing(10)
        back = self.make_ghost("Back to menu", self.show_menu)
        leave = self.make_ghost("Exit to desktop", self.close)
        self.back_button = back
        self.run_buttons = QWidget()
        buttons = QHBoxLayout(self.run_buttons)
        buttons.setContentsMargins(0, 18, 0, 0)
        buttons.setSpacing(12)
        buttons.addWidget(back)
        buttons.addWidget(leave)
        buttons.addStretch(1)

        layout.addStretch(1)
        layout.addWidget(self.run_accent)
        layout.addSpacing(22)
        layout.addWidget(self.run_title)
        layout.addSpacing(24)
        layout.addWidget(self.run_progress)
        layout.addSpacing(22)
        layout.addLayout(self.run_steps)
        layout.addWidget(self.run_buttons)
        layout.addStretch(1)
        outer.addStretch(1)
        outer.addWidget(panel)
        outer.addStretch(1)
        return view

    # --- showing and hiding ----------------------------------------------------

    def show_window(self):
        if self.fullscreen:
            self.showFullScreen()
        else:
            self.showNormal()

    def restore(self):
        self.hidden = False
        self.show_window()
        self.raise_()
        self.activateWindow()

    def hide_launcher(self):
        # Called from the boot thread; give the desktop a moment to uncover the app.
        if not self.hidden:
            self.hidden = True
            self.on_ui.emit(self.showMinimized)
            time.sleep(0.4)

    def show_menu(self):
        self.stack.setCurrentIndex(0)
        self.hint.setText(self.default_hint)
        for card, _ in self.cards:
            if card.isEnabled():
                card.setFocus()
                break

    # --- booting ---------------------------------------------------------------

    def add_step_line(self, text):
        row = QHBoxLayout()
        glyph = make_label("", 26)
        glyph.setFixedWidth(44)
        glyph.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        label = make_label(text, 26, wrap=True)
        row.addWidget(glyph)
        row.addWidget(label, 1)
        self.run_steps.addLayout(row)
        self.step_line = (glyph, label)

    def set_step_state(self, state, detail=""):
        glyph, label = self.step_line
        symbol, color = GLYPHS[state]
        glyph.setText(symbol)
        glyph.setStyleSheet(text_style(26, color))
        label.setStyleSheet(text_style(26, "#F2F5F3" if state in ("running", "done") else color))
        if detail:
            label.setText(f"{label.text()}\n{detail}")

    def clear_step_lines(self):
        while self.run_steps.count():
            row = self.run_steps.takeAt(0).layout()
            while row.count():
                row.takeAt(0).widget().deleteLater()
            row.deleteLater()

    def start_sim(self, sim, action=None):
        """Boots a sim; with an action (e.g. Driving Range) its steps replace the sim's own."""
        if self.busy:
            return
        self.busy = True
        self.auto_boot_at = None
        name = f"{sim['name']} – {action['label']}" if action else sim["name"]
        accent = sim.get("accent", steps.DEFAULT_ACCENT)
        self.run_accent.setStyleSheet(f"background: {accent}; border-radius: 3px;")
        self.run_progress.setStyleSheet(
            f"QProgressBar {{ background: #24322A; border: none; }} QProgressBar::chunk {{ background: {accent}; }}")
        self.run_title.setText(f"Starting {name}")
        self.clear_step_lines()
        self.run_buttons.hide()
        self.stack.setCurrentIndex(1)
        self.log(f"=== Starting {name}{' (dry run)' if self.args.dry_run else ''}")
        threading.Thread(target=self.boot, args=(sim, action, name), daemon=True).start()

    def boot(self, sim, action, name):
        # Runs on its own thread so the window keeps painting during long waits.
        try:
            own = (action or sim).get("steps")
            all_steps = steps.resolve_steps(self.config, self.config.get("beforeEachSim")) \
                + steps.resolve_steps(self.config, own)
        except Exception as error:  # noqa: BLE001 - shown on screen, whatever it is
            message = str(error)
            self.on_ui.emit(lambda: self.add_step_line("Reading launcher.json"))
            self.on_ui.emit(lambda: self.set_step_state("failed", message))
            self.on_ui.emit(lambda: self.show_failure(name))
            return

        total = max(1, len(all_steps))
        self.on_ui.emit(lambda: (self.run_progress.setMaximum(total), self.run_progress.setValue(0)))
        for number, step in enumerate(all_steps, 1):
            text = step.get("label", step.get("type", "step"))
            self.on_ui.emit(lambda t=text: (self.add_step_line(t), self.set_step_state("running")))
            try:
                result = self.runner.run(step)
                self.log(f"  {result}  {text}")
                self.on_ui.emit(lambda r=result: self.set_step_state(r))
            except Exception as error:  # noqa: BLE001 - shown on screen, whatever it is
                message = str(error)
                if not step.get("optional"):
                    self.log(f"  FAIL  {text} - {message}")
                    self.on_ui.emit(lambda m=message: self.set_step_state("failed", m))
                    self.on_ui.emit(lambda: self.show_failure(name))
                    return
                self.log(f"  warn  {text} - {message}")
                self.on_ui.emit(lambda m=message: self.set_step_state("warn", m))
            self.on_ui.emit(lambda n=number: self.run_progress.setValue(n))

        self.log(f"=== {name} ready")
        self.on_ui.emit(lambda: self.run_title.setText(f"{name} is ready"))
        if not self.hidden:
            time.sleep(1.5)
        self.on_ui.emit(lambda: self.finish_boot(name))

    def show_failure(self, name):
        self.restore()
        self.run_title.setText(f"Couldn't start {name}")
        self.run_buttons.show()
        self.back_button.setFocus()
        self.busy = False

    def finish_boot(self, name):
        # whenReady: "minimize" (default) parks the menu in the taskbar for switching
        # sims later; "close" exits; "stay" leaves the progress screen up.
        when_ready = self.config.get("whenReady", "minimize")
        if when_ready == "close":
            self.close()
        elif when_ready == "stay":
            self.restore()
            self.run_buttons.show()
            self.busy = False
        else:
            self.hidden = True
            self.showMinimized()
            self.show_menu()
            self.subtitle.setText(f"{name} is running   ·   pick a simulator to switch")
            self.busy = False

    def close_all(self):
        if self.busy:
            return
        self.busy = True
        self.hint.setText("Closing sim apps...")
        threading.Thread(target=self.close_all_thread, daemon=True).start()

    def close_all_thread(self):
        try:
            for step in steps.resolve_steps(self.config, self.config.get("closeAll")):
                try:
                    self.runner.run(step)
                except Exception as error:  # noqa: BLE001 - one app failing mustn't stop the rest
                    self.log(f"  closeAll: {error}")
        except Exception as error:  # noqa: BLE001 - logged, and the button must recover
            self.log(f"  closeAll: {error}")
        self.log("=== Closed sim apps")

        def done():
            self.subtitle.setText(self.subtitle_base)
            self.hint.setText("Sim apps closed.")
            self.busy = False

        self.on_ui.emit(done)

    # --- events ----------------------------------------------------------------

    def stop_auto_boot(self):
        if self.auto_boot_at:
            self.auto_boot_at = None
            self.hint.setText(self.default_hint)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in (event.Type.MouseButtonPress, event.Type.TouchBegin):
            self.stop_auto_boot()
        elif kind == event.Type.KeyPress:
            self.stop_auto_boot()
            if not self.busy and self.handle_key(event):
                return True
        return False

    def handle_key(self, event):
        key = event.key()
        text = event.text().upper()
        in_menu = self.stack.currentIndex() == 0
        if key == Qt.Key_Escape:
            self.close()
        elif in_menu and text in tuple("123456789"):
            index = int(text) - 1
            if index < len(self.cards) and self.cards[index][0].isEnabled():
                self.start_sim(self.cards[index][1])
        elif in_menu and text in self.action_keys:
            sim, action, button = self.action_keys[text]
            if button.isEnabled():
                self.start_sim(sim, action)
        elif key in (Qt.Key_Right, Qt.Key_Down, Qt.Key_Left, Qt.Key_Up):
            self.focusNextPrevChild(key in (Qt.Key_Right, Qt.Key_Down))
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            focused = QApplication.focusWidget()
            if isinstance(focused, QPushButton):
                focused.click()
        else:
            return False
        return True

    def tick(self):
        self.clock.setText(f"{datetime.now():%I:%M %p}".lstrip("0"))
        if self.auto_boot_at and not self.busy:
            left = math.ceil((self.auto_boot_at - datetime.now()).total_seconds())
            if left <= 0:
                self.auto_boot_at = None
                self.start_sim(self.auto_boot_sim)
            else:
                self.hint.setText(
                    f"Starting {self.auto_boot_sim['name']} in {left} s   ·   press any key or tap to choose")

    def begin(self):
        self.show_menu()
        select = self.args.select
        if select:
            sim_id, _, action_id = select.partition(":")
            sim = next((s for s in self.sims if s.get("id") == sim_id), None)
            action = None
            if sim and action_id:
                action = next((a for a in sim.get("actions") or [] if a.get("id") == action_id), None)
            if sim and steps.sim_state(sim, BASE_DIR)[0] and (not action_id or action):
                self.start_sim(sim, action)
            else:
                self.hint.setText(f"'{select}' isn't available - pick a simulator")
            return
        seconds = int(self.config.get("autoBootSeconds", 0))
        default = next((s for s in self.sims if s.get("id") == self.config.get("defaultSim")), None)
        if seconds > 0 and default and steps.sim_state(default, BASE_DIR)[0]:
            self.auto_boot_sim = default
            self.auto_boot_at = datetime.now() + timedelta(seconds=seconds)


def data_dir():
    """Where launcher.json and launcher.log live: next to the script, or for a packaged
    build (whose files are replaced on upgrade) in the user's data folder."""
    if not FROZEN:
        return BASE_DIR
    if sys.platform == "darwin":
        folder = Path.home() / "Library/Application Support"
    else:
        folder = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    folder = folder / "golf_simulator_launcher"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def default_config():
    # First run from a fresh copy of the repo: start from the example for this OS.
    config = data_dir() / "launcher.json"
    example = BASE_DIR / f"launcher.{'macos' if sys.platform == 'darwin' else 'linux'}.example.json"
    if not config.exists() and example.exists():
        shutil.copyfile(example, config)
    return config


def main(args):
    qt_args = sys.argv[:1]
    if sys.platform != "darwin" and os.environ.get("DISPLAY") and not os.environ.get("QT_QPA_PLATFORM"):
        # X11 even in a Wayland session: Wayland apps can't restore themselves from
        # minimized or be raised by another launcher instance. Still better than not
        # opening if Qt's X11 support is missing its libxcb-cursor0. An argument, not
        # the environment variable, so the apps it starts don't inherit the choice.
        qt_args += ["-platform", "xcb;wayland"]
    app = QApplication(qt_args)
    QGuiApplication.setDesktopFileName("golf-simulator")

    # Only one launcher at a time. Opening it again (desktop shortcut, login item)
    # brings the running one back instead.
    server_name = f"GolfSimLauncher-{os.getuid()}"
    other = QLocalSocket()
    other.connectToServer(server_name)
    if other.waitForConnected(300):
        other.disconnectFromServer()
        return 0
    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    server.listen(server_name)

    path = args.config or default_config()
    try:
        config = steps.load_config(path)
    except (OSError, json.JSONDecodeError) as error:
        QMessageBox.critical(None, "Golf Simulator Launcher", f"Couldn't read {path}\n\n{error}")
        return 1

    window = Launcher(config, args)
    server.newConnection.connect(window.restore)
    window.setMinimumSize(900, 600)
    window.resize(1400, 860)
    window.show_window()
    window.log(f"Launcher opened{' (dry run)' if args.dry_run else ''}")
    QTimer.singleShot(0, window.begin)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(ARGS))
