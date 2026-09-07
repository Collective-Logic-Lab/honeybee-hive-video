from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from itertools import groupby
from pathlib import Path
from unittest import mock

from hive_video._binaries import resolve_binary
from hive_video.fragment import create_fragment


def write_source(path: Path, *, width: int = 16, height: int = 12, fps: str = "10") -> None:
    """Eight lossless gray frames, separated by 24 intensity levels in RGB space."""
    pixels = b"".join(bytes([24 + 24 * index]) * (width * height * 3) for index in range(8))
    subprocess.run(
        [
            resolve_binary("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-n",
            "-f",
            "rawvideo",
            "-pixel_format",
            "rgb24",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            fps,
            "-i",
            "pipe:0",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-pix_fmt",
            "bgr0",
            "-threads",
            "1",
            str(path),
        ],
        input=pixels,
        capture_output=True,
        check=True,
    )


def decoded_frames(path: Path, *, width: int = 16, height: int = 12) -> list[bytes]:
    """Decode every stored frame independently of the utility's selection code."""
    result = subprocess.run(
        [
            resolve_binary("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-noautorotate",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-fps_mode",
            "passthrough",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-threads",
            "1",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    frame_bytes = width * height * 3
    if len(result.stdout) % frame_bytes:
        raise RuntimeError(f"Decoded size {len(result.stdout)} is not a multiple of {frame_bytes}")
    return [
        result.stdout[offset : offset + frame_bytes]
        for offset in range(0, len(result.stdout), frame_bytes)
    ]


class FragmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        resolve_binary("ffmpeg")
        resolve_binary("ffprobe")
        cls.fixtures = tempfile.TemporaryDirectory(prefix="hive video source ")
        cls.addClassCleanup(cls.fixtures.cleanup)
        cls.source = Path(cls.fixtures.name) / "source with spaces.avi"
        cls.fractional_source = Path(cls.fixtures.name) / "fractional.avi"
        cls.odd_source = Path(cls.fixtures.name) / "odd dimensions.avi"
        cls.uncounted_source = Path(cls.fixtures.name) / "no frame count.mkv"
        write_source(cls.source)
        write_source(cls.fractional_source, fps="30000/1001")
        write_source(cls.odd_source, width=15, height=11)
        subprocess.run(
            [
                resolve_binary("ffmpeg"),
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-n",
                "-i",
                str(cls.source),
                "-c",
                "copy",
                str(cls.uncounted_source),
            ],
            capture_output=True,
            check=True,
        )
        cls.reference = decoded_frames(cls.source)
        if len(cls.reference) != 8:
            raise RuntimeError(f"Expected 8 fixture frames, observed {len(cls.reference)}")

    def setUp(self) -> None:
        self.outputs = tempfile.TemporaryDirectory(prefix="hive video fragments ")
        self.addCleanup(self.outputs.cleanup)
        self.root = Path(self.outputs.name)

    def assert_complete(self, output: Path) -> None:
        self.assertTrue(output.is_file())
        metadata = output.with_suffix(output.suffix + ".json")
        self.assertTrue(metadata.is_file())
        self.assertIsInstance(json.loads(metadata.read_text()), dict)

    def assert_no_fragment(self, output: Path) -> None:
        self.assertFalse(output.exists())
        self.assertFalse(output.with_suffix(output.suffix + ".json").exists())

    def assert_video_frames(self, output: Path, indices: range) -> None:
        frames = decoded_frames(output)
        self.assertEqual(len(frames), len(indices))
        for actual, index in zip(frames, indices, strict=True):
            # H.264's RGB -> limited-range YUV420 -> RGB conversion is lossy.
            # A three-level maximum error permits conversion rounding while
            # remaining far below the fixture's 24-level adjacent-frame gap.
            self.assertLessEqual(
                max(abs(a - b) for a, b in zip(actual, self.reference[index], strict=True)),
                3,
                f"Output does not match source frame {index}",
            )

    def test_png_selects_zero_based_first_middle_and_last_frames(self) -> None:
        for index in (0, 3, 7):
            with self.subTest(index=index):
                output = self.root / f"frame {index}.png"
                result = create_fragment(self.source, output, start=index, unit="frames")
                self.assertEqual(result.resolve(), output.resolve())
                self.assert_complete(output)
                self.assertEqual(decoded_frames(output), [self.reference[index]])

    def test_omitted_and_zero_duration_make_identical_pngs(self) -> None:
        omitted = self.root / "omitted.png"
        zero = self.root / "zero.png"
        create_fragment(str(self.source), str(omitted), start="2", unit="frames")
        create_fragment(self.source, zero, start=2, duration=0, unit="frames")
        self.assert_complete(omitted)
        self.assert_complete(zero)
        self.assertEqual(decoded_frames(omitted), decoded_frames(zero))

    def test_seconds_png_selects_first_frame_at_or_after_start(self) -> None:
        output = self.root / "rounded start.png"
        create_fragment(self.source, output, start="0.11", duration="0", unit="seconds")
        self.assertEqual(decoded_frames(output), [self.reference[2]])

    def test_frame_interval_has_exact_first_last_and_count(self) -> None:
        output = self.root / "middle.mp4"
        create_fragment(self.source, output, start=2, duration=4, unit="frames")
        self.assert_complete(output)
        self.assert_video_frames(output, range(2, 6))

    def test_seconds_and_frame_intervals_select_identical_video(self) -> None:
        seconds = self.root / "seconds.mp4"
        frames = self.root / "frames.mp4"
        create_fragment(self.source, seconds, start="0.11", duration="0.2", unit="seconds")
        create_fragment(self.source, frames, start=2, duration=2, unit="frames")
        self.assert_video_frames(seconds, range(2, 4))
        self.assertEqual(decoded_frames(seconds), decoded_frames(frames))

    def test_sidecar_records_resolved_interval_and_media_checksum(self) -> None:
        output = self.root / "recorded.mp4"
        create_fragment(self.source, output, start="0.11", duration="0.2", unit="seconds")
        report = json.loads(output.with_suffix(".mp4.json").read_text())
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["source"]["path"], str(self.source.resolve()))
        self.assertEqual(report["interval"]["start_frame"], 2)
        self.assertEqual(report["interval"]["stop_frame"], 4)
        self.assertEqual(report["interval"]["frame_count"], 2)
        self.assertEqual(report["interval"]["frame_rate"], "10")
        self.assertEqual(report["output"]["path"], str(output.resolve()))
        self.assertEqual(
            report["output"]["sha256"], hashlib.sha256(output.read_bytes()).hexdigest()
        )

    def test_exact_decimal_boundaries_do_not_add_or_drop_frames(self) -> None:
        output = self.root / "decimal.mp4"
        create_fragment(self.source, output, start=0.2, duration=0.3, unit="seconds")
        self.assert_video_frames(output, range(2, 5))

    def test_interval_may_end_exactly_at_eof(self) -> None:
        output = self.root / "through eof.mp4"
        create_fragment(self.source, output, start=6, duration=2, unit="frames")
        self.assert_video_frames(output, range(6, 8))

    def test_container_without_frame_count_still_requires_complete_interval(self) -> None:
        valid = self.root / "uncounted valid.mp4"
        incomplete = self.root / "uncounted incomplete.mp4"
        create_fragment(self.uncounted_source, valid, start=6, duration=2, unit="frames")
        self.assert_video_frames(valid, range(6, 8))
        with self.assertRaisesRegex(RuntimeError, "Incomplete fragment"):
            create_fragment(self.uncounted_source, incomplete, start=6, duration=3, unit="frames")
        self.assert_no_fragment(incomplete)

    def test_fractional_rate_uses_exact_frame_clock(self) -> None:
        seconds = self.root / "fractional seconds.mp4"
        frames = self.root / "fractional frames.mp4"
        create_fragment(
            self.fractional_source,
            seconds,
            start="1001/10000",
            duration="1001/7500",
            unit="seconds",
        )
        create_fragment(self.fractional_source, frames, start=3, duration=4, unit="frames")
        self.assert_video_frames(seconds, range(3, 7))
        self.assertEqual(decoded_frames(seconds), decoded_frames(frames))

    def test_positive_seconds_interval_without_any_frames_is_rejected(self) -> None:
        output = self.root / "empty interval.mp4"
        with self.assertRaises(ValueError):
            create_fragment(self.source, output, start="0.01", duration="0.01", unit="seconds")
        self.assert_no_fragment(output)

    def test_out_of_bounds_selection_is_rejected_without_clamping(self) -> None:
        selections = (
            {"start": 8, "unit": "frames"},
            {"start": 9, "unit": "frames"},
            {"start": 7, "duration": 2, "unit": "frames"},
            {"start": "0.8", "unit": "seconds"},
            {"start": "0.7", "duration": "0.2", "unit": "seconds"},
        )
        for index, selection in enumerate(selections):
            with self.subTest(selection=selection):
                extension = ".mp4" if "duration" in selection else ".png"
                output = self.root / f"out of bounds {index}{extension}"
                with self.assertRaises(ValueError):
                    create_fragment(self.source, output, **selection)
                self.assert_no_fragment(output)

    def test_invalid_parameters_fail_without_outputs(self) -> None:
        selections = (
            {"start": -1, "unit": "frames"},
            {"start": 0, "duration": -1, "unit": "frames"},
            {"start": 0.5, "unit": "frames"},
            {"start": 0, "duration": 1.5, "unit": "frames"},
            {"start": "nan", "unit": "seconds"},
            {"start": float("inf"), "unit": "seconds"},
            {"start": 0, "duration": "nan", "unit": "seconds"},
            {"start": 0, "duration": float("inf"), "unit": "seconds"},
            {"start": 0, "unit": "milliseconds"},
            {"start": 0, "unit": "frames", "threads": 0},
        )
        for index, selection in enumerate(selections):
            with self.subTest(selection=selection):
                output = self.root / f"invalid {index}.png"
                with self.assertRaises(ValueError):
                    create_fragment(self.source, output, **selection)
                self.assert_no_fragment(output)

    def test_output_extension_must_match_fragment_type(self) -> None:
        for filename, duration in (("image.mp4", None), ("video.png", 2), ("image.jpg", 0)):
            with self.subTest(filename=filename, duration=duration):
                output = self.root / filename
                with self.assertRaises(ValueError):
                    create_fragment(self.source, output, start=0, duration=duration, unit="frames")
                self.assert_no_fragment(output)

    def test_existing_media_or_sidecar_is_preserved(self) -> None:
        for occupied_suffix in (".png", ".png.json"):
            with self.subTest(occupied_suffix=occupied_suffix):
                output = self.root / f"occupied {occupied_suffix}.png"
                occupied = output if occupied_suffix == ".png" else output.with_suffix(".png.json")
                occupied.write_bytes(b"preserve existing result")
                with self.assertRaises(FileExistsError):
                    create_fragment(self.source, output, start=0, unit="frames")
                self.assertEqual(occupied.read_bytes(), b"preserve existing result")
                other = output.with_suffix(".png.json") if occupied == output else output
                self.assertFalse(other.exists())

    def test_source_cannot_be_its_own_output(self) -> None:
        source = self.root / "self.mp4"
        create_fragment(self.source, source, start=0, duration=2, unit="frames")
        original = source.read_bytes()
        with self.assertRaises(ValueError):
            create_fragment(source, source, start=0, duration=1, unit="frames")
        self.assertEqual(source.read_bytes(), original)

    def test_concurrent_output_collision_preserves_other_writer(self) -> None:
        output = self.root / "concurrent.png"
        real_link = os.link

        def concurrent_link(source, destination, *args, **kwargs):
            if Path(destination).resolve() == output.resolve():
                output.write_bytes(b"another writer's media")
            return real_link(source, destination, *args, **kwargs)

        with mock.patch("hive_video.fragment.os.link", side_effect=concurrent_link):
            with self.assertRaises(FileExistsError):
                create_fragment(self.source, output, start=0, unit="frames")
        self.assertEqual(output.read_bytes(), b"another writer's media")
        self.assertFalse(output.with_suffix(".png.json").exists())

    def test_missing_source_fails_without_outputs(self) -> None:
        output = self.root / "missing.png"
        with self.assertRaises(FileNotFoundError):
            create_fragment(self.root / "absent.avi", output, start=0, unit="frames")
        self.assert_no_fragment(output)

    def test_unreadable_media_fails_without_finalized_outputs(self) -> None:
        source = self.root / "broken.avi"
        source.write_bytes(b"This is not a video")
        output = self.root / "broken.png"
        with self.assertRaises(RuntimeError):
            create_fragment(source, output, start=0, unit="frames")
        self.assert_no_fragment(output)

    def test_odd_stored_dimensions_are_preserved_in_png_and_rejected_for_mp4(self) -> None:
        image = self.root / "odd.png"
        video = self.root / "odd.mp4"
        create_fragment(self.odd_source, image, start=2, unit="frames")
        self.assertEqual(
            decoded_frames(image, width=15, height=11),
            [decoded_frames(self.odd_source, width=15, height=11)[2]],
        )
        with self.assertRaises(ValueError):
            create_fragment(self.odd_source, video, start=2, duration=2, unit="frames")
        self.assert_no_fragment(video)

    def test_encoder_failure_preserves_nonzero_outcome_and_no_finalized_outputs(self) -> None:
        output = self.root / "failed encode.mp4"
        real_run = subprocess.run
        encoder = resolve_binary("ffmpeg")

        def fail_encoding(command, *args, **kwargs):
            if command[0] == encoder and "-i" in command:
                Path(command[-1]).write_bytes(b"partial output")
                return subprocess.CompletedProcess(
                    command, 1, stdout="", stderr="forced encoder failure"
                )
            return real_run(command, *args, **kwargs)

        with mock.patch("hive_video.fragment.subprocess.run", side_effect=fail_encoding):
            with self.assertRaisesRegex(RuntimeError, "forced encoder failure"):
                create_fragment(self.source, output, start=1, duration=2, unit="frames")
        self.assert_no_fragment(output)

    def test_progress_preserves_media_and_reports_counts_before_publication(self) -> None:
        for suffix, duration, total in ((".png", 0, 1), (".mp4", 4, 4)):
            with self.subTest(suffix=suffix):
                quiet = self.root / ("quiet" + suffix)
                visible = self.root / ("progress" + suffix)
                events = []

                def record(stage, completed, planned):
                    self.assert_no_fragment(visible)
                    events.append((stage, completed, planned))

                create_fragment(self.source, quiet, start=2, duration=duration, unit="frames")
                create_fragment(
                    self.source,
                    visible,
                    start=2,
                    duration=duration,
                    unit="frames",
                    on_progress=record,
                )
                self.assert_complete(visible)
                self.assertEqual(decoded_frames(visible), decoded_frames(quiet))
                self.assertEqual(
                    [stage for stage, _ in groupby(event[0] for event in events)],
                    ["probing", "extracting", "encoding", "validating", "finalizing"],
                )
                self.assertEqual(events[0], ("probing", None, None))
                counts = []
                for stage, completed, planned in events[1:]:
                    self.assertEqual(planned, total)
                    if stage == "encoding":
                        self.assertGreater(completed, 0)
                        self.assertLessEqual(completed, total)
                        counts.append(completed)
                    else:
                        self.assertIsNone(completed)
                self.assertEqual(counts, sorted(counts))
                self.assertEqual(counts[-1], total)

    def test_finalizing_callback_failure_leaves_no_published_artifacts(self) -> None:
        output = self.root / "callback failure.mp4"

        def stop_before_publication(stage, completed, planned):
            if stage == "finalizing":
                raise RuntimeError("callback stopped finalization")

        with self.assertRaisesRegex(RuntimeError, "callback stopped finalization"):
            create_fragment(
                self.source,
                output,
                start=1,
                duration=2,
                unit="frames",
                on_progress=stop_before_publication,
            )
        self.assert_no_fragment(output)
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
