"""Run with: .venv/bin/python -m unittest discover tests"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import desktop
import install
import steps


def word_rows(*rows):
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    page = "1\t1\t0\t0\t0\t0\t0\t0\t2000\t1000\t-1\t"
    words = [f"5\t1\t1\t1\t{line}\t{n}\t{x}\t{y}\t100\t40\t90\t{text}" for line, n, x, y, text in rows]
    return "\n".join([header, page, *words])


class FindText(unittest.TestCase):
    window = desktop.Window("1", 100, 50, 1000, 500)  # the capture is twice the window size

    def lines(self, *rows):
        return desktop.parse_tsv(word_rows(*rows), self.window)

    def test_maps_capture_pixels_to_screen(self):
        lines = self.lines((1, 1, 200, 100, "Play"))
        self.assertEqual(desktop.find_text(lines, "Play"), (225, 110))

    def test_case_and_punctuation_are_ignored(self):
        self.assertIsNotNone(desktop.find_text(self.lines((1, 1, 0, 0, "PLAY!")), "Play!"))

    def test_centres_on_all_words(self):
        lines = self.lines((1, 1, 0, 0, "GSPro"), (1, 2, 200, 0, "Practice"))
        self.assertEqual(desktop.find_text(lines, "GSPro Practice"), (100 + 75, 50 + 10))

    def test_exact_line_beats_sentence(self):
        lines = self.lines((1, 1, 0, 0, "Press"), (1, 2, 200, 0, "Play"), (2, 1, 400, 400, "Play"))
        self.assertEqual(desktop.find_text(lines, "Play"), (100 + 225, 50 + 210))

    def test_partial_words_do_not_match(self):
        self.assertIsNone(desktop.find_text(self.lines((1, 1, 0, 0, "Replay")), "Play"))


class WindowGeometry(unittest.TestCase):
    def test_uses_absolute_position_from_xwininfo(self):
        out = """
  Absolute upper-left X:  350
  Absolute upper-left Y:  -7
  Relative upper-left X:  1
  Relative upper-left Y:  20
  Width: 900
  Height: 600
"""
        with mock.patch.object(desktop, "tool", return_value=mock.Mock(stdout=out)):
            self.assertEqual(desktop._x_window("9"), desktop.Window("9", 350, -7, 900, 600))
        with mock.patch.object(desktop, "tool", return_value=mock.Mock(stdout="")):
            self.assertIsNone(desktop._x_window("9"))


class Processes(unittest.TestCase):
    def test_program_name(self):
        for text in ("C:\\GSProV1\\GSPro.exe", "/opt/GSPro", "gspro", "GSPRO.EXE"):
            self.assertEqual(desktop.program_name(text), "gspro")

    def spawn(self, script, argv0):
        proc = subprocess.Popen(["bash", "-c", script, argv0])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        return proc

    def wait_for(self, name):
        self.assertTrue(steps.wait_until(lambda: desktop.find_processes(name), 5, 0.05), name)

    def test_finds_wine_style_names_over_15_characters(self):
        self.spawn('exec -a "$0" sleep 30', "C:\\Program Files\\Square Golf Launcher.exe")
        self.wait_for("Square Golf Launcher")

    def test_kill_removes_process_tree_and_ignores_zombies(self):
        # Parent and child both ignore SIGTERM, so only the force-kill can end them.
        proc = self.spawn('trap "" TERM; sleep 30 & exec -a "$0" sleep 30', "TestSimGame.exe")
        self.wait_for("TestSimGame")
        family = [p.pid for p in desktop.find_processes("TestSimGame")[0].children(recursive=True)]
        self.assertTrue(family)
        runner = steps.Runner(ROOT)
        self.assertEqual(runner.run({"type": "kill", "names": ["TestSimGame"], "graceful": True, "timeout": 1}), "done")
        # proc is this test's own unreaped child: a zombie, which must count as closed.
        self.assertEqual(desktop.find_processes("TestSimGame"), [])
        for pid in family:
            self.assertFalse(desktop.psutil.pid_exists(pid) and desktop.psutil.Process(pid).status() != "zombie")
        proc.wait()
        self.assertEqual(runner.run({"type": "kill", "names": ["TestSimGame"]}), "skipped")


class Config(unittest.TestCase):
    def test_includes_nest_and_unknown_sets_fail(self):
        config = {"stepSets": {"a": [{"type": "include", "set": "b"}, {"type": "delay"}], "b": [{"type": "kill"}]}}
        flat = steps.resolve_steps(config, [{"type": "include", "set": "a"}])
        self.assertEqual([s["type"] for s in flat], ["kill", "delay"])
        with self.assertRaises(steps.StepError):
            steps.resolve_steps(config, [{"type": "include", "set": "nope"}])

    def test_example_configs_resolve(self):
        for path in ROOT.glob("launcher.*.example.json"):
            config = steps.load_config(path)
            for sim in config["sims"]:
                for owner in [sim, *sim.get("actions", [])]:
                    resolved = steps.resolve_steps(config, owner["steps"])
                    for step in resolved:
                        self.assertTrue(hasattr(steps.Runner, f"step_{step['type']}"), step)
                        self.assertNotIn("\\", step.get("path", ""), step)

    def test_args_keep_spaces_and_expand_variables(self):
        with mock.patch.dict(os.environ, {"HOME": "/home/g"}):
            self.assertEqual(steps.split_args('"$HOME/Square Golf.exe" --x'), ["/home/g/Square Golf.exe", "--x"])
        self.assertEqual(steps.split_args(["a b"]), ["a b"])
        self.assertEqual(steps.split_args(None), [])

    def test_sim_state(self):
        self.assertEqual(steps.sim_state({"enabled": False}, ROOT), (False, "Coming soon"))
        self.assertFalse(steps.sim_state({"check": "/no/such/file"}, ROOT)[0])
        self.assertTrue(steps.sim_state({"check": "gspro.png"}, ROOT)[0])


class StartAndFiles(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)
        self.runner = steps.Runner(self.dir)

    def test_start_waits_and_times_out(self):
        marker = self.dir / "ran"
        self.assertEqual(self.runner.run({"type": "start", "path": "touch", "args": [str(marker)], "wait": True}), "done")
        self.assertTrue(marker.exists())
        with self.assertRaisesRegex(steps.StepError, "Still running"):
            self.runner.run({"type": "start", "path": "sleep", "args": "5", "wait": True, "timeout": 0.1})

    def test_start_reports_missing_file(self):
        with self.assertRaisesRegex(steps.StepError, "File not found"):
            self.runner.run({"type": "start", "path": "/no/such/app"})

    def test_delete_files(self):
        (self.dir / "run_1").touch()
        (self.dir / "keep").touch()
        step = {"type": "deleteFiles", "path": "run_*"}
        self.assertEqual(self.runner.run(step), "done")
        self.assertEqual(self.runner.run(step), "skipped")
        self.assertTrue((self.dir / "keep").exists())

    def test_dry_run_touches_nothing(self):
        (self.dir / "run_1").touch()
        steps.Runner(self.dir, dry_run=True).run({"type": "deleteFiles", "path": "run_*"})
        self.assertTrue((self.dir / "run_1").exists())


class ObsSource(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = Path(folder.name) / "scenes.json"
        self.original = {
            "current_scene": "Main", "current_program_scene": "Main", "name": "Mine", "extra": {"é": 1},
            "sources": [
                {"id": "scene", "name": "Main"}, {"id": "scene", "name": "Range"},
                {"id": "game_capture", "name": "Game", "settings": {"window": "old", "keep": 1}},
            ],
        }
        self.file.write_text(json.dumps(self.original), encoding="utf-8")
        (Path(folder.name) / "broken.json").write_text("{", encoding="utf-8")
        self.step = {"type": "obsSource", "folder": folder.name, "collection": "Mine"}
        patcher = mock.patch.object(desktop, "find_processes", return_value=[])
        self.find_processes = patcher.start()
        self.addCleanup(patcher.stop)
        self.runner = steps.Runner(folder.name)

    def test_updates_source_and_scene_once_and_backs_up(self):
        step = {**self.step, "sources": {"Game": {"window": "new"}}, "scene": "Range"}
        self.assertEqual(self.runner.run(step), "done")
        doc = json.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual(doc["sources"][2]["settings"], {"window": "new", "keep": 1})
        self.assertEqual(doc["current_program_scene"], "Range")
        self.assertEqual(list(doc), list(self.original))
        backup = Path(f"{self.file}.before-launcher")
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), self.original)
        self.assertEqual(self.runner.run(step), "skipped")

    def test_unknown_names_leave_the_file_alone(self):
        before = self.file.read_bytes()
        for bad in ({"sources": {"Nope": {"a": 1}}}, {"scene": "Nope"}, {"collection": "Nope"}):
            with self.assertRaises(steps.StepError):
                self.runner.run({**self.step, **bad})
        self.assertEqual(self.file.read_bytes(), before)

    def test_refuses_while_obs_runs(self):
        self.find_processes.return_value = [object()]
        with self.assertRaisesRegex(steps.StepError, "OBS is running"):
            self.runner.run(self.step)


class Clicks(unittest.TestCase):
    """The click flow, with the screen replaced by a script of what's visible."""

    window = desktop.Window("1", 0, 0, 800, 600)

    def setUp(self):
        self.screen = []
        self.clicks = []
        patches = [
            mock.patch.object(desktop, "find_window", return_value=self.window),
            mock.patch.object(desktop, "focus", return_value=True),
            mock.patch.object(desktop, "read_window", side_effect=lambda w: list(self.screen)),
            mock.patch.object(desktop, "find_text", side_effect=self.find_text),
            mock.patch.object(desktop, "click", side_effect=self.click),
            mock.patch.object(desktop, "move_mouse"),
            mock.patch.object(steps.time, "sleep"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.runner = steps.Runner(ROOT)
        self.on_click = {}

    def find_text(self, lines, text):
        return (len(text), 1) if text in lines else None

    def click(self, x, y):
        name = next(t for t in self.screen if len(t) == x)
        self.clicks.append(name)
        self.screen[:] = self.on_click.get(name, self.screen)

    def test_click_first_presses_update_then_play(self):
        self.screen[:] = ["UPDATE", "PLAY"]
        self.on_click["UPDATE"] = ["PLAY"]
        step = {"type": "clickButton", "process": "x", "clickFirst": "UPDATE", "text": "PLAY"}
        self.assertEqual(self.runner.run(step), "done")
        self.assertEqual(self.clicks, ["UPDATE", "PLAY"])

    def test_click_first_is_optional(self):
        self.screen[:] = ["PLAY"]
        step = {"type": "clickButton", "process": "x", "clickFirst": "UPDATE", "text": "PLAY"}
        self.assertEqual(self.runner.run(step), "done")
        self.assertEqual(self.clicks, ["PLAY"])

    def test_missing_button_times_out(self):
        with self.assertRaisesRegex(steps.StepError, "Couldn't find the 'PLAY' button"):
            self.runner.run({"type": "clickButton", "process": "x", "text": "PLAY", "timeout": 0.01})

    def test_done_if_port_skips(self):
        with mock.patch.object(steps, "port_open", return_value=True):
            step = {"type": "clickButton", "process": "x", "text": "PLAY", "doneIfPort": 1}
            self.assertEqual(self.runner.run(step), "skipped")
        self.assertEqual(self.clicks, [])

    def test_if_command_line_skips_when_it_differs(self):
        proc = mock.Mock(info={"cmdline": ["GSPro.exe"]})
        with mock.patch.object(desktop, "find_processes", return_value=[proc]):
            step = {"type": "clickButton", "process": "GSPro", "text": "Play!", "ifCommandLine": "*-show-screen*"}
            self.assertEqual(self.runner.run(step), "skipped")

    def test_click_text_confirm_gone_retries_until_it_goes(self):
        self.screen[:] = ["Sign in"]
        self.on_click["Sign in"] = ["Sign in"]

        def second_click_works(x, y):
            self.clicks.append("Sign in")
            if len(self.clicks) == 2:
                self.screen.clear()

        desktop.click.side_effect = second_click_works
        step = {"type": "clickText", "process": "x", "text": "Sign in", "confirmGone": True, "parkMouse": True}
        self.assertEqual(self.runner.run(step), "done")
        self.assertEqual(len(self.clicks), 2)
        desktop.move_mouse.assert_called_once_with(798, 598)


BUNDLED_ENV = {
    "LD_LIBRARY_PATH": "/bundle", "LD_LIBRARY_PATH_ORIG": "/usr/mine", "DYLD_LIBRARY_PATH": "/bundle",
    "QML2_IMPORT_PATH": "/bundle/qml", "X": "1",
}


class ChildEnvironment(unittest.TestCase):
    def test_packaged_build_restores_library_paths(self):
        with mock.patch.dict(os.environ, BUNDLED_ENV), mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", "/bundle", create=True):
            env = desktop.child_env()
        self.assertEqual(env["LD_LIBRARY_PATH"], "/usr/mine")
        self.assertNotIn("LD_LIBRARY_PATH_ORIG", env)
        self.assertNotIn("DYLD_LIBRARY_PATH", env)
        self.assertNotIn("QML2_IMPORT_PATH", env)
        self.assertEqual(env["X"], "1")

    def test_running_from_source_changes_nothing(self):
        with mock.patch.dict(os.environ, BUNDLED_ENV):
            self.assertEqual(desktop.child_env()["LD_LIBRARY_PATH"], "/bundle")


class Formula(unittest.TestCase):
    def test_every_platform_gets_its_own_archive_and_checksum(self):
        spec = importlib.util.spec_from_file_location("formula", ROOT / "packaging/homebrew/formula.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        text = module.formula("v1.2.3", "v1.2.3", {key: key * 3 for key in module.ARCHIVES})
        self.assertIn('version "1.2.3"', text)
        for key, archive in module.ARCHIVES.items():
            self.assertIn(f'releases/download/v1.2.3/{archive}"\n      sha256 "{key * 3}"', text)
        with self.assertRaises(ValueError):
            module.formula("1", "v1", {})


class Install(unittest.TestCase):
    home = Path("/home/golf user")

    def test_linux_quotes_paths_for_desktop_entries(self):
        files = install.shortcut_files(
            "linux", self.home, self.home / "Desktop", Path("/a b/$x/python"), Path("/a b/launcher.py"))
        self.assertEqual(len(files), 3)
        entry = files[self.home / ".config/autostart/golf-simulator.desktop"][0].decode()
        self.assertIn('Exec="/a b/\\$x/python" "/a b/launcher.py"', entry)
        self.assertTrue(files[self.home / "Desktop/golf-simulator.desktop"][1])

    def test_macos_writes_a_login_item(self):
        files = install.shortcut_files(
            "darwin", self.home, self.home / "Desktop", Path("/a/python"), Path("/a/launcher.py"))
        plist = install.plistlib.loads(files[self.home / "Library/LaunchAgents/com.golfsimlauncher.plist"][0])
        self.assertEqual(plist["ProgramArguments"], ["/a/python", "/a/launcher.py"])
        self.assertTrue(plist["RunAtLoad"])
        self.assertTrue(files[self.home / "Desktop/Golf Simulator.command"][1])


if __name__ == "__main__":
    unittest.main()
