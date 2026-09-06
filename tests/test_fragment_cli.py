from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hive_video import cli
from hive_video.sources import fragment_filename, parse_locator, resolve_source, source_identity

KEY = "start04_20190609_175013_side0_top"
RAW_NAME = "start04__20190609_175013_side0_top.mp4"


class SourceTests(unittest.TestCase):
    def test_locator_normalization_and_invalid_identity(self) -> None:
        self.assertEqual(parse_locator("start4_side0_top"), "start04_side0_top")
        self.assertEqual(parse_locator("start004_side1_bottom"), "start04_side1_bottom")
        for locator in (
            "day4_side0_top",
            "start4_side2_top",
            "start4_side0_left",
            "../start4_side0_top",
            "start4_side0_top.mp4",
        ):
            with self.subTest(locator=locator), self.assertRaises(ValueError):
                parse_locator(locator)

    def test_identity_supports_existing_source_names(self) -> None:
        for source in (
            RAW_NAME,
            f"reseq_1_{RAW_NAME}",
            f"reseq_{KEY}.mp4",
            f"reseq_{KEY}.low.mp4",
            f"reseq_{KEY}/output/resequenced.mp4",
            f"reseq_{KEY}/compressed/high/resequenced.high.mp4",
        ):
            with self.subTest(source=source):
                self.assertEqual(source_identity(source), "start04_side0_top")

    def test_generic_sources_do_not_fabricate_identity(self) -> None:
        for source in (
            "start04_sample_5s.mp4",
            f"reseq_{KEY}/review/qc_roll_flagged_joins.mp4",
            "unrelated/resequenced.mp4",
            "fragment_start04_side0_top_1s_2s.mp4",
        ):
            with self.subTest(source=source):
                self.assertIsNone(source_identity(source))

    def test_resolver_selects_single_source_and_excludes_generated_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "raw" / RAW_NAME
            source.parent.mkdir()
            source.touch()
            for ignored in ("fragments", "review", "qc", "segments", "order", "parts", ".cache"):
                path = root / ignored / RAW_NAME
                path.parent.mkdir()
                path.touch()
            (root / "fragment_start04_side0_top_1s_2s.mp4").touch()
            self.assertEqual(resolve_source("start4_side0_top", root), source.resolve())

    def test_resolver_reports_every_ambiguous_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / RAW_NAME
            reseq = root / f"reseq_{KEY}.mp4"
            compressed = root / f"reseq_{KEY}.low.mp4"
            for source in (raw, reseq, compressed):
                source.touch()
            with self.assertRaisesRegex(ValueError, "Ambiguous locator") as raised:
                resolve_source("start4_side0_top", root)
            for source in (raw, reseq, compressed):
                self.assertIn(str(source.resolve()), str(raised.exception))

    def test_resolver_supports_canonical_run_generic_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / f"reseq_{KEY}" / "output" / "resequenced.mp4"
            source.parent.mkdir(parents=True)
            source.touch()
            self.assertEqual(resolve_source("start4_side0_top", root), source.resolve())

    def test_resolver_does_not_follow_directory_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            search = root / "search"
            search.mkdir()
            outside = root / "outside"
            outside.mkdir()
            (outside / RAW_NAME).touch()
            (search / "linked").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(FileNotFoundError, "No local video matched"):
                resolve_source("start4_side0_top", search)

    def test_resolver_missing_wrong_type_and_no_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(FileNotFoundError):
                resolve_source("start4_side0_top", root / "missing")
            source = root / "sample.mp4"
            source.touch()
            with self.assertRaises(NotADirectoryError):
                resolve_source("start4_side0_top", source)
            with self.assertRaisesRegex(FileNotFoundError, "No local video matched"):
                resolve_source("start4_side0_top", root)

    def test_filenames_record_requested_units_and_normalize_decimal_values(self) -> None:
        self.assertEqual(
            fragment_filename(RAW_NAME, start="60.00", duration="1e1", unit="seconds"),
            "fragment_start04_side0_top_60s_10s.mp4",
        )
        self.assertEqual(
            fragment_filename(RAW_NAME, start=1500, duration=250, unit="frames"),
            "fragment_start04_side0_top_1500f_250f.mp4",
        )
        self.assertEqual(
            fragment_filename(RAW_NAME, start="1.2500", unit="seconds"),
            "fragment_start04_side0_top_1.25s_0s.png",
        )
        self.assertEqual(
            fragment_filename(RAW_NAME, start=1500, duration=0, unit="frames"),
            "fragment_start04_side0_top_1500f_0f.png",
        )
        self.assertEqual(
            fragment_filename("start04_sample_5s.mp4", start=0, unit="frames"),
            "fragment_start04_sample_5s_0f_0f.png",
        )

    def test_filenames_reject_invalid_timing(self) -> None:
        for start, duration, unit in (
            (-1, 1, "seconds"),
            (0, -1, "seconds"),
            ("NaN", 1, "seconds"),
            (0, "Infinity", "seconds"),
            ("garbage", 1, "seconds"),
            ("1.5", 1, "frames"),
            (0, "1.5", "frames"),
            (0, 1, "minutes"),
        ):
            with self.subTest(start=start, duration=duration, unit=unit):
                with self.assertRaises(ValueError):
                    fragment_filename(RAW_NAME, start=start, duration=duration, unit=unit)


class FragmentCliTests(unittest.TestCase):
    def test_default_output_is_relative_to_callers_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / RAW_NAME
            source.touch()
            caller = Path(directory) / "another-project"
            caller.mkdir()
            with (
                contextlib.chdir(caller),
                mock.patch.object(
                    cli, "create_fragment", return_value=Path("created.png")
                ) as create,
                contextlib.redirect_stdout(io.StringIO()) as stdout,
                contextlib.redirect_stderr(io.StringIO()) as stderr,
            ):
                result = cli.main(["fragment", "--video", str(source), "--start-frame", "3"])
            self.assertEqual(result, 0)
            self.assertEqual(stdout.getvalue().strip(), "created.png")
            self.assertIn(str(source.resolve()), stderr.getvalue())
            create.assert_called_once_with(
                source.resolve(),
                caller.resolve() / "data/artifacts/fragments/fragment_start04_side0_top_3f_0f.png",
                start=3,
                duration=None,
                unit="frames",
                threads=1,
                on_progress=mock.ANY,
            )

    def test_locator_and_explicit_output_preserve_requested_decimal_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / RAW_NAME
            source.touch()
            output = Path(directory) / "chosen.mp4"
            with (
                mock.patch.object(cli, "create_fragment", return_value=output) as create,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = cli.main(
                    [
                        "fragment",
                        "--locator",
                        "start4_side0_top",
                        "--data-dir",
                        directory,
                        "--start-seconds",
                        "0.04",
                        "--duration-seconds",
                        "1.20",
                        "--out",
                        str(output),
                        "--threads",
                        "2",
                    ]
                )
            self.assertEqual(result, 0)
            create.assert_called_once_with(
                source.resolve(),
                output,
                start="0.04",
                duration="1.20",
                unit="seconds",
                threads=2,
                on_progress=mock.ANY,
            )

    def test_output_directory_and_explicit_zero_duration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.mp4"
            source.touch()
            with (
                mock.patch.object(
                    cli, "create_fragment", return_value=Path("created.png")
                ) as create,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = cli.main(
                    [
                        "fragment",
                        "--video",
                        str(source),
                        "--start-seconds",
                        "0.00",
                        "--duration-seconds",
                        "0.0",
                        "--out-dir",
                        "chosen-dir",
                    ]
                )
            self.assertEqual(result, 0)
            self.assertEqual(create.call_args.args[1], Path("chosen-dir/fragment_sample_0s_0s.png"))

    def test_parser_rejects_incomplete_and_conflicting_arguments_before_extraction(self) -> None:
        cases = [
            [],
            ["fragment", "--video", "sample.mp4"],
            ["fragment", "--start-frame", "0"],
            ["fragment", "--locator", "start4_side0_top", "--start-frame", "0"],
            ["fragment", "--video", "sample.mp4", "--data-dir", ".", "--start-frame", "0"],
            ["fragment", "--video", "sample.mp4", "--start-frame", "0", "--duration-seconds", "1"],
            ["fragment", "--video", "sample.mp4", "--start-seconds", "0", "--duration-frames", "1"],
            ["fragment", "--video", "sample.mp4", "--start-frame", "1.5"],
            ["fragment", "--video", "sample.mp4", "--start-frame", "0", "--threads", "0"],
            [
                "fragment",
                "--video",
                "sample.mp4",
                "--start-frame",
                "0",
                "--out",
                "a",
                "--out-dir",
                "b",
            ],
        ]
        for arguments in cases:
            with (
                self.subTest(arguments=arguments),
                mock.patch.object(cli, "create_fragment") as create,
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                cli.main(arguments)
            self.assertEqual(raised.exception.code, 2)
            create.assert_not_called()

    def test_missing_source_reports_nonzero_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(cli, "create_fragment") as create,
                contextlib.redirect_stderr(io.StringIO()) as stderr,
            ):
                result = cli.main(
                    [
                        "fragment",
                        "--video",
                        str(Path(directory) / "missing.mp4"),
                        "--start-frame",
                        "0",
                    ]
                )
            self.assertEqual(result, 1)
            self.assertIn("missing.mp4", stderr.getvalue())
            create.assert_not_called()

    def test_extraction_failure_remains_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.mp4"
            source.touch()
            with (
                mock.patch.object(
                    cli, "create_fragment", side_effect=RuntimeError("FFmpeg failed")
                ),
                contextlib.redirect_stdout(io.StringIO()) as stdout,
                contextlib.redirect_stderr(io.StringIO()) as stderr,
            ):
                result = cli.main(["fragment", "--video", str(source), "--start-frame", "0"])
            self.assertEqual(result, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("FFmpeg failed", stderr.getvalue())

    def test_negative_or_nonfinite_time_fails_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.mp4"
            source.touch()
            for start in ("-1", "NaN", "Infinity"):
                with (
                    self.subTest(start=start),
                    mock.patch.object(cli, "create_fragment") as create,
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    result = cli.main(
                        ["fragment", "--video", str(source), "--start-seconds", start]
                    )
                self.assertEqual(result, 1)
                create.assert_not_called()

    def test_progress_off_preserves_stdout_path_and_suppresses_working_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.mp4"
            source.touch()
            with (
                mock.patch.object(
                    cli, "create_fragment", return_value=Path("created.png")
                ) as create,
                contextlib.redirect_stdout(io.StringIO()) as stdout,
                contextlib.redirect_stderr(io.StringIO()) as stderr,
            ):
                result = cli.main(
                    ["fragment", "--video", str(source), "--start-frame", "0", "--progress", "off"]
                )
            self.assertEqual(result, 0)
            self.assertEqual(stdout.getvalue(), "created.png\n")
            self.assertEqual(stderr.getvalue(), "")
            self.assertIsNone(create.call_args.kwargs["on_progress"])

    def test_progress_off_keeps_errors_visible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.mp4"
            source.touch()
            with (
                mock.patch.object(
                    cli, "create_fragment", side_effect=RuntimeError("Decoder failed")
                ),
                contextlib.redirect_stdout(io.StringIO()) as stdout,
                contextlib.redirect_stderr(io.StringIO()) as stderr,
            ):
                result = cli.main(
                    ["fragment", "--video", str(source), "--start-frame", "0", "--progress", "off"]
                )
            self.assertEqual(result, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "hive-video: error: Decoder failed\n")

    def test_cancel_returns_130_without_reporting_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.mp4"
            source.touch()
            with (
                mock.patch.object(cli, "create_fragment", side_effect=KeyboardInterrupt),
                contextlib.redirect_stdout(io.StringIO()) as stdout,
                contextlib.redirect_stderr(io.StringIO()) as stderr,
            ):
                result = cli.main(["fragment", "--video", str(source), "--start-frame", "0"])
            self.assertEqual(result, 130)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("cancelled", stderr.getvalue())
            self.assertNotIn("Honey packed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
