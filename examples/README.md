# Recipes

Each file here is a complete, working config for one simulator (or add-on). Use them to try a setup on its own, or copy the parts you need into your `launcher.json`.

| Recipe | What it does | Status |
|---|---|---|
| [`gspro.json`](gspro.json) | Boots GSPro; opens GSPconnect's *Visual Data* for Square launch monitors; **Driving Range** shortcut clicks *Launch GSPro* → *Practice* → *GSPro Practice* | Tested |
| [`square-golf.json`](square-golf.json) | Boots Square Golf; presses *UPDATE* if offered, otherwise *PLAY*; *Sign in*; skips the device search; **Driving Range** shortcut clicks *Driving Ranges* → *Play* | Tested (app 2.0.5) |
| [`pureplay.json`](pureplay.json) | Starts PurePlay and OBS — a starting point; fill in the path and screens | **Not tested** |
| [`square-impact.json`](square-impact.json) | Adds [square_impact](https://github.com/moexx399/square_impact) high-speed impact replays (an OBS overlay for the Square Omni) to GSPro and Square Golf | Tested (square_impact 0.8.0) |

Every recipe also starts OBS and makes sure it opens in normal mode. `"_about"` and `"_note"` fields are explanations only; the launcher ignores them.

## Try a recipe

Close the launcher if it's running (only one copy runs at a time), then from the launcher folder:

```powershell
# Walk through the steps without starting or clicking anything
powershell -NoProfile -STA -ExecutionPolicy Bypass -File launcher.ps1 -Config examples\gspro.json -Windowed -DryRun

# Use it for real
powershell -NoProfile -STA -ExecutionPolicy Bypass -File launcher.ps1 -Config examples\gspro.json
```

Images and other relative paths are resolved from the launcher folder, so the logos work from here too.

## Combine recipes

Your `launcher.json` can hold any number of simulators. To add one from a recipe, copy:

1. its entry from `sims`,
2. any `stepSets` it `include`s that you don't already have (they're named the same across recipes, e.g. `obs`),
3. its process names into the `names` lists of `beforeEachSim` and `closeAll`, so switching simulators closes it.

To add **square_impact** to a simulator you already have, copy the `squareImpact` step set from [`square-impact.json`](square-impact.json), add `{ "type": "include", "set": "squareImpact" }` to the simulator's steps (and its actions' steps) after OBS starts, and add `"square_impact"` to both close lists.

## square_impact notes

- Download `square_impact.exe` from its [releases](https://github.com/moexx399/square_impact/releases) and set the path in both `squareImpact` steps (the recipe expects `%USERPROFILE%\square_impact\square_impact.exe`).
- In OBS add a **Browser Source** with URL `http://localhost:8765/`, sized 1280×384 or 640×192 (1280×192 with `--crop full`). Each shot pops up, plays twice and disappears.
- Overlay options go in the `obs` step's `args`: `--port`, `--crop ball|full|left|right`, `--trim-front` / `--trim-back` (0–1), `--no-denoise`. Run `square_impact.exe --help` for the full list.
- `"conhost": true` opens it in the classic console, so the taskbar shows square_impact's icon instead of Windows Terminal's.

## Contributing a recipe

New simulators and launch monitors are very welcome. Add a file here following the same layout, mark anything you couldn't test with `"optional": true`, explain non-obvious steps in a `"_note"`, use `%LOCALAPPDATA%` / `%ProgramFiles%` style paths, and list the simulator version you tested in the table above.
