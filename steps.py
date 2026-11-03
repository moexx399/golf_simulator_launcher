"""Reads launcher.json and runs the start-up steps it lists.

Step types are the methods named step_<type> on Runner. Every step also accepts
"label" (text shown on screen) and "optional": true (a failure shows a warning
instead of stopping the boot).
"""
import fnmatch
import glob
import json
import os
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import psutil

import desktop
from desktop import StepError

DEFAULT_ACCENT = "#3FA66B"
OBS_CONFIG = "~/Library/Application Support/obs-studio" if desktop.MAC else "~/.config/obs-studio"


def load_config(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def resolve_steps(config, steps):
    """Flattens "include" steps into the step sets they name."""
    resolved = []
    for step in steps or []:
        if step.get("type") == "include":
            step_set = config.get("stepSets", {}).get(step.get("set"))
            if step_set is None:
                raise StepError(f"Unknown step set '{step.get('set')}' in launcher.json")
            resolved.extend(resolve_steps(config, step_set))
        else:
            resolved.append(step)
    return resolved


def expand_path(path, base_dir):
    # Relative paths (e.g. a logo next to the script) are relative to the launcher folder.
    if not path:
        return path
    return os.path.join(base_dir, os.path.expanduser(os.path.expandvars(path)))


def find_program(path, base_dir):
    """A "start" step's program: a file, or a bare command on PATH like "wine"."""
    if os.sep not in path and shutil.which(path):
        return shutil.which(path)
    return expand_path(path, base_dir)


def sim_state(sim, base_dir):
    """(can it be started, the text shown on its tile)"""
    if not sim.get("enabled", True):
        return False, sim.get("status", "Coming soon")
    check = sim.get("check")
    if check and not os.path.exists(find_program(check, base_dir)):
        return False, "Not installed - set its path in launcher.json"
    return True, sim.get("status", "")


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.25):
            return True
    except OSError:
        return False


def wait_until(condition, timeout, poll=0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(poll)
    return bool(condition())


def split_args(args):
    tokens = shlex.split(args) if isinstance(args, str) else list(args or [])
    return [os.path.expandvars(t) for t in tokens]


def write_json_atomic(path, document):
    # A crash mid-write must not cost the user their OBS scenes.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".launcher-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(document, out, indent=4, ensure_ascii=False)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


class Runner:
    def __init__(self, base_dir, dry_run=False, log=lambda message: None, hide_launcher=lambda: None):
        self.base_dir = str(base_dir)
        self.dry_run = dry_run
        self.log = log
        self.hide_launcher = hide_launcher  # steps that read the screen need the app in front

    def run(self, step):
        """Runs one step; returns 'done' or 'skipped', raises StepError on failure."""
        if self.dry_run:
            time.sleep(0.4)
            return "done"
        handler = getattr(self, f"step_{step.get('type')}", None)
        if handler is None:
            raise StepError(f"Unknown step type '{step.get('type')}'")
        return handler(step)

    def step_kill(self, step):
        names = step["names"]

        def running():
            return [p for name in names for p in desktop.find_processes(name)]

        procs = running()
        if not procs:
            return "skipped"
        if step.get("graceful"):
            for p in procs:
                try:
                    p.terminate()
                except psutil.Error:
                    pass
            psutil.wait_procs(procs, timeout=step.get("timeout", 8))

        # Force-kill whole process trees (a sim can be a wrapper, a main process
        # and workers), then make sure no copy is left behind.
        def sweep():
            left = running()
            for p in left:
                desktop.kill_tree(p)
            return not left

        if wait_until(sweep, 10):
            return "done"
        left = sorted({p.info["name"] for p in running()})
        raise StepError(f"{', '.join(left)} couldn't be closed. Close it yourself, then try again.")

    def step_killCommandLine(self, step):
        pattern = step["pattern"].casefold()
        for p in desktop.own_processes():
            if fnmatch.fnmatchcase(desktop.command_line(p).casefold(), pattern):
                desktop.kill_tree(p)
        return "done"

    def step_start(self, step):
        path = find_program(step["path"], self.base_dir)
        if not os.path.exists(path):
            raise StepError(f"File not found: {path}")
        name = Path(path).stem
        if step.get("skipIfRunning") and desktop.find_processes(name):
            return "skipped"
        args = split_args(step.get("args"))
        wait = step.get("wait")
        if desktop.MAC and path.endswith(".app"):
            command = ["open", *(["-W"] if wait else []), path, *(["--args", *args] if args else [])]
        else:
            command = [path, *args]
        working_dir = expand_path(step.get("workingDir"), self.base_dir) or os.path.dirname(path)
        # Its own session, so apps outlive the launcher and a Ctrl+C in its terminal.
        proc = subprocess.Popen(command, cwd=working_dir, env=desktop.child_env(), start_new_session=True, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if wait:
            timeout = step.get("timeout", 60)
            if not wait_until(lambda: proc.poll() is not None, timeout):
                raise StepError(f"Still running after {timeout} s")
        return "done"

    def step_waitPort(self, step):
        timeout = step.get("timeout", 60)
        if not wait_until(lambda: port_open(step["port"]), timeout):
            raise StepError(f"Nothing answered on port {step['port']} after {timeout} s")
        return "done"

    def step_waitProcess(self, step):
        timeout = step.get("timeout", 60)
        if not wait_until(lambda: desktop.find_processes(step["name"]), timeout):
            raise StepError(f"{step['name']} didn't start within {timeout} s")
        return "done"

    def step_deleteFiles(self, step):
        # e.g. OBS's crash markers, so it doesn't ask about Safe Mode after a forced close.
        files = glob.glob(expand_path(step["path"], self.base_dir))
        if not files:
            return "skipped"
        for file in files:
            if os.path.isdir(file) and not os.path.islink(file):
                shutil.rmtree(file, ignore_errors=True)
            else:
                Path(file).unlink(missing_ok=True)
        self.log(f"    removed {len(files)} file(s)")
        return "done"

    def step_obsSource(self, step):
        # Points OBS sources at this simulator (e.g. the capture's window) by editing
        # the scene collection before OBS starts. OBS saves the collection when it
        # closes, so it must not be running.
        if desktop.find_processes("obs") or desktop.find_processes("obs64"):
            raise StepError("OBS is running - put this step before OBS starts")
        folder = expand_path(step.get("folder", f"{OBS_CONFIG}/basic/scenes"), self.base_dir)
        collection = step["collection"]
        file = doc = None
        for candidate in sorted(glob.glob(os.path.join(glob.escape(folder), "*.json"))):
            try:
                loaded = json.loads(Path(candidate).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(loaded, dict) and loaded.get("name") == collection:
                file, doc = candidate, loaded
                break
        if file is None:
            raise StepError(f"No OBS scene collection named '{collection}' in {folder}")

        changed = []
        sources = doc.get("sources", [])
        for source_name, settings in step.get("sources", {}).items():
            source = next((s for s in sources if s.get("name") == source_name), None)
            if source is None:
                raise StepError(f"OBS collection '{collection}' has no source named '{source_name}'")
            current = source.setdefault("settings", {})
            for key, value in settings.items():
                if current.get(key) != value:
                    current[key] = value
                    changed.append(f"{source_name}.{key}")
        scene = step.get("scene")
        if scene:
            if not any(s.get("id") == "scene" and s.get("name") == scene for s in sources):
                raise StepError(f"OBS collection '{collection}' has no scene named '{scene}'")
            for key in ("current_scene", "current_program_scene"):
                if doc.get(key) != scene:
                    doc[key] = scene
                    changed.append(key)
        if not changed:
            return "skipped"

        # Keep the collection as it was before the launcher first touched it.
        backup = f"{file}.before-launcher"
        if not os.path.exists(backup):
            shutil.copy2(file, backup)
        write_json_atomic(file, doc)
        self.log(f"    OBS: set {', '.join(changed)}")
        return "done"

    def step_delay(self, step):
        time.sleep(step.get("seconds", 1))
        return "done"

    def step_focus(self, step):
        time.sleep(step.get("settle", 0))
        self.hide_launcher()
        process = step["process"]
        timeout = step.get("timeout", 60)
        found = []

        def window_found():
            found[:] = filter(None, [desktop.find_window(process)])
            return bool(found)

        if not wait_until(window_found, timeout):
            raise StepError(f"No {process} window to bring forward")
        if not desktop.focus(found[0]):
            raise StepError(f"Couldn't bring {process} to the front")
        return "done"

    def _locate(self, process, text):
        """(window, point) of the text on the process's window, or None."""
        window = desktop.find_window(process)
        if window is None:
            return None
        desktop.focus(window)
        point = desktop.find_text(desktop.read_window(window), text)
        return (window, point) if point else None

    def _click(self, located, text, note=""):
        window, (x, y) = located
        desktop.focus(window)
        time.sleep(0.2)
        desktop.click(x, y)
        self.log(f"    clicked '{text}' at {x},{y}{note}")

    def step_clickButton(self, step):
        # Wine apps don't expose their controls to AT-SPI or the macOS accessibility
        # API, so buttons are found by reading their captions off the screen.
        process, text = step["process"], step["text"]
        timeout = step.get("timeout", 60)
        only_if = step.get("ifCommandLine")
        if only_if:
            # e.g. only press "Play!" when GSPro was started with its screen selector.
            if not wait_until(lambda: desktop.find_processes(process), timeout):
                raise StepError(f"{process} didn't start within {timeout} s")
            pattern = only_if.casefold()
            if not any(fnmatch.fnmatchcase(desktop.command_line(p).casefold(), pattern)
                       for p in desktop.find_processes(process)):
                return "skipped"
        self.hide_launcher()

        # clickFirst: press this button first whenever it's on screen (e.g. Square's
        # UPDATE), then keep waiting for the main one (PLAY) until the timeout.
        first = step.get("clickFirst")
        first_done = not first
        already_up = False
        button = None

        def ready():
            nonlocal already_up, first_done, button
            if (step.get("doneIfProcess") and desktop.find_processes(step["doneIfProcess"])) \
                    or (step.get("doneIfWindowClass") and desktop.has_window_class(process, step["doneIfWindowClass"])) \
                    or (step.get("doneIfPort") and port_open(step["doneIfPort"])):
                already_up = True
                return True
            if not first_done:
                pre = self._locate(process, first)
                if pre:
                    self._click(pre, first)
                    first_done = True
                    return False
            button = self._locate(process, text)
            return bool(button)

        deadline = time.monotonic() + timeout
        while True:
            found = wait_until(ready, max(1, deadline - time.monotonic()))
            if already_up:
                return "skipped"
            if not found:
                raise StepError(f"Couldn't find the '{text}' button after {timeout} s")
            if first_done:
                break
            # The main button can show for a moment before the update check
            # finishes, so give the first button a chance to appear.
            time.sleep(step.get("settle", 2))
            if not self._locate(process, first):
                first_done = True
        self._click(button, text)
        return "done"

    def step_clickText(self, step):
        process, text = step["process"], step["text"]
        timeout = step.get("timeout", 60)
        self.hide_launcher()
        hit = None

        def on_screen():
            nonlocal hit
            hit = self._locate(process, text)
            return bool(hit)

        if not wait_until(on_screen, timeout):
            raise StepError(f"Couldn't find '{text}' on screen after {timeout} s")

        # Menus slide/fade in; look again once they settle so the click lands on the button.
        time.sleep(step.get("settle", 0.6))
        hit = self._locate(process, text) or hit
        # confirmGone: the screen may show the button before it accepts clicks, so
        # keep clicking until the text goes away (only for text the next screen lacks).
        tries = 6 if step.get("confirmGone") else 1
        for attempt in range(1, tries + 1):
            self._click(hit, text, f" (try {attempt})")
            if attempt == tries:
                break
            time.sleep(2)
            still = self._locate(process, text)
            if not still:
                break
            hit = still
        if step.get("parkMouse"):
            window = hit[0]
            desktop.move_mouse(window.x + window.w - 2, window.y + window.h - 2)
        return "done"
