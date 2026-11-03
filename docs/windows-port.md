# Porting the PowerShell launcher into the Python app

Instructions for an agent working on a Windows machine.

## Goal

Make the Python app (`launcher.py`, `steps.py`, `desktop.py`, `install.py`) run on Windows 10 and 11 with everything `launcher.ps1` and `install.ps1` do today, so one codebase serves Windows, Linux and macOS.

`launcher.ps1` is the specification. It has been used on a real simulator PC; the Python app has never run on Windows. Where this document and the PowerShell disagree about behaviour, the PowerShell wins. Tell the user about the disagreement.

## Rules

- **Don't delete or change `launcher.ps1`, `install.ps1` or `launcher.example.json`.** Removing the PowerShell version is the user's decision, after the checklist at the end passes.
- **Don't break Linux or macOS.** Their code paths must behave as they do now. The existing tests must keep passing there; CI will check once it's pushed.
- **Real boots have side effects.** A non-dry-run `kill` step force-closes OBS and simulators, `obsSource` edits the user's OBS scene files, and click steps take over the mouse. Use `--dry-run` and the test window described below. Ask the user before each real boot, and let them save their work first.
- **Work on a branch.** Commit or push only if the user asks.
- **Match the surrounding code:** small functions, comments that explain why, no speculative options.
- **Check what you can't assume.** The Windows API notes below are written from another OS and have not been run. If one turns out to be wrong, follow what you observe and report it.

## Set up

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m unittest discover -s tests
```

Expect this to fail at first: the code is Unix-only today. `launcher.ps1` is still runnable for comparison:

```powershell
powershell -NoProfile -STA -ExecutionPolicy Bypass -File launcher.ps1 -Windowed -DryRun
```

## Where the code goes

Put Windows-only code (`ctypes`, UI Automation, OCR) in a new module, `desktop_windows.py`, and have `desktop.py` choose it on `sys.platform == "win32"`, the way it already branches on `MAC`. Import it only on Windows. `steps.py` and `launcher.py` should keep calling `desktop.*` and stay as free of platform branches as possible.

Windows-only packages go in `requirements.txt` with an environment marker (`; sys_platform == "win32"`), so Linux and macOS don't install them.

## Function map

Line numbers refer to `launcher.ps1` at commit `e116055`; the file should not have changed since.

| PowerShell | Lines | Python target | Port notes |
|---|---|---|---|
| `GolfNative.Focus` | 71-78 | `desktop.focus` | Keep the Alt-tap workaround: Windows only lets the app with the last input take focus |
| `GolfNative.Click` | 79-88 | `desktop.click` | Keep the hover and all three delays. Unity ignores clicks on a button the pointer hasn't entered |
| `SetProcessDPIAware` | 109 | App startup | Qt 6 sets DPI awareness itself. Confirm window rectangles and clicks are in physical pixels on a scaled display, and don't set a conflicting mode |
| Single-instance mutex | 111-120 | `launcher.main` | `QLocalServer` should work; replace `os.getuid()` in the name with the session ID. Raising the first instance may need `desktop.focus` (see Traps) |
| Startup-delay registry | 189-203 | `launcher.py` | Same condition: only when the Startup shortcut exists. `install.py --uninstall` restores it, as in `install.ps1` |
| First-run config copy | 205-210 | `default_config` | Use `launcher.example.json` on Windows |
| `Get-SessionProcess` | 247-251 | `desktop.own_processes` | Filter by session ID, not user. See Traps |
| `Get-AppWindow` | 253-259 | `desktop.find_window` | The main window, skipping minimized ones. Use `EnumWindows` to pick the visible, unowned window of the process |
| `Find-UiaButton`, `Invoke-UiaButton` | 276-310 | New `desktop.find_button`, `desktop.press_button` | See clickButton below |
| `Stop-ProcessTree` | 312-319 | `desktop.kill_tree` | `taskkill /PID n /T /F` as in the PowerShell, or psutil if it proves equivalent |
| `Test-WindowClass` | 321-330 | `desktop.has_window_class` | Real window classes here, e.g. `UnityWndClass` |
| `Initialize-Ocr`, `Find-TextOnWindow` | 332-413 | `desktop.read_window` | See OCR below |
| `kill` step | 422-453 | `Runner.step_kill` | See Traps: graceful close and the admin prompt |
| `killCommandLine` step | 454-459 | `Runner.step_killCommandLine` | Already ported; needs `own_processes` working |
| `start` step | 460-486 | `Runner.step_start` | See Traps: arguments, `minimized`, `conhost`, elevation |
| `deleteFiles`, `obsSource` | 499-551 | `Runner` | Should work as is. Default OBS folder becomes `%APPDATA%\obs-studio\basic\scenes` (`OBS_CONFIG` in `steps.py`) |
| `clickButton` step | 566-619 | `Runner.step_clickButton` | Same flow as today; see below |
| `clickText` step | 620-656 | `Runner.step_clickText` | Already ported; needs `desktop` functions working |
| `install.ps1` | all | `install.py` | `.lnk` shortcuts in Startup and on the Desktop, and `--user <name>` for `-User`. See Packaging |

Already portable, but confirm by testing: `program_name` (it strips `.exe` and handles backslashes), `find_text`, `resolve_steps`, `sim_state`, `wait_until`, `write_json_atomic`, the menu UI and `--select`.

## clickButton: use real UI Automation

On Linux and macOS, `clickButton` falls back to OCR because Wine apps don't expose their controls. **Don't carry that fallback over to Windows.** It would lose the ability to tell a greyed-out button from an active one, so a disabled `PLAY` during an update would be "clicked" and the step would falsely succeed.

Keep the flow in `Runner.step_clickButton` and move the button lookup behind `desktop.find_button` and `desktop.press_button`. On Linux and macOS these wrap today's `_locate` and `_click`. On Windows, match lines 276-310:

1. Look in each of the process's top-level windows for an element with the exact name, preferring control type Button. Apps can have a link with the same name (Muni Golf's "Play" nav tab next to its "Play" button).
2. Fall back to any element with that name. GSPconnect's "Open Visual Data" is custom-drawn and isn't a button.
3. Return it only if `IsEnabled`.
4. Press with the Invoke pattern if supported. Otherwise bring the window forward and click the middle of its bounding rectangle.

Don't minimize the launcher or steal focus on Windows while polling: UI Automation doesn't need the app in front, and the PowerShell doesn't do either. `hide_launcher()` in `step_clickButton` therefore becomes non-Windows only.

Choose the UI Automation binding yourself and name the choice in your report. Prefer the thinnest one that works, such as `comtypes` over `UIAutomationCore`. Steps run on a background thread (`Launcher.boot`), so COM has to be initialized on that thread.

## OCR: use Windows OCR

Use the built-in Windows OCR through a Python WinRT binding, so users don't need to install Tesseract. Create the engine with `TryCreateFromUserProfileLanguages`, as in line 343, and show the same "needs an English language pack" error when it returns nothing.

`read_window` must keep returning lines of `Word` in screen coordinates, so `find_text` stays shared. `normalize_word` already matches the PowerShell's normalization.

Capture with `GetWindowRect` plus a screen copy, like lines 361-374. It reads the screen, so the window must be in front. Don't write to a fixed temp filename. If the WinRT binding turns out to be unworkable, stop and ask the user before switching to Tesseract, because that adds an install step.

## Traps

These work on Linux and would fail or misbehave on Windows, in most cases with no error.

- **`p.terminate()` is a force-kill on Windows.** `step_kill` uses it for `graceful: true`, so apps would die without saving. OBS writes its scene collection when it closes, so this can lose work. Post `WM_CLOSE` to the process's windows instead, matching `CloseMainWindow` on line 426, then wait for `timeout`.
- **psutil has no `uids` on Windows.** `own_processes` asks for it and calls `os.getuid()`. Use `ProcessIdToSessionId`, and keep the current exclusion of the launcher's own PID and zombies. Sessions matter here: with fast user switching, another account's copy of an app must not be closed.
- **Elevated apps need the admin prompt back.** Restore lines 441-452: if processes remain after the sweep, ask once through `taskkill` with the `runas` verb, hidden, then raise the same error message if they're still there.
- **`shlex.split` destroys Windows paths.** `split_args` runs in POSIX mode, which eats backslashes. `launcher.example.json` and `examples/` use one string for `args`, for example with `conhost`. On Windows, pass that string to `CreateProcess` unchanged and don't expand variables in it, as `Start-Process` does. Accept lists as well.
- **`Start-Process` isn't `subprocess.Popen`.** It goes through `ShellExecute`, so a program whose manifest requires admin rights still starts. `Popen` raises `WinError 740`. Handle that by falling back to `ShellExecuteExW`.
- **`start` options come back on Windows:**
  - `minimized`: `STARTUPINFO` with a minimized `wShowWindow`.
  - `conhost`: run through `%SystemRoot%\System32\conhost.exe`, line 474-479.
  - `start_new_session`: POSIX only. Use `creationflags` so the app outlives the launcher, and confirm that it does.
- **`child_env` would delete `PATH`.** It drops every variable whose value contains `sys._MEIPASS`. A packaged Windows build may prepend the bundle to `PATH`, and then the whole variable is removed. Strip only that entry. Also check whether the bootloader's DLL search directory leaks into child processes (`SetDllDirectoryW`). Test by launching a Qt app such as OBS from a packaged build.
- **External tools flash console windows.** A windowed app launching `taskkill` opens a console each time. Use `CREATE_NO_WINDOW` in `tool()`.
- **Bringing the launcher back can fail.** `restore()` uses `activateWindow`, which Windows may reduce to a taskbar flash. Use `desktop.focus` with the launcher's own `winId()` if so.
- **Fullscreen topmost:** line 881 keeps the menu topmost until a boot starts (line 1018). `launcher.py` doesn't do this yet. Add it for Windows if the taskbar shows over the menu.
- **The venv layout differs:** `.venv\Scripts\pythonw.exe`, not `.venv/bin/python`. `VENV_PYTHON` in `install.py` and the import error hint in `launcher.py` need adjusting. Shortcuts should use `pythonw.exe` so no console appears.
- **Config locations:** for a packaged build, `data_dir()` should use `%LOCALAPPDATA%\golf_simulator_launcher`.

## Tests

- Unix-only tests rely on `bash`, `touch`, `sleep` and `exec -a`. Port them to `sys.executable -c ...` where you can, so kill and start stay covered on Windows. A copy of `python.exe` under a test name gives a process with a custom name. Use `skipUnless` only for tests of truly platform-specific code.
- Add unit tests for what can go wrong without a real app: session filtering, passing `args` through verbatim, `PATH` handling in `child_env`, and the button preference (Button over other types, enabled only). Follow the mocking style of the `Clicks` class.
- **Make a test window** instead of using real simulators: a small script, kept outside the repo or in `tests/`, that shows a window with `UPDATE` and `PLAY` buttons and a disabled button, and logs each click to a file. Give it a unique process name by running a renamed copy of `python.exe`. Then point a test config at it and run a real boot, using `start`, `waitProcess`, `clickButton` with `clickFirst`, `clickText` with `confirmGone`, `focus` and `kill` with `graceful`. Read the click log to confirm the clicks landed. A config that names only the test process can't close anything of the user's.

## Packaging and CI

- `.github/workflows/ci.yml`: add `windows-latest` to the test matrix.
- `.github/workflows/build-exe.yml`:
  - Add `{ os: windows-latest, platform: windows-x64 }` to the build matrix.
  - Package as a `.zip`, and extend the release job's `files` and the artifact path to include it. `packaging/homebrew/formula.py` lists its archives by name, so Homebrew is unaffected.
  - Build with `--noconsole` on Windows. With no console `sys.stdout` is `None`, so `--version` and `--help` can crash inside argparse; handle that, and keep `packaging/smoke-test.sh` working (it uses `--version`). Its log path also needs a Windows case.
- `--add-data` works the same way, but check that the `launcher.example.json` used on Windows is bundled: the current pattern only matches `launcher.*.example.json`.

## Docs

Update `README.md`: merge the Windows instructions into one setup section, keep the Windows-only behaviour accurate in "What differs from Windows", and mention the new requirements. `launcher.ps1` stays documented until the user removes it. Add a changie fragment with `changie new` (kind `Added`).

## Done when

Report each item as passed, failed or not run. Don't mark one passed from reasoning alone.

Without real simulators:

- [ ] Unit tests pass on Windows, and nothing was skipped without a stated reason
- [ ] `launcher.py --windowed --dry-run` shows the same menu as `launcher.ps1 -Windowed -DryRun`, with logos, keys `1`-`9`, action letters, arrows, Enter and Esc
- [ ] Opening it twice brings back the first window, including from minimized
- [ ] Test window boot: `UPDATE` then `PLAY` pressed, a disabled button is not pressed, `clickText` lands, `confirmGone` retries
- [ ] Graceful `kill` lets the test window handle `WM_CLOSE` before anything is force-killed
- [ ] An app started by the launcher keeps running after the launcher exits
- [ ] A packaged build runs, and an app it starts doesn't inherit the bundle's `PATH`
- [ ] Clicks land correctly on a display scaled above 100%
- [ ] `install.py` and `install.py --uninstall` create and remove the shortcuts and restore the registry values

With the user's approval, on the simulator PC, using their real `launcher.json`:

- [ ] Each configured simulator boots, and each Driving Range action works
- [ ] An app started as administrator triggers the admin prompt
- [ ] `obsSource` retargets the capture, and OBS opens on the right scene with its data intact
- [ ] Results and app versions are written up in `examples/README.md`

## When you finish

Give the user a short report with these points:

- What passed, failed or wasn't run, using the list above
- Each new dependency and why you chose it
- Anything in this document that turned out to be wrong
- Any behaviour that still differs from `launcher.ps1`
- Whether you believe the PowerShell files can now be removed. Leave them in place; removing them is the user's decision
