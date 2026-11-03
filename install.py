#!/usr/bin/env python3
"""Sets up the launcher on Linux or macOS: its Python packages, starting at login
and a Desktop shortcut. Run it as the account that uses the simulator.

    python3 install.py              # install
    python3 install.py --deps-only  # just the Python packages (.venv)
    python3 install.py --uninstall  # remove the login item and shortcuts
"""
import argparse
import ctypes.util
import plistlib
import shutil
import subprocess
import sys
import venv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VENV_PYTHON = BASE_DIR / ".venv" / "bin" / "python"
NEEDED_TOOLS = {
    "linux": {
        "xdotool": "xdotool", "xwininfo": "x11-utils", "import": "imagemagick",
        "tesseract": "tesseract-ocr tesseract-ocr-eng",
    },
    "darwin": {"cliclick": "cliclick", "tesseract": "tesseract"},
}


def desktop_folder(home):
    try:
        folder = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, check=False).stdout.strip()
    except FileNotFoundError:
        folder = ""
    return Path(folder) if folder and folder != str(home) else home / "Desktop"


def desktop_entry_quote(path):
    escaped = str(path)
    for char in ("\\", '"', "`", "$"):
        escaped = escaped.replace(char, f"\\{char}")
    return '"' + escaped.replace("%", "%%") + '"'


def shortcut_files(platform, home, desktop, python, launcher):
    """{path: (contents, is executable)} of everything the install creates."""
    if platform == "darwin":
        agent = plistlib.dumps({
            "Label": "com.golfsimlauncher",
            "ProgramArguments": [str(python), str(launcher)],
            "WorkingDirectory": str(launcher.parent),
            "RunAtLoad": True,
        })
        script = f'#!/bin/sh\nexec "{python}" "{launcher}"\n'.encode()
        return {
            home / "Library/LaunchAgents/com.golfsimlauncher.plist": (agent, False),
            desktop / "Golf Simulator.command": (script, True),
        }
    entry = "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=Golf Simulator",
        f"Exec={desktop_entry_quote(python)} {desktop_entry_quote(launcher)}",
        f"Path={launcher.parent}",
        "Icon=applications-games",
        "Terminal=false",
        "Categories=Game;",
        "",
    ]).encode()
    return {
        home / ".config/autostart/golf-simulator.desktop": (entry, False),
        home / ".local/share/applications/golf-simulator.desktop": (entry, False),
        desktop / "golf-simulator.desktop": (entry, True),
    }


def install_packages():
    if not VENV_PYTHON.exists():
        print(f"Creating {VENV_PYTHON.parent.parent}")
        try:
            venv.create(BASE_DIR / ".venv", with_pip=True)
        except subprocess.CalledProcessError:
            shutil.rmtree(BASE_DIR / ".venv", ignore_errors=True)
            sys.exit("Couldn't create the Python environment. On Ubuntu/Debian: sudo apt install python3-venv")
    subprocess.run([VENV_PYTHON, "-m", "pip", "install", "--quiet", "-r", BASE_DIR / "requirements.txt"], check=True)


def report_missing_tools():
    missing = [pkg for cmd, pkg in NEEDED_TOOLS[sys.platform].items() if not shutil.which(cmd)]
    if sys.platform == "linux" and not ctypes.util.find_library("xcb-cursor"):
        missing.append("libxcb-cursor0")
    if missing:
        command = "brew install" if sys.platform == "darwin" else "sudo apt install"
        print(f"\nStill needed:\n  {command} {' '.join(missing)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--deps-only", action="store_true")
    args = parser.parse_args()
    if sys.platform not in NEEDED_TOOLS:
        sys.exit("This installer is for Linux and macOS; on Windows use install.ps1.")

    home = Path.home()
    desktop = desktop_folder(home)
    files = shortcut_files(sys.platform, home, desktop, VENV_PYTHON, BASE_DIR / "launcher.py")
    if args.uninstall:
        for path in files:
            if path.exists():
                path.unlink()
                print(f"Removed {path}")
        return

    install_packages()
    if not args.deps_only:
        for path, (contents, executable) in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
            path.chmod(0o755 if executable else 0o644)
            print(f"Created {path}")
    report_missing_tools()


if __name__ == "__main__":
    main()
