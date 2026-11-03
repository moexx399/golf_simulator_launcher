# Golf Simulator Launcher

A full-screen, touch-friendly menu for golf simulator PCs on Linux, macOS and Windows. Pick a simulator — or jump straight to its driving range — and the launcher closes whatever was running, starts the supporting apps (OBS, launch-monitor bridges, stream deck servers…), and clicks through the simulator's launch screens for you.

Everything is driven by a single JSON file, so adding a new simulator or changing the boot sequence doesn't require touching any code.

![The launcher menu with GSPro, Square Golf and PurePlay tiles](docs/menu.webp)

> **Linux and macOS:** use the Python launcher described under [Linux and macOS](#linux-and-macos). The rest of this README describes the Windows version (`launcher.ps1`); the config format and step types are shared, with the differences listed in that section.

## Features

- **One-tap boot** — big tiles for each simulator, plus optional shortcut buttons (e.g. *Driving Range*) underneath.
- **Clicks through launcher screens** — presses buttons in normal Windows apps via UI Automation, and in game engines (Unity, Unreal) by reading the screen with Windows' built-in OCR.
- **Clean switching** — closes leftover sim apps (gracefully first, then whole process trees) before every boot, and asks for admin rights once if something was started elevated.
- **Live progress** — every step is shown with a ✓ / – / ✕ as it runs; failures say what went wrong and let you go back to the menu.
- **Stays out of the way** — minimizes to the taskbar when the sim is ready; launching it again brings the existing window back instead of opening a second copy.
- **Starts at login, quickly** — one install script adds it to Startup and the Desktop. When it runs from Startup it turns off Windows' startup-app delay for that account (from the next sign-in; `install.ps1 -Uninstall` restores it), and its native helper is compiled once and cached in `%LOCALAPPDATA%\GolfSimLauncher`.
- **Keyboard support** — `1`–`9` pick a simulator, letter keys trigger shortcut buttons, `Esc` exits.
- **No dependencies** — plain PowerShell 5.1 + WPF, which ship with Windows 10 and 11.

### Included recipes

The sample `launcher.example.json` is a working setup for:

| Simulator | Boot | Shortcut |
|---|---|---|
| **GSPro** | OBS → GSPro launcher → opens GSPconnect's *Visual Data* (Square launch monitor connector) → brings GSPro to the front | **Driving Range (R)** — clicks *Launch GSPro*, *Practice*, *GSPro Practice* |
| **Square Golf** | OBS → Square launcher → *Update* if available, otherwise *Play* → *Sign in* → *Skip* device search | **Driving Range (D)** — clicks *Driving Ranges*, then *Play* |
| **Muni Golf** | Muni Golf Launcher → *Update* if available, otherwise *Play* → waits for the game → brings it to the front | **Driving Range (M)** — clicks *Practice*, *Facilities*, *Muni Driving Range* |
| **PurePlay** | Placeholder (disabled) | — |

More recipes — one per simulator, plus [square_impact](https://github.com/moexx399/square_impact) impact replays for the Square Omni — are in the [`examples`](examples/) folder. Each is a complete config you can try with `-Config examples\<name>.json`.

## Linux and macOS

The simulators in the sample configs are Windows programs, so on these systems they have to run through Wine or Proton (or a similar compatibility layer). Whether a given simulator and its launch monitor work that way is up to them; the launcher only starts and clicks through them. The samples assume the default Wine prefix (`~/.wine`) — change the `check`, `path` and `args` values to match your install.

### Install with Homebrew

```sh
brew install moexx399/tap/golf_simulator_launcher
golf_simulator_launcher --windowed --dry-run     # try it; nothing starts
```

This is a self-contained build, and Homebrew brings the tools it needs. Your `launcher.json` and `launcher.log` go in `~/.local/share/golf_simulator_launcher` (Linux) or `~/Library/Application Support/golf_simulator_launcher` (macOS), and relative paths in the config resolve from the installed build, so use full paths for your own files. It doesn't start at login by itself; add `golf_simulator_launcher` to your desktop's startup apps. The build isn't notarized; macOS may ask you to approve it the first time.

Prebuilt archives for each platform are also on the [releases page](https://github.com/moexx399/golf_simulator_launcher/releases).

### Set up from source

Needs Python 3.10 or newer.

```sh
python3 install.py --deps-only     # installs the Python packages into .venv and lists any tools still needed
.venv/bin/python launcher.py --windowed --dry-run     # try it; nothing starts
python3 install.py                 # start at login, add a Desktop shortcut (--uninstall removes them)
```

The tools it asks for:

| | Install |
|---|---|
| Ubuntu / Debian | `sudo apt install xdotool x11-utils imagemagick tesseract-ocr tesseract-ocr-eng libxcb-cursor0` (and `python3-venv` for the system Python) |
| macOS | `brew install cliclick tesseract` |

On first run `launcher.json` is created from `launcher.linux.example.json` or `launcher.macos.example.json`. Options are spelled `--config`, `--select`, `--windowed` and `--dry-run`.

On macOS, allow the launcher (Terminal, or the Python in `.venv`) under **System Settings → Privacy & Security**, in both *Accessibility* and *Screen Recording*.

### What differs from Windows

| Windows version | Linux and macOS |
|---|---|
| `clickButton` finds buttons through UI Automation | Wine apps don't expose their controls, so it reads the caption with OCR like `clickText`. The app has to be visible, and a **greyed-out button can't be told apart from an active one**: if `PLAY` is on screen but disabled during an update, the click does nothing and the next step waits. Give that next step a generous `timeout` |
| Windows OCR | Tesseract |
| `doneIfWindowClass` compares Windows classes | Compares the X11 `WM_CLASS` on Linux; never matches on macOS. Classes such as `UnityWndClass` don't exist under Wine |
| `kill` offers an admin prompt for elevated apps | Only your own account's processes are seen; anything running as root or another user is left alone |
| `minimized` and `conhost` on `start` | Ignored |
| `path` is a file | Can also be a command on `PATH` (`"wine"`) or, on macOS, an `.app` bundle. `args` is split like a shell command line and expands `$VARIABLES`, but no shell runs |
| Paths use `%VAR%` | Paths use `~` and `$VAR` |

Process names match the program's name with or without `.exe`, ignoring case, including Windows programs under Wine. OBS is `obs` here, and `obsSource` looks in `~/.config/obs-studio/basic/scenes` (Linux) or `~/Library/Application Support/obs-studio/basic/scenes` (macOS). Its `sources` settings are passed to OBS as written, and capture sources have different settings on each system.

### Linux: X11 and Wayland

Clicking and window control go through X11, which also covers Wayland sessions as long as the target is an X11 window. Wine and Proton games normally are (through XWayland), and the launcher's own window runs that way too. **Apps running as native Wayland windows can't be found, focused or clicked**, so `focus`, `clickButton` and `clickText` fail for them. `start`, `kill`, the `wait` steps and `obsSource` work regardless. Run such an app with `QT_QPA_PLATFORM=xcb` or `GDK_BACKEND=x11` if you need to click in it.

### Run the tests

```sh
.venv/bin/python -m unittest discover tests
```

## Requirements (Windows)

- Windows 10 or 11
- Windows PowerShell 5.1 (built in — PowerShell 7 is not required)
- An English (or other OCR-supported) language pack for the screen-reading steps — installed by default on English Windows
- A 4K display is not required, but the sample recipes were tuned on one

## Quick start (Windows)

1. **Download** this repository (or `git clone` it) somewhere permanent, e.g. `C:\GolfLauncher`.
2. **Create your config** — copy `launcher.example.json` to `launcher.json` (the launcher also does this for you on first run) and adjust it to your setup: app paths, your OBS scene collection (`--collection "Name"` in the OBS `args`), and any extra apps to start or close (see [Configuration](#configuration)). Paths can use environment variables like `%LOCALAPPDATA%`, and relative paths are relative to the launcher folder. `launcher.json` is git-ignored, so pulling updates never overwrites your setup.
3. **Try it** without starting anything:

   ```powershell
   powershell -NoProfile -STA -ExecutionPolicy Bypass -File launcher.ps1 -Windowed -DryRun
   ```

4. **Install** it to start at login and add a Desktop shortcut:

   ```powershell
   powershell -ExecutionPolicy Bypass -File install.ps1
   ```

   Run this while signed in as the Windows account that uses the simulator — apps such as OBS and Square keep their settings per account. To install for a different account from an admin account, add `-User <name>`. Remove it again with `-Uninstall`.

### Command-line options

| Option | What it does |
|---|---|
| `-Config <path>` | Use a different config file (default: `launcher.json` next to the script) |
| `-Select gspro` | Boot a simulator immediately, skipping the menu |
| `-Select gspro:range` | Boot a simulator's shortcut action immediately |
| `-Windowed` | Open in a normal window instead of full screen |
| `-DryRun` | Walk through the steps without starting, closing or clicking anything |

## Configuration

`launcher.json` has five parts:

```jsonc
{
    // Window
    "title": "Golf Simulator",
    "subtitle": "Choose a simulator to start",
    "icon": "C:\\path\\to\\icon.ico",
    "fullscreen": true,
    "whenReady": "minimize",     // "minimize" (default), "stay" or "close"
    "autoBootSeconds": 0,        // > 0 starts defaultSim after a countdown
    "defaultSim": "gspro",

    "beforeEachSim": [ ... ],    // steps run before every boot
    "stepSets":      { ... },    // named, reusable lists of steps
    "sims":          [ ... ],    // the tiles on the menu
    "closeAll":      [ ... ]     // steps for the "Close all sim apps" button
}
```

### Simulators

```jsonc
{
    "id": "square",                       // used by -Select
    "name": "Square",
    "subtitle": "Square Golf",
    "accent": "#E8833A",                  // tile colour
    "image": "logos\\square.png",         // optional tile artwork (PNG/JPG; relative paths are
                                          // relative to the launcher folder; SVG isn't supported)
    "check": "%LOCALAPPDATA%\\Programs\\Square Golf Launcher\\Square Golf Launcher.exe",
                                          // tile shows "Not installed" if this file is missing
    "enabled": true,                      // false greys the tile out…
    "status": "Coming soon",              // …with this text
    "steps": [ { "type": "include", "set": "squareBoot" } ],
    "actions": [                          // optional buttons under the tile
        {
            "id": "range", "label": "Driving Range", "key": "D",
            "steps": [ ... ]              // replaces the sim's own steps
        }
    ]
}
```

Action keys are global, so give each action a different letter.

### Steps

Every step has a `type`, and optionally:

- `label` — the text shown while it runs
- `optional: true` — a failure is shown as skipped instead of stopping the boot
- `timeout` — seconds to wait (default 60)

| Type | Does | Options |
|---|---|---|
| `include` | Runs a named list from `stepSets` (can be nested) | `set` |
| `start` | Starts a program | `path`, `args`, `workingDir`, `wait` (wait for it to exit), `minimized`, `skipIfRunning`, `conhost` (open a console app in the classic console instead of Windows Terminal, so the taskbar shows the app's own icon) |
| `kill` | Closes programs by process name, including their child processes, and checks none are left | `names`, `graceful` (ask them to close first; `timeout` is how long to wait) |
| `killCommandLine` | Closes processes whose command line matches a wildcard pattern (e.g. a Chrome app window) | `pattern` |
| `waitProcess` | Waits for a process to appear | `name` |
| `waitPort` | Waits for something to listen on a local TCP port | `port` |
| `delay` | Waits | `seconds` |
| `deleteFiles` | Deletes files matching a wildcard path | `path` |
| `focus` | Brings a program's window to the front | `process`, `settle` (seconds to wait first) |
| `obsSource` | Points OBS at the simulator being started by editing the scene collection before OBS opens — e.g. retarget one shared game-capture source to each simulator's window, and pick the starting scene. Must come before OBS starts; the original collection is backed up once as `<file>.before-launcher` | `collection` (scene collection name), `sources` (`{ "Source name": { "setting": value } }`), `scene`, `folder` (default `%APPDATA%\obs-studio\basic\scenes`) |
| `clickButton` | Presses a button found through Windows UI Automation — works for most normal desktop apps (WinForms, WPF, Qt, Electron) | see below |
| `clickText` | Finds text on a program's window with OCR and clicks it — for games and custom-drawn UIs | see below |

**`clickButton` options**

| Option | Meaning |
|---|---|
| `process` | Process name without `.exe` |
| `text` | The button's name (usually its caption) |
| `clickFirst` | Another button to press first whenever it's enabled — e.g. `"UPDATE"` before `"PLAY"` |
| `doneIfProcess` | Skip if this process is already running (the app moved on by itself) |
| `doneIfWindowClass` | Skip once `process` shows a window of this class (e.g. `UnityWndClass`) |
| `doneIfPort` | Skip once something listens on this port |
| `ifCommandLine` | Only press if `process` was started with a matching command line |
| `settle` | With `clickFirst`: seconds to wait before trusting that it isn't needed (default 2) |

**`clickText` options**

| Option | Meaning |
|---|---|
| `process` | Process name without `.exe` |
| `text` | Words to find. A line that is exactly this text (a button) wins over a sentence that contains it |
| `settle` | Seconds to wait after the text first appears, so menus finish animating (default 0.6) |
| `confirmGone` | Look again after clicking and click again (up to 6 times) while the text is still there. Only use it when the next screen doesn't show the same text |
| `parkMouse` | Move the pointer to the window's corner afterwards |

### Finding button names

For `clickButton`, list what UI Automation can see in a running app:

```powershell
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$ae = [System.Windows.Automation.AutomationElement]
$id = (Get-Process 'Square Golf Launcher').Id
$cond = New-Object System.Windows.Automation.PropertyCondition($ae::ProcessIdProperty, $id)
$ae::RootElement.FindAll('Children', $cond) | ForEach-Object {
    $_.FindAll('Descendants', [System.Windows.Automation.Condition]::TrueCondition) |
        Where-Object { $_.Current.Name } |
        ForEach-Object { '{0,-12} {1}' -f $_.Current.ControlType.ProgrammaticName.Split('.')[1], $_.Current.Name }
}
```

If nothing useful shows up (typical for Unity and Unreal games), use `clickText` with the words printed on the button.

## Troubleshooting

- **Logs** — every boot is written to `launcher.log` next to the script, including where each click landed.
- **A `clickText` step never finds its text** — the target window must be visible and in front; the launcher minimizes itself while it reads the screen. Check the spelling against what's actually shown, and that a supported OCR language is installed (`Get-WindowsCapability -Online -Name Language.OCR*`).
- **A click lands but nothing happens** — some games show buttons before they accept input. Increase `settle` or add `confirmGone: true`.
- **"…is running as administrator and couldn't be closed"** — something was started elevated. Accept the UAC prompt, or close it yourself. Avoid starting sim apps "as administrator".
- **OBS asks about Safe Mode** — add the `deleteFiles` step for `%APPDATA%\obs-studio\.sentinel\run_*` before starting OBS (the sample config does), and the `Run in Normal Mode` fallback click.
- **Wrong OBS scenes or no websocket** — OBS settings are per Windows account; make sure the account running the launcher has the scene collection and has *Tools → WebSocket Server Settings → Enable* turned on.
- **Clicks land in the wrong place on high-DPI screens** — the launcher is DPI-aware; if you run pieces of it from another script, call `SetProcessDPIAware` first.

## Contributing

Contributions are welcome — especially recipes for more simulators and launch monitors.

- **Changelog**: add an entry with [`changie new`](https://changie.dev) and commit it with your change. CI checks for one on pull requests; docs- or CI-only PRs can use the `skip-changelog` label. Dependabot PRs get theirs automatically.

- **New simulator recipes**: add a `sims` entry (and any `stepSets`) to `launcher.example.json` — not your own `launcher.json`, which isn't tracked — using `%LOCALAPPDATA%`/`%ProgramFiles%` style paths rather than your own user folder. Mention the simulator version you tested in your pull request.
- **New step types**: add a `step_<type>` method to `Runner` in `steps.py` and a case to `Invoke-Step` in `launcher.ps1`, keep it working under `-DryRun`, log anything useful with `Write-Log`, and document it in the table above.
- **Keep it dependency-free on Windows**: `launcher.ps1` must run on a fresh Windows install with Windows PowerShell 5.1 — no modules, no PowerShell 7-only syntax.
- **Test** with `-Windowed -DryRun` for layout and flow, then with a real boot.
- **Bugs**: open an issue with the relevant part of `launcher.log`, your Windows version, screen resolution, and a screenshot of the screen it got stuck on.

## Releasing

The version lives in `launcher.py` (`VERSION`), and changie updates it.

```sh
changie batch auto      # collects .changes/unreleased into .changes/vX.Y.Z.md, bumps VERSION
changie merge           # regenerates CHANGELOG.md
git add -A && git commit -m "Release vX.Y.Z"
git tag vX.Y.Z && git push && git push --tags
```

Pushing the tag runs `.github/workflows/build-exe.yml`: it checks the tag matches `VERSION`, builds for macOS and Linux, publishes a GitHub Release with `.changes/vX.Y.Z.md` as the notes, installs the new formula with Homebrew on each platform, and only then pushes it to [the tap](https://github.com/moexx399/homebrew-tap). That last step needs a `HOMEBREW_TAP_TOKEN` secret with write access to the tap.

## Project layout

```
launcher.py             Linux/macOS: the window
steps.py                Linux/macOS: reads the config, runs the steps
desktop.py              Linux/macOS: processes, windows, mouse, OCR
install.py              Linux/macOS: Python packages, login item, Desktop shortcut
launcher.linux.example.json, launcher.macos.example.json    sample configs
tests/                  unit tests for the Python version
packaging/              smoke test for builds, Homebrew formula generator
.changes/, CHANGELOG.md changelog fragments and released notes
launcher.ps1            Windows: the app (UI, step runner, automation helpers)
install.ps1             Windows: adds/removes the Startup and Desktop shortcuts
launcher.example.json   Windows: sample config with the GSPro / Square / PurePlay recipes
launcher.json           your own config (git-ignored; created from the example on first run)
examples/               Windows: one complete recipe per simulator / add-on
*.png                   tile logos
docs/                   README images
launcher.log            created at runtime (git-ignored)
```

## Disclaimer

This project isn't affiliated with GSPro, Square Golf, PurePlay, OBS, or any other product it launches. It only automates clicks on your own machine; it never stores or enters passwords — sign-ins rely on the simulator's own "remember me".

## License

Released under the [MIT License](LICENSE). The software is provided "as is", without warranty of any kind, and the authors aren't liable for any damage or problems arising from its use. It closes programs and clicks on your screen, so try it with `-DryRun` first and use it at your own risk.
