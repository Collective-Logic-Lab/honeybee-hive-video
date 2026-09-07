from __future__ import annotations

import contextlib
import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hive_video.resequence import (
    build_segments_from_jumps,
    cli,
    prepare_cut_review,
    reassemble_video_from_segments,
    summarize_jump_events,
)
from hive_video.resequence.diagnostics import approve_manual_join_qc, auto_qc_segment_joins


class ResequencePackageTests(unittest.TestCase):
    def test_event_and_cut_helpers_preserve_inclusive_segment_boundaries(self) -> None:
        candidates = [
            {"prev_frame_idx": 4, "next_frame_idx": 5, "mean_abs_diff": 9.0},
            {"prev_frame_idx": 5, "next_frame_idx": 6, "mean_abs_diff": 12.0},
            {"prev_frame_idx": 19, "next_frame_idx": 20, "mean_abs_diff": 20.0},
        ]
        groups = summarize_jump_events.group_events(candidates, max_gap_frames=1)
        events = [
            summarize_jump_events.summarize_event(i, group, 10.0) for i, group in enumerate(groups)
        ]
        self.assertEqual([event["jump_count"] for event in events], [2, 1])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events_path = root / "events.csv"
            cuts_path = root / "cuts.csv"
            segments_path = root / "segments.csv"
            summarize_jump_events.write_events(events_path, events)
            rows = prepare_cut_review.prepare_rows(events_path)
            self.assertEqual(
                [(row["prev_frame_idx"], row["keep"]) for row in rows], [("5", "0"), ("19", "1")]
            )
            prepare_cut_review.write_rows(cuts_path, rows)
            with contextlib.redirect_stdout(io.StringIO()):
                cuts = build_segments_from_jumps.read_jump_prev_frames(
                    cuts_path,
                    top_n=2,
                    input_kind="cut-review",
                    single_jump_events_only=False,
                    max_duration_frames=None,
                )
                build_segments_from_jumps.write_segments(
                    segments_path,
                    root / "source.mp4",
                    40,
                    10.0,
                    4.0,
                    cuts,
                )
            self.assertEqual(cuts, [19])
            with segments_path.open() as handle:
                segments = list(csv.DictReader(handle))
            self.assertEqual(
                [
                    (
                        int(row["start_frame_idx"]),
                        int(row["end_frame_idx"]),
                        int(row["duration_frames"]),
                    )
                    for row in segments
                ],
                [(0, 19, 20), (20, 39, 20)],
            )

    def test_installed_module_and_group_write_identical_cut_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events = root / "events.csv"
            events.write_text(
                "rank,event_id,jump_count,duration_frames,peak_prev_frame_idx,"
                "avg_mean_abs_diff,max_mean_abs_diff\n1,7,1,2,19,20.0,20.0\n"
            )
            direct = root / "direct.csv"
            with mock.patch.object(sys, "argv", ["unrelated-process", "--unrelated"]):
                with contextlib.redirect_stdout(io.StringIO()):
                    prepare_cut_review.main(["--events", str(events), "--out", str(direct)])
            for name, command in (
                ("module", ["-m", "hive_video.resequence.prepare_cut_review"]),
                ("group", ["-m", "hive_video.resequence", "prepare-cuts"]),
            ):
                with self.subTest(entrypoint=name):
                    out = root / (name + ".csv")
                    result = subprocess.run(
                        [sys.executable, *command, "--events", str(events), "--out", str(out)],
                        cwd=root,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(out.read_bytes(), direct.read_bytes())
            self.assertIn(b"1,19,7,1,1,2,20.0,20.0,", direct.read_bytes())

    def test_all_stage_help_entrypoints_accept_explicit_argv(self) -> None:
        stages = (
            "detect",
            "summarize",
            "prepare-cuts",
            "build-segments",
            "order",
            "qc",
            "review",
            "approve",
            "diagnose",
            "render",
            "compress",
        )
        commands = [(stage,) for stage in stages] + [("approve", "create"), ("approve", "check")]
        for command in commands:
            with self.subTest(command=command), contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as stopped:
                    cli.main([*command, "--help"])
                self.assertEqual(stopped.exception.code, 0)
                usages = [
                    line for line in output.getvalue().splitlines() if line.startswith("usage:")
                ]
                self.assertTrue(usages)
                for usage in usages:
                    self.assertTrue(
                        usage.startswith("usage: hive-video resequence " + " ".join(command)), usage
                    )


class ResequenceRenderGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.video = self.root / "source.mp4"
        self.video.write_bytes(b"explicit source-identity fixture; rendering is mocked")
        self.segments = self.root / "segments.csv"
        self.segments.write_text(
            "segment_id,source_video,start_frame_idx,end_frame_idx,duration_frames\n"
            f"0,{self.video},0,3,4\n1,{self.video},4,7,4\n"
        )
        self.order = self.root / "order.csv"
        self.order.write_text("order,segment_id\n0,0\n1,1\n")
        self.metadata = self.root / "detector.json"
        self.metadata.write_text('{"comparison_width": 16, "comparison_height": 12}\n')
        self.summary = self.root / "auto_qc.summary.json"
        self.approval = self.root / "approval.json"
        self.output = self.root / "rendered.mp4"
        self.arguments = [
            "render",
            "--qc-summary",
            str(self.summary),
            "--video",
            str(self.video),
            "--detector-metadata",
            str(self.metadata),
            "--segments",
            str(self.segments),
            "--order-csv",
            str(self.order),
            "--require-complete-order",
            "--ranked-edges",
            str(self.root / "ranked.csv"),
            "--out",
            str(self.output),
        ]
        self.write_summary("auto_pass")

    def write_summary(self, decision: str) -> None:
        self.summary.write_text(
            json.dumps(
                {
                    "decision": decision,
                    "inputs": {
                        name: auto_qc_segment_joins.input_fingerprint(
                            path, hash_contents=name != "video"
                        )
                        for name, path in {
                            "video": self.video,
                            "segments": self.segments,
                            "order_csv": self.order,
                            "detector_metadata": self.metadata,
                        }.items()
                    },
                }
            )
        )

    def approve_fixture(self) -> None:
        self.write_summary("manual_review_required")
        for name in approve_manual_join_qc.REVIEW_ARTIFACT_NAMES.values():
            (self.root / name).write_bytes(b"manual-review artifact fixture")
        approve_manual_join_qc.create_approval(
            self.summary,
            self.approval,
            reviewer="fixture reviewer",
            note="test fixture",
        )

    def test_matching_automatic_decision_reaches_renderer_with_unchanged_stage_arguments(
        self,
    ) -> None:
        with mock.patch.object(reassemble_video_from_segments, "main", return_value=75) as render:
            self.assertEqual(cli.main(self.arguments), 75)
        self.assertEqual(render.call_args.args[0], self.arguments[7:])

    def test_changed_fingerprinted_inputs_never_reach_renderer(self) -> None:
        self.metadata.write_text('{"comparison_width": 32, "comparison_height": 12}\n')
        with mock.patch.object(reassemble_video_from_segments, "main") as render:
            with self.assertRaisesRegex(ValueError, "current inputs"):
                cli.main(self.arguments)
        render.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_current_manual_approval_is_required_and_sufficient(self) -> None:
        self.approve_fixture()
        with mock.patch.object(reassemble_video_from_segments, "main", return_value=0) as render:
            with self.assertRaisesRegex(ValueError, "manual review"):
                cli.main(self.arguments)
            render.assert_not_called()
            self.assertEqual(cli.main(self.arguments + ["--qc-approval", str(self.approval)]), 0)
            render.assert_called_once()

    def test_changed_manual_review_artifact_makes_approval_stale(self) -> None:
        self.approve_fixture()
        (self.root / "qc_roll_flagged_joins.captions.csv").write_bytes(b"changed captions")
        with mock.patch.object(reassemble_video_from_segments, "main") as render:
            with self.assertRaisesRegex(ValueError, "stale"):
                cli.main(self.arguments + ["--qc-approval", str(self.approval)])
        render.assert_not_called()

    def test_segment_sources_must_match_the_guarded_source(self) -> None:
        self.segments.write_text(self.segments.read_text().replace(str(self.video), "other.mp4"))
        self.write_summary("auto_pass")
        with mock.patch.object(reassemble_video_from_segments, "main") as render:
            with self.assertRaisesRegex(ValueError, "does not match"):
                cli.main(self.arguments)
        render.assert_not_called()

    def test_unknown_qc_decision_and_incomplete_order_are_rejected(self) -> None:
        with mock.patch.object(reassemble_video_from_segments, "main") as render:
            self.write_summary("unknown")
            with self.assertRaisesRegex(ValueError, "Unexpected auto-QC decision"):
                cli.main(self.arguments)
            self.order.write_text("order,segment_id\n0,0\n")
            self.write_summary("auto_pass")
            with self.assertRaisesRegex(ValueError, "every segment exactly once"):
                cli.main(self.arguments)
        render.assert_not_called()


if __name__ == "__main__":
    unittest.main()
