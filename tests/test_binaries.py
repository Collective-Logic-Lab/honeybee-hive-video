from __future__ import annotations

import contextlib
import hashlib
import io
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from filelock import FileLock, Timeout

from hive_video import _binaries


class BinaryResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="hive binary fixtures ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.explicit = self.make_pair("explicit")
        self.system = self.make_pair("path")
        self.managed = self.make_pair("managed")
        self.provider = mock.Mock(return_value=(self.managed["ffmpeg"], self.managed["ffprobe"]))
        self.version_v8 = mock.sentinel.ffmpeg_v8
        self.cache_dir = self.root / "provider-cache" / "portable-ffmpeg"
        self.cache_dir.parent.mkdir()
        package = types.ModuleType("portable_ffmpeg")
        package.FFmpegVersions = types.SimpleNamespace(V8=self.version_v8)
        package.core = types.SimpleNamespace(CACHE_DIR=self.cache_dir)
        package.get_ffmpeg = self.provider
        self.enterContext(mock.patch.dict("sys.modules", {"portable_ffmpeg": package}))
        self.enterContext(mock.patch.dict(os.environ, {}, clear=True))
        self.which = self.enterContext(
            mock.patch("hive_video._binaries.shutil.which", return_value=None)
        )
        _binaries._managed_binaries.cache_clear()
        self.addCleanup(_binaries._managed_binaries.cache_clear)

    def make_pair(self, label: str) -> dict[str, str]:
        directory = self.root / label
        directory.mkdir()
        paths = {}
        for name in ("ffmpeg", "ffprobe"):
            path = directory / name
            path.write_text(f"#!/bin/sh\nprintf '{name} version fixture-1.0\\n'\n")
            path.chmod(0o755)
            paths[name] = str(path)
        return paths

    def set_explicit_pair(self) -> None:
        os.environ.update(
            {
                "HIVE_VIDEO_FFMPEG": self.explicit["ffmpeg"],
                "HIVE_VIDEO_FFPROBE": self.explicit["ffprobe"],
            }
        )

    def test_explicit_pair_takes_precedence_over_path_and_is_allowed_on_slurm(self) -> None:
        self.set_explicit_pair()
        os.environ["SLURM_JOB_ID"] = "123"
        self.which.side_effect = self.system.get
        for name, expected in self.explicit.items():
            self.assertEqual(_binaries.resolve_binary(name), expected)
        self.provider.assert_not_called()

    def test_complete_path_pair_is_used_without_managed_provider(self) -> None:
        self.which.side_effect = self.system.get
        for name, expected in self.system.items():
            self.assertEqual(_binaries.resolve_binary(name), expected)
        self.provider.assert_not_called()

    def test_partial_path_uses_both_managed_binaries_and_never_mixes_pairs(self) -> None:
        self.which.side_effect = lambda name: self.system["ffmpeg"] if name == "ffmpeg" else None
        for name, expected in self.managed.items():
            self.assertEqual(_binaries.resolve_binary(name), expected)
        self.provider.assert_called_once_with(version=self.version_v8)

    def test_managed_pair_is_cached_and_provider_messages_stay_off_stdout(self) -> None:
        def prepare(*, version):
            self.assertIs(version, self.version_v8)
            print("provider preparation message")
            return self.managed["ffmpeg"], self.managed["ffprobe"]

        self.provider.side_effect = prepare
        with (
            contextlib.redirect_stdout(io.StringIO()) as stdout,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            self.assertEqual(_binaries.resolve_binary("ffmpeg"), self.managed["ffmpeg"])
            self.provider.side_effect = RuntimeError("offline provider must not be contacted again")
            self.assertEqual(_binaries.resolve_binary("ffprobe"), self.managed["ffprobe"])
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("provider preparation message", stderr.getvalue())
        self.provider.assert_called_once_with(version=self.version_v8)

    def test_held_preparation_lock_prevents_provider_call(self) -> None:
        lock_path = self.cache_dir.parent / "hive-video.lock"
        with FileLock(str(lock_path), timeout=0):
            with mock.patch(
                "filelock.FileLock",
                side_effect=lambda path, timeout: FileLock(path, timeout=0),
            ) as make_lock:
                with self.assertRaises(Timeout):
                    _binaries.resolve_binary("ffmpeg")
            make_lock.assert_called_once_with(str(lock_path), timeout=60)
            self.provider.assert_not_called()

    def test_invalid_explicit_settings_refuse_fallback(self) -> None:
        self.which.side_effect = self.system.get
        cases = (
            ({"HIVE_VIDEO_FFMPEG": self.explicit["ffmpeg"]}, ValueError),
            (
                {
                    "HIVE_VIDEO_FFMPEG": "relative/ffmpeg",
                    "HIVE_VIDEO_FFPROBE": self.explicit["ffprobe"],
                },
                ValueError,
            ),
            (
                {
                    "HIVE_VIDEO_FFMPEG": str(self.root / "missing"),
                    "HIVE_VIDEO_FFPROBE": self.explicit["ffprobe"],
                },
                FileNotFoundError,
            ),
        )
        for environment, error in cases:
            with (
                self.subTest(environment=environment),
                mock.patch.dict(os.environ, environment, clear=True),
            ):
                with self.assertRaises(error):
                    _binaries.resolve_binary("ffmpeg")
        self.provider.assert_not_called()

    def test_nonexecutable_explicit_binary_is_rejected(self) -> None:
        self.set_explicit_pair()
        Path(self.explicit["ffprobe"]).chmod(0o644)
        with self.assertRaises(PermissionError):
            _binaries.resolve_binary("ffmpeg")
        self.provider.assert_not_called()

    def test_unprepared_slurm_job_cannot_trigger_managed_download(self) -> None:
        os.environ["SLURM_JOB_ID"] = "123"
        self.which.side_effect = lambda name: self.system["ffmpeg"] if name == "ffmpeg" else None
        with self.assertRaises(RuntimeError):
            _binaries.resolve_binary("ffmpeg")
        self.provider.assert_not_called()

    def test_unknown_binary_name_is_rejected_before_resolution(self) -> None:
        with self.assertRaises(ValueError):
            _binaries.resolve_binary("ffplay")
        self.which.assert_not_called()
        self.provider.assert_not_called()

    def test_unsupported_managed_architecture_does_not_fetch_a_substitute(self) -> None:
        with mock.patch("hive_video._binaries.platform.machine", return_value="riscv64"):
            with self.assertRaisesRegex(RuntimeError, "unsupported"):
                _binaries.resolve_binary("ffmpeg")
        self.provider.assert_not_called()

    def test_setup_reports_the_selected_versions_and_binary_checksums(self) -> None:
        self.set_explicit_pair()
        report = _binaries.setup_ffmpeg()
        self.assertEqual(set(report), {"ffmpeg", "ffprobe"})
        for name, path in self.explicit.items():
            self.assertEqual(report[name]["path"], path)
            self.assertEqual(report[name]["version"], f"{name} version fixture-1.0")
            self.assertEqual(
                report[name]["sha256"], hashlib.sha256(Path(path).read_bytes()).hexdigest()
            )
        self.provider.assert_not_called()

    def test_setup_rejects_an_executable_that_is_not_the_requested_tool(self) -> None:
        self.set_explicit_pair()
        Path(self.explicit["ffprobe"]).write_text("#!/bin/sh\nprintf 'different tool\\n'\n")
        with self.assertRaisesRegex(RuntimeError, "Unexpected ffprobe version output"):
            _binaries.setup_ffmpeg()

    def test_setup_nonzero_exit_reports_tool_status_and_diagnostics(self) -> None:
        self.set_explicit_pair()
        executable = Path(self.explicit["ffprobe"])
        executable.write_text("#!/bin/sh\nprintf 'missing runtime dependency\\n' >&2\nexit 7\n")
        with self.assertRaises(RuntimeError) as stopped:
            _binaries.setup_ffmpeg()
        self.assertIn("ffprobe version check failed (exit 7)", str(stopped.exception))
        self.assertIn(str(executable), str(stopped.exception))
        self.assertIn("missing runtime dependency", str(stopped.exception))


if __name__ == "__main__":
    unittest.main()
