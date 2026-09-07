from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from hive_video import cli


class PackageInterfaceTests(unittest.TestCase):
    def test_setup_ffmpeg_prints_binary_identity_json(self) -> None:
        binaries = {
            name: {"path": f"/cache/{name}", "version": "8.0", "sha256": "a" * 64}
            for name in ("ffmpeg", "ffprobe")
        }
        with (
            mock.patch("hive_video._binaries.setup_ffmpeg", return_value=binaries) as setup,
            contextlib.redirect_stdout(io.StringIO()) as stdout,
        ):
            self.assertEqual(cli.main(["setup-ffmpeg"]), 0)
        self.assertEqual(json.loads(stdout.getvalue()), binaries)
        setup.assert_called_once_with()

    def test_setup_ffmpeg_help_never_prepares_binaries(self) -> None:
        with (
            mock.patch("hive_video._binaries.setup_ffmpeg") as setup,
            contextlib.redirect_stdout(io.StringIO()) as stdout,
            self.assertRaises(SystemExit) as raised,
        ):
            cli.main(["setup-ffmpeg", "--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("Help does not download", stdout.getvalue())
        setup.assert_not_called()

    def test_setup_ffmpeg_failure_is_nonzero_without_json_success(self) -> None:
        with (
            mock.patch(
                "hive_video._binaries.setup_ffmpeg",
                side_effect=RuntimeError("Binary verification failed"),
            ),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            self.assertEqual(cli.main(["setup-ffmpeg"]), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Binary verification failed", stderr.getvalue())

    def test_download_dispatch_preserves_arguments_and_exit_status(self) -> None:
        arguments = ["--locator", "start4_side0_top", "--resolve-only", "--format", "sh"]
        original_argv = sys.argv[:]
        with mock.patch("hive_video.download.main", return_value=7) as run:
            self.assertEqual(cli.main(["download", *arguments]), 7)
        run.assert_called_once_with(arguments)
        self.assertEqual(sys.argv, original_argv)

    def test_resequence_dispatch_preserves_stage_and_help_arguments(self) -> None:
        arguments = ["render", "--help"]
        with mock.patch("hive_video.resequence.cli.main", return_value=0) as run:
            self.assertEqual(cli.main(["resequence", *arguments]), 0)
        run.assert_called_once_with(arguments)

    def test_stage_failures_do_not_become_successful_commands(self) -> None:
        for failure, status in ((ValueError("stale approval"), 1), (KeyboardInterrupt(), 130)):
            with (
                self.subTest(failure=type(failure).__name__),
                mock.patch("hive_video.resequence.cli.main", side_effect=failure),
                contextlib.redirect_stderr(io.StringIO()),
                contextlib.redirect_stdout(io.StringIO()) as stdout,
            ):
                self.assertEqual(cli.main(["resequence", "render"]), status)
                self.assertEqual(stdout.getvalue(), "")

    def test_package_help_does_not_load_analysis_or_resequence_dependencies(self) -> None:
        code = (
            "import sys; from hive_video.cli import build_parser; "
            "build_parser().print_help(); "
            "heavy = {'numpy', 'cv2', 'PIL', 'sklearn', 'matplotlib'} & sys.modules.keys(); "
            "sys.exit('Unexpected imports: ' + str(heavy) if heavy else 0)"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("fragment", "download", "resequence", "setup-ffmpeg"):
            self.assertIn(command, result.stdout)

    def test_download_module_runs_outside_checkout(self) -> None:
        repository = Path(__file__).parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "hive_video.download", "--help"],
            cwd=repository.parent,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: hive-video download", result.stdout)

    def test_resequence_module_entrypoints_run_outside_checkout(self) -> None:
        repository = Path(__file__).parents[1]
        modules = (
            ("detect_video_discontinuities", "detect"),
            ("summarize_jump_events", "summarize"),
            ("prepare_cut_review", "prepare-cuts"),
            ("build_segments_from_jumps", "build-segments"),
            ("order_video_segments", "order"),
            ("diagnostics.auto_qc_segment_joins", "qc"),
            ("diagnostics.make_join_review_video", "review"),
            ("diagnostics.approve_manual_join_qc", "approve"),
            ("diagnostics.diagnose_segment_discontinuities", "diagnose"),
            ("reassemble_video_from_segments", "render"),
            ("compress_resequenced", "compress"),
        )
        for module, stage in modules:
            with self.subTest(module=module):
                result = subprocess.run(
                    [sys.executable, "-m", "hive_video.resequence." + module, "--help"],
                    cwd=repository.parent,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage: hive-video resequence " + stage, result.stdout)


if __name__ == "__main__":
    unittest.main()
