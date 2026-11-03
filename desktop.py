"""Processes, windows, mouse and screen reading on Linux and macOS.

Linux drives X11 windows with xdotool and captures them with ImageMagick. Windows
games under Wine or Proton are X11 windows even in a Wayland session (through
XWayland); apps running as native Wayland windows can't be seen or clicked.
macOS uses System Events, screencapture and cliclick. Both read text with tesseract.
"""
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

import psutil

MAC = sys.platform == "darwin"


class StepError(Exception):
    """A step failed; the message is shown to the user."""


def child_env():
    """The environment for other programs. A packaged build points the library path and
    Qt's variables at its own files, which would break anything it starts (OBS, say)."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        for name in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            original = env.pop(f"{name}_ORIG", None)
            env.pop(name, None)
            if original is not None:
                env[name] = original
        env = {name: value for name, value in env.items() if sys._MEIPASS not in value}
    return env


def tool(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=60, env=child_env(), check=False)
    except FileNotFoundError:
        raise StepError(f"{cmd[0]} isn't installed - see Requirements in the README") from None


# --- processes ---------------------------------------------------------------


def program_name(text):
    """'C:\\GSProV1\\GSPro.exe' and '/usr/bin/GSPro' are both 'gspro'."""
    name = re.split(r"[\\/]", text)[-1].casefold()
    return name.removesuffix(".exe")


def own_processes():
    # Only this account's: another signed-in user's copy of an app must not be
    # closed, waited on or clicked. Zombies have already exited.
    for p in psutil.process_iter(["name", "cmdline", "uids", "status"]):
        info = p.info
        if p.pid == os.getpid() or info["status"] == psutil.STATUS_ZOMBIE:
            continue
        if info["uids"] and info["uids"].real == os.getuid():
            yield p


def find_processes(name):
    # The command line matters: Linux cuts process names to 15 characters, and
    # under Wine it holds the Windows program's path.
    want = program_name(name)
    found = []
    for p in own_processes():
        names = [p.info["name"] or ""] + (p.info["cmdline"] or [""])[:1]
        if want in map(program_name, names):
            found.append(p)
    return found


def command_line(p):
    return " ".join(p.info["cmdline"] or [])


def kill_tree(p):
    try:
        family = p.children(recursive=True) + [p]
    except psutil.Error:
        family = [p]
    for member in family:
        try:
            member.kill()
        except psutil.Error:
            pass


# --- windows and mouse -------------------------------------------------------


@dataclass
class Window:
    id: str
    x: int
    y: int
    w: int
    h: int


def _x_window(window_id):
    # Not xdotool getwindowgeometry: it counts the title bar twice on framed windows.
    out = tool("xwininfo", "-id", window_id).stdout
    numbers = [re.search(rf"{field}:\s+(-?\d+)", out)
               for field in ("Absolute upper-left X", "Absolute upper-left Y", "Width", "Height")]
    if not all(numbers):
        return None  # closed since it was listed
    return Window(window_id, *(int(n.group(1)) for n in numbers))


def _x_windows(pid):
    return tool("xdotool", "search", "--onlyvisible", "--pid", str(pid)).stdout.split()


def _mac_window(pid):
    script = f'''tell application "System Events"
        set p to first process whose unix id is {pid}
        if (count of windows of p) is 0 then return ""
        set {{x, y}} to position of window 1 of p
        set {{w, h}} to size of window 1 of p
        return (x as text) & " " & y & " " & w & " " & h
    end tell'''
    result = tool("osascript", "-e", script)
    if "assistive" in result.stderr:
        raise StepError("Allow the launcher under System Settings > Privacy & Security > Accessibility")
    numbers = result.stdout.split()
    return Window(str(pid), *map(int, numbers)) if len(numbers) == 4 else None


def find_window(process):
    """The process's largest visible window, or None."""
    windows = []
    for p in find_processes(process):
        if MAC:
            windows.append(_mac_window(p.pid))
        else:
            windows.extend(_x_window(w) for w in _x_windows(p.pid))
    return max(filter(None, windows), key=lambda w: w.w * w.h, default=None)


def has_window_class(process, class_name):
    # Windows classes such as UnityWndClass don't exist under Wine; this compares
    # against the X11 WM_CLASS, and macOS has no equivalent.
    if MAC:
        return False
    matching = set(tool("xdotool", "search", "--onlyvisible", "--class", class_name).stdout.split())
    return any(matching.intersection(_x_windows(p.pid)) for p in find_processes(process))


def focus(window):
    """Brings the window to the front; returns whether that worked."""
    if MAC:
        script = f'tell application "System Events" to set frontmost of (first process whose unix id is {window.id}) to true'
        return tool("osascript", "-e", script).returncode == 0
    if tool("xdotool", "windowactivate", window.id).returncode != 0:
        # No window manager that takes activation requests.
        return tool("xdotool", "windowfocus", window.id).returncode == 0
    for _ in range(10):
        if tool("xdotool", "getactivewindow").stdout.strip() == window.id:
            return True
        time.sleep(0.1)
    return False


def click(x, y):
    # Hover first: Unity UI only takes a click on a button the pointer has entered.
    if MAC:
        result = tool("cliclick", "-w", "60", f"m:{x - 6},{y}", f"m:{x},{y}", f"dd:{x},{y}", f"du:{x},{y}")
    else:
        result = tool("xdotool", "mousemove", str(x - 6), str(y), "sleep", "0.06",
                      "mousemove", str(x), str(y), "sleep", "0.15",
                      "mousedown", "1", "sleep", "0.08", "mouseup", "1")
    if result.returncode != 0:
        raise StepError(f"Couldn't click: {result.stderr.strip()}")


def move_mouse(x, y):
    if MAC:
        tool("cliclick", f"m:{x},{y}")
    else:
        tool("xdotool", "mousemove", str(x), str(y))


# --- reading the screen ------------------------------------------------------


@dataclass
class Word:
    text: str
    left: float
    top: float
    right: float
    bottom: float


def normalize_word(text):
    return re.sub(r"[\W_]", "", text).casefold()


def parse_tsv(tsv, window):
    """Tesseract's TSV as lines of words, in screen coordinates."""
    lines = {}
    scale = 1.0  # captures can have more pixels than the window (Retina, upscaling)
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12:
            continue
        level, page, block, par, line = map(int, cols[:5])
        left, top, width, height = map(int, cols[6:10])
        if level == 1 and window.w:
            scale = width / window.w
        text = normalize_word(cols[11])
        if level == 5 and text:
            lines.setdefault((page, block, par, line), []).append(Word(
                text,
                window.x + left / scale,
                window.y + top / scale,
                window.x + (left + width) / scale,
                window.y + (top + height) / scale,
            ))
    return list(lines.values())


def find_text(lines, text):
    """The (x, y) centre of the words, preferring a line that is exactly that text
    (a button) over a sentence that merely contains it."""
    want = [w for w in map(normalize_word, text.split()) if w]
    best = None
    for words in lines:
        seen = [w.text for w in words]
        for i in range(len(seen) - len(want) + 1):
            if seen[i:i + len(want)] != want:
                continue
            whole = len(seen) == len(want)
            if best is None or (whole and not best[1]):
                best = (words[i:i + len(want)], whole)
            break
    if best is None or not want:
        return None
    hit = best[0]
    return (
        int((min(w.left for w in hit) + max(w.right for w in hit)) / 2),
        int((min(w.top for w in hit) + max(w.bottom for w in hit)) / 2),
    )


def _capture(window, path):
    if MAC:
        region = f"{window.x},{window.y},{window.w},{window.h}"
        result = tool("screencapture", "-x", "-R", region, path)
    else:
        # Doubling the size helps tesseract with small launcher text.
        result = tool("import", "-silent", "-window", window.id, "-resize", "200%", f"png:{path}")
    if result.returncode != 0:
        raise StepError(f"Couldn't capture the window: {result.stderr.strip()}")


def read_window(window):
    if window.w < 100 or window.h < 100:
        return []
    with tempfile.TemporaryDirectory(prefix="golf-launcher-") as folder:
        image = os.path.join(folder, "window.png")
        _capture(window, image)
        result = tool("tesseract", image, "stdout", "--psm", "11", "tsv")
    if result.returncode != 0:
        raise StepError(f"Text recognition failed: {result.stderr.strip()}")
    return parse_tsv(result.stdout, window)
