"""Where FluentFlow puts gigabytes of source media.

One transcription stores its whole source file, so the data root grows in
gigabytes. %APPDATA% is on the system drive — the one drive a user cannot
afford to fill — so Windows prefers a fixed non-system drive. The rule that
matters most here is the migration guard: an install whose jobs database
already lives under %APPDATA% must keep reading it, because pointing at a new
empty directory would present an empty workspace and read as total data loss.
"""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import backend.core.runtime_paths as runtime_paths
from backend.core.runtime_paths import APP_NAME, app_data_root


class _FakeWindows:
    """A Windows machine with a chosen set of drives and their free space."""

    def __init__(self, drives: dict[str, int], appdata_populated: bool, removable: set[str] | None = None):
        self.drives = drives
        self.appdata_populated = appdata_populated
        self.removable = removable or set()
        self.appdata = Path("C:\\Users\\tester\\AppData\\Roaming") / APP_NAME

    def is_fixed(self, drive: str) -> bool:
        return drive not in self.removable

    def is_dir(self, path: Path) -> bool:
        text = str(path)
        if text.rstrip("\\") in {drive.rstrip("\\") for drive in self.drives}:
            return True
        return text == str(self.appdata) and self.appdata_populated

    def exists(self, path: Path) -> bool:
        return self.is_dir(path)

    def disk_usage(self, path: str):
        free = self.drives[str(path).rstrip("\\") + "\\"]
        return type("Usage", (), {"total": free, "used": 0, "free": free})()


def _resolve(machine: _FakeWindows) -> Path:
    with patch.dict(os.environ, {"SystemDrive": "C:", "APPDATA": "C:\\Users\\tester\\AppData\\Roaming"}, clear=False):
        os.environ.pop("FLUENTFLOW_DATA_DIR", None)
        with patch.object(runtime_paths.platform, "system", return_value="Windows"), \
             patch.object(runtime_paths, "read_data_root_pointer", return_value=None), \
             patch.object(Path, "is_dir", lambda self: machine.is_dir(self)), \
             patch.object(Path, "exists", lambda self: machine.exists(self)), \
             patch.object(Path, "iterdir", lambda self: iter([Path("fluentflow_jobs.sqlite")])), \
             patch.object(runtime_paths.shutil, "disk_usage", machine.disk_usage), \
             patch.object(runtime_paths, "_is_fixed_drive", machine.is_fixed):
            return app_data_root()


BIG = 200 * 1024 * 1024 * 1024
SMALL = 1 * 1024 * 1024 * 1024


class WindowsDataRootTests(TestCase):
    def test_a_fresh_install_lands_on_the_data_drive_not_the_system_drive(self):
        root = _resolve(_FakeWindows({"C:\\": BIG, "D:\\": BIG}, appdata_populated=False))
        self.assertEqual(root, Path("D:\\") / APP_NAME)

    def test_an_existing_appdata_workspace_keeps_being_read(self):
        # Silently switching would show an empty task list — indistinguishable
        # from having lost every record. scripts/migrate_data_dir.py moves it.
        machine = _FakeWindows({"C:\\": BIG, "D:\\": BIG}, appdata_populated=True)
        self.assertEqual(_resolve(machine), machine.appdata)

    def test_a_machine_with_only_a_system_drive_still_works(self):
        machine = _FakeWindows({"C:\\": BIG}, appdata_populated=False)
        self.assertEqual(_resolve(machine), machine.appdata)

    def test_a_nearly_full_second_drive_is_not_chosen(self):
        machine = _FakeWindows({"C:\\": BIG, "D:\\": SMALL}, appdata_populated=False)
        self.assertEqual(_resolve(machine), machine.appdata)

    def test_the_roomiest_drive_wins_and_the_choice_is_stable(self):
        machine = _FakeWindows({"C:\\": BIG, "D:\\": 10 * SMALL, "E:\\": BIG}, appdata_populated=False)
        self.assertEqual(_resolve(machine), Path("E:\\") / APP_NAME)

    def test_a_roomy_usb_disk_never_becomes_the_workspace(self):
        # It would win on free space, then take the workspace with it when
        # unplugged.
        machine = _FakeWindows({"C:\\": BIG, "D:\\": 10 * SMALL, "F:\\": BIG}, appdata_populated=False, removable={"F:"})
        self.assertEqual(_resolve(machine), Path("D:\\") / APP_NAME)

    def test_an_unreadable_volume_is_skipped_instead_of_crashing_startup(self):
        machine = _FakeWindows({"C:\\": BIG, "D:\\": BIG, "E:\\": BIG}, appdata_populated=False)
        original = machine.disk_usage

        def flaky(path):
            if str(path).startswith("E:"):
                raise OSError("device not ready")
            return original(path)

        machine.disk_usage = flaky
        self.assertEqual(_resolve(machine), Path("D:\\") / APP_NAME)


class ExplicitOverrideTests(TestCase):
    def test_the_env_override_still_beats_every_default(self):
        with patch.dict(os.environ, {"FLUENTFLOW_DATA_DIR": "E:\\custom\\place"}):
            self.assertEqual(app_data_root(), Path("E:\\custom\\place"))


# A migration used to leave no trace of where it put the data, so startup kept
# re-running the free-space heuristic. Moving the workspace to any drive other
# than the roomiest one was therefore silently abandoned on the next launch: the
# jobs database sat on D: while resolution answered G:, and the app would have
# come up with an empty workspace that reads as total data loss.
class DataRootPointerTests(TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.pointer = Path(self._tmp.name) / "data-root.txt"
        patcher = patch.object(runtime_paths, "data_root_pointer_path", return_value=self.pointer)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("FLUENTFLOW_DATA_DIR", None)

    def test_a_recorded_location_is_used_instead_of_re_running_the_heuristic(self):
        runtime_paths.write_data_root_pointer(Path("G:\\FluentFlow"))
        self.assertEqual(app_data_root(), Path("G:\\FluentFlow"))

    def test_the_recorded_location_survives_a_roomier_drive_appearing_later(self):
        runtime_paths.write_data_root_pointer(Path("D:\\FluentFlow"))
        machine = _FakeWindows({"C:\\": BIG, "D:\\": SMALL, "G:\\": BIG * 4}, appdata_populated=False)
        with patch.object(runtime_paths.platform, "system", return_value="Windows"), \
             patch.object(runtime_paths.shutil, "disk_usage", machine.disk_usage), \
             patch.object(runtime_paths, "_is_fixed_drive", machine.is_fixed):
            self.assertEqual(app_data_root(), Path("D:\\FluentFlow"))

    def test_an_unreachable_recorded_location_is_still_reported(self):
        # Better a clear "cannot open database" than a fresh empty workspace.
        runtime_paths.write_data_root_pointer(Path("Z:\\unplugged\\FluentFlow"))
        self.assertEqual(app_data_root(), Path("Z:\\unplugged\\FluentFlow"))

    def test_no_pointer_and_an_empty_pointer_both_fall_through_to_the_default(self):
        self.assertIsNone(runtime_paths.read_data_root_pointer())
        self.pointer.write_text("   \n", encoding="utf-8")
        self.assertIsNone(runtime_paths.read_data_root_pointer())

    def test_the_env_override_still_wins_over_a_recorded_location(self):
        runtime_paths.write_data_root_pointer(Path("G:\\FluentFlow"))
        with patch.dict(os.environ, {"FLUENTFLOW_DATA_DIR": "E:\\custom"}):
            self.assertEqual(app_data_root(), Path("E:\\custom"))


# One decision, made once, and able to say how it was made. The location used to
# be re-derived on every startup from whichever mechanism answered first, with
# nothing reporting which had won — so a workspace resolved somewhere unexpected
# presented as an empty task list, indistinguishable from total data loss.
class WorkspaceResolutionTests(TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.pointer = Path(self._tmp.name) / "data-root.txt"
        self._patch(patch.object(runtime_paths, "data_root_pointer_path", return_value=self.pointer))
        self._patch(patch.dict(os.environ, {}, clear=False))
        for name in ("FLUENTFLOW_DATA_DIR", *runtime_paths.PATH_OVERRIDE_ENV_NAMES):
            os.environ.pop(name, None)

    def _patch(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_explicit_override_reports_itself_as_the_decision(self):
        with patch.dict(os.environ, {"FLUENTFLOW_DATA_DIR": "E:\\custom"}):
            workspace = runtime_paths.resolve_workspace()
        self.assertEqual(workspace.decided_by, runtime_paths.DECIDED_BY_ENV)
        self.assertFalse(workspace.is_guess)

    def test_a_recorded_location_reports_itself_and_is_not_a_guess(self):
        runtime_paths.write_data_root_pointer(Path("G:\\FluentFlow"))
        workspace = runtime_paths.resolve_workspace()
        self.assertEqual(workspace.root, Path("G:\\FluentFlow"))
        self.assertEqual(workspace.decided_by, runtime_paths.DECIDED_BY_RECORDED)
        self.assertFalse(workspace.is_guess)

    def test_a_first_run_guess_is_recorded_so_it_is_never_guessed_twice(self):
        before = runtime_paths.resolve_workspace()
        self.assertTrue(before.is_guess)

        recorded = runtime_paths.ensure_workspace_recorded()
        self.assertEqual(recorded.root, before.root)
        self.assertEqual(recorded.decided_by, runtime_paths.DECIDED_BY_RECORDED)
        # The next startup reads the file instead of re-deciding.
        self.assertEqual(runtime_paths.read_data_root_pointer(), before.root)
        self.assertFalse(runtime_paths.resolve_workspace().is_guess)

    def test_recording_does_not_pin_a_temporary_override(self):
        # An operator steering one run must not silently become permanent.
        with patch.dict(os.environ, {"FLUENTFLOW_DATA_DIR": "E:\\just-this-once"}):
            runtime_paths.ensure_workspace_recorded()
        self.assertIsNone(runtime_paths.read_data_root_pointer())

    def test_a_failure_to_record_is_not_fatal(self):
        with patch.object(runtime_paths, "write_data_root_pointer", side_effect=OSError("read-only")):
            workspace = runtime_paths.ensure_workspace_recorded()
        self.assertTrue(workspace.is_guess)
        self.assertTrue(workspace.root)

    def test_per_path_overrides_are_reported_rather_than_left_silent(self):
        # A jobs database on one disk and a config file on another looked, from
        # the UI, exactly like every record had been lost.
        with patch.dict(os.environ, {
            "FLUENTFLOW_JOB_DB_PATH": "D:\\elsewhere\\jobs.sqlite",
            "FLUENTFLOW_CONFIG_PATH": "E:\\elsewhere\\config.json",
        }):
            workspace = runtime_paths.resolve_workspace()
        self.assertEqual([name for name, _ in workspace.overrides],
                         ["FLUENTFLOW_CONFIG_PATH", "FLUENTFLOW_JOB_DB_PATH"])
        described = workspace.describe()
        self.assertIn("FLUENTFLOW_JOB_DB_PATH", described)
        self.assertIn("2", described)

    def test_a_workspace_with_nothing_scattered_says_nothing_about_overrides(self):
        runtime_paths.write_data_root_pointer(Path("G:\\FluentFlow"))
        described = runtime_paths.resolve_workspace().describe()
        self.assertIn("G:\\FluentFlow", described)
        self.assertNotIn("FLUENTFLOW_", described)
