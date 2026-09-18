from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hive_video.resequence import cli, reassemble_video_from_segments, workflow
from hive_video.resequence.diagnostics import approve_manual_join_qc, auto_qc_segment_joins


class AutomaticResequenceRunTests(unittest.TestCase):
    """Exercise orchestration with real fingerprints and QC/approval gates, without codecs."""

    def setUp(self) -> None:
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.video = self.root / "source.mp4"
        self.video.write_bytes(b"source-identity fixture; video processing is mocked")
        self.out_dir = self.root / "run"
        self.decision = "auto_pass"
        self.failure_stage: str | None = None
        self.mutate_source_after_qc = False
        self.calls: list[list[str]] = []
        self.summary: Path | None = None
        self.patcher = mock.patch.object(cli, "main", side_effect=self.pipeline_stage)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        binaries = mock.patch(
            "hive_video._binaries.setup_ffmpeg",
            return_value={"ffprobe": {"path": "fixture-ffprobe"}},
        )
        binaries.start()
        self.addCleanup(binaries.stop)
        probe = mock.patch(
            "hive_video.fragment._probe",
            return_value={"r_frame_rate": "25/1", "avg_frame_rate": "25/1"},
        )
        self.probe = probe.start()
        self.addCleanup(probe.stop)

    @staticmethod
    def argument(argv: list[str], flag: str) -> Path:
        return Path(argv[argv.index(flag) + 1]).resolve()

    @staticmethod
    def write(path: Path, contents: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)

    def pipeline_stage(self, argv: list[str]) -> int:
        argv = [str(value) for value in argv]
        self.calls.append(argv)
        stage = argv[0]
        if stage == self.failure_stage:
            return 75
        if stage == "detect":
            output = self.argument(argv, "--out")
            self.write(
                output / "metadata.json",
                json.dumps(
                    {
                        "video": str(self.video),
                        "fps": 25.0,
                        "frame_count": 40,
                        "comparison_width": 16,
                        "comparison_height": 12,
                        "distance_median": 2.0,
                        "distance_mad": 0.5,
                    }
                ),
            )
            self.write(
                output / "candidates.csv",
                "rank,prev_frame_idx,next_frame_idx,mean_abs_diff\n0,19,20,20.0\n",
            )
        elif stage == "summarize":
            self.write(self.argument(argv, "--out"), "event_id,jump_count\n0,1\n")
        elif stage == "prepare-cuts":
            self.write(self.argument(argv, "--out"), "keep,prev_frame_idx\n1,19\n")
        elif stage == "build-segments":
            self.write(
                self.argument(argv, "--out"),
                "segment_id,source_video,start_frame_idx,end_frame_idx,duration_frames\n"
                f"0,{self.video},0,19,20\n1,{self.video},20,39,20\n",
            )
        elif stage == "order":
            output = self.argument(argv, "--out")
            self.write(output / "greedy_order.csv", "order,segment_id\n0,0\n1,1\n")
            self.write(
                output / "ranked_edges.csv",
                "from_segment_id,to_segment_id,rank,mean_abs_diff\n0,1,1,1.0\n",
            )
            self.write(output / "metadata.json", '{"fps": 25.0}\n')
        elif stage == "qc":
            output = self.argument(argv, "--out-dir")
            self.summary = output / "auto_qc.summary.json"
            inputs = {
                "video": self.video,
                "segments": self.argument(argv, "--segments"),
                "order_csv": self.argument(argv, "--order-csv"),
                "detector_metadata": self.argument(argv, "--detector-metadata"),
            }
            self.write(
                self.summary,
                json.dumps(
                    {
                        "decision": self.decision,
                        "counts": {"joins_flagged": int(self.decision != "auto_pass")},
                        "inputs": {
                            name: auto_qc_segment_joins.input_fingerprint(
                                path, hash_contents=name != "video"
                            )
                            for name, path in inputs.items()
                        },
                    }
                ),
            )
            self.write(output / "auto_qc.join_scores.csv", "join_index\n1\n")
            self.write(output / "auto_qc.flagged_joins.csv", "join_index\n1\n")
            if self.mutate_source_after_qc:
                self.video.write_bytes(b"changed source after scoring")
        elif stage == "review":
            output = self.argument(argv, "--out")
            self.write(output, "flagged review video fixture")
            self.write(output.with_suffix(".captions.csv"), "join_index,caption\n1,flagged\n")
        elif stage == "render":
            # Keep the actual report-bound render gate; mock only codec work.
            with mock.patch.object(
                reassemble_video_from_segments, "main", side_effect=self.render_artifacts
            ):
                return cli._render(argv[1:])
        elif stage == "compress":
            self.assertTrue(Path(argv[1]).is_file(), "Compression needs the completed render")
            output = self.argument(argv, "--out")
            self.write(output, "browser-playable compressed video fixture")
            self.write(output.with_suffix(".compression.json"), '{"codec_name": "h264"}\n')
        else:
            self.fail(f"Unexpected stage: {stage}")
        return 0

    def render_artifacts(self, argv: list[str]) -> int:
        output = self.argument(argv, "--out")
        self.write(output, "rendered video fixture")
        self.write(output.with_suffix(".mapping.csv"), "output_frame_idx,source_frame_idx\n0,0\n")
        self.write(
            output.with_suffix(".metadata.json"),
            json.dumps(
                {
                    "final_video_written": True,
                    "final_mapping_written": True,
                    "stopped_by_safeword": False,
                }
            ),
        )
        return 0

    def run_fixture(self) -> dict:
        return workflow.run_resequence(self.video, self.out_dir, profile="edmond-2019-v1")

    def manual_fixture(self) -> dict:
        self.decision = "manual_review_required"
        return self.run_fixture()

    def test_automatic_pass_renders_without_a_review_or_approval(self) -> None:
        with mock.patch.object(approve_manual_join_qc, "create_approval") as approve:
            result = self.run_fixture()
        self.assertEqual(result["status"], "complete")
        self.assertTrue(Path(result["video"]).is_file())
        self.assertIsNone(result["review_video"])
        self.assertEqual(
            [argv[0] for argv in self.calls],
            ["detect", "summarize", "prepare-cuts", "build-segments", "order", "qc", "render", "compress"],
        )
        self.assertIn("--require-complete-order", self.calls[-2])
        self.assertEqual(Path(result["video"]), self.argument(self.calls[-1], "--out"))
        self.assertEqual(self.calls[-1][self.calls[-1].index("--quality") + 1], "low")
        approve.assert_not_called()

    def test_flagged_run_stops_with_review_video_and_never_approves(self) -> None:
        with mock.patch.object(approve_manual_join_qc, "create_approval") as approve:
            result = self.manual_fixture()
        self.assertEqual(result["status"], "manual_review_required")
        self.assertIsNone(result["video"])
        self.assertTrue(Path(result["review_video"]).is_file())
        self.assertEqual(self.calls[-1][0], "review")
        self.assertIn("--join-filter-csv", self.calls[-1])
        self.assertNotIn("render", [argv[0] for argv in self.calls])
        approve.assert_not_called()

    def test_one_explicit_approval_finishes_without_recomputing_the_review(self) -> None:
        result = self.manual_fixture()
        review_before = Path(result["review_video"]).read_bytes()
        self.calls.clear()
        result = workflow.approve_resequence(
            self.out_dir, reviewer="fixture reviewer", note="Inspected every flagged join."
        )
        self.assertEqual(result["status"], "complete")
        self.assertTrue(Path(result["video"]).is_file())
        self.assertEqual([argv[0] for argv in self.calls], ["render", "compress"])
        approval = self.argument(self.calls[0], "--qc-approval")
        valid, message = approve_manual_join_qc.validate_approval(self.summary, approval)
        self.assertTrue(valid, message)
        self.assertEqual(
            (self.summary.parent / "qc_roll_flagged_joins.mp4").read_bytes(), review_before
        )

    def test_existing_output_directory_is_refused_even_when_empty(self) -> None:
        self.out_dir.mkdir()
        with self.assertRaises((FileExistsError, ValueError)):
            self.run_fixture()
        self.assertEqual(self.calls, [])
        marker = self.out_dir / "researcher-result.txt"
        marker.write_text("preserve this result")
        with self.assertRaises((FileExistsError, ValueError)):
            self.run_fixture()
        self.assertEqual(marker.read_text(), "preserve this result")
        self.assertEqual(self.calls, [])

    def test_invalid_profile_and_missing_source_fail_before_running_stages(self) -> None:
        with self.assertRaises(ValueError):
            workflow.run_resequence(self.video, self.out_dir, profile="unknown")
        with self.assertRaises((FileNotFoundError, ValueError)):
            workflow.run_resequence(
                self.root / "missing.mp4", self.out_dir, profile="edmond-2019-v1"
            )
        self.assertEqual(self.calls, [])

    def test_nonzero_stage_outcome_stops_the_run_and_remains_a_failure(self) -> None:
        self.failure_stage = "order"
        with self.assertRaises(RuntimeError):
            self.run_fixture()
        self.assertEqual(self.calls[-1][0], "order")
        self.assertNotIn("qc", [argv[0] for argv in self.calls])
        self.assertNotIn("render", [argv[0] for argv in self.calls])
        manifest = json.loads((self.out_dir / "run.json").read_text())
        self.assertEqual(manifest["status"], "failed")
        self.assertIn("75", manifest["error"])

    def test_preset_refuses_a_different_source_frame_rate(self) -> None:
        self.probe.return_value = {"r_frame_rate": "30/1", "avg_frame_rate": "30/1"}
        with self.assertRaisesRegex(ValueError, "25 fps"):
            self.run_fixture()
        self.assertEqual(self.calls, [])
        self.assertFalse(self.out_dir.exists())

    def test_failed_compression_preserves_render_but_never_marks_run_complete(self) -> None:
        self.failure_stage = "compress"
        with self.assertRaisesRegex(RuntimeError, "compress.*75"):
            self.run_fixture()
        self.assertEqual([argv[0] for argv in self.calls[-2:]], ["render", "compress"])
        self.assertTrue(Path(self.calls[-1][1]).is_file())
        self.assertFalse(self.argument(self.calls[-1], "--out").exists())
        manifest = json.loads((self.out_dir / "run.json").read_text())
        self.assertEqual(manifest["status"], "failed")
        self.assertIsNone(manifest["video"])

    def test_unknown_qc_decision_does_not_render_or_create_an_approval(self) -> None:
        self.decision = "unexpected"
        with self.assertRaises((ValueError, RuntimeError)):
            self.run_fixture()
        self.assertEqual(self.calls[-1][0], "qc")

    def test_source_changed_after_scoring_cannot_render(self) -> None:
        self.mutate_source_after_qc = True
        with self.assertRaisesRegex(ValueError, "current inputs|changed"):
            self.run_fixture()
        self.assertEqual(self.calls[-1][0], "qc")

    def test_source_changed_between_ordering_and_qc_fails_before_scoring(self) -> None:
        def replace_after_order(argv: list[str]) -> int:
            outcome = self.pipeline_stage(argv)
            if argv[0] == "order":
                self.video.write_bytes(b"source replaced between stages")
            return outcome

        cli.main.side_effect = replace_after_order
        with self.assertRaisesRegex(ValueError, "changed"):
            self.run_fixture()
        self.assertEqual(self.calls[-1][0], "order")
        self.assertNotIn("qc", [argv[0] for argv in self.calls])
        self.assertNotIn("render", [argv[0] for argv in self.calls])
        manifest = json.loads((self.out_dir / "run.json").read_text())
        self.assertEqual(manifest["status"], "failed")

    def test_approval_rejects_changed_source_and_review_evidence(self) -> None:
        for name in ("source", "summary", "review_video", "review_captions", "flagged_joins"):
            with self.subTest(changed=name):
                self.out_dir = self.root / name
                self.manual_fixture()
                if name == "source":
                    changed = self.video
                elif name == "summary":
                    changed = self.summary
                else:
                    changed = self.summary.parent / approve_manual_join_qc.REVIEW_ARTIFACT_NAMES[name]
                changed.write_bytes(changed.read_bytes() + b"\nchanged after review generation\n")
                self.calls.clear()
                with mock.patch.object(approve_manual_join_qc, "create_approval") as approve:
                    with self.assertRaises((ValueError, RuntimeError)):
                        workflow.approve_resequence(
                            self.out_dir, reviewer="fixture reviewer", note="Reviewed the roll."
                        )
                approve.assert_not_called()
                self.assertEqual(self.calls, [])

    def test_approval_requires_reviewer_and_review_note(self) -> None:
        self.manual_fixture()
        self.calls.clear()
        for reviewer, note in ((" ", "Reviewed the roll."), ("fixture reviewer", " ")):
            with self.subTest(reviewer=reviewer, note=note):
                with self.assertRaises(ValueError):
                    workflow.approve_resequence(self.out_dir, reviewer=reviewer, note=note)
        self.assertEqual(self.calls, [])

    def test_completed_run_cannot_receive_a_later_manual_approval(self) -> None:
        self.run_fixture()
        self.calls.clear()
        with self.assertRaises(ValueError):
            workflow.approve_resequence(
                self.out_dir, reviewer="fixture reviewer", note="Reviewed the roll."
            )
        self.assertEqual(self.calls, [])

    def test_approval_cannot_replace_existing_rendering_output(self) -> None:
        self.manual_fixture()
        (self.out_dir / "output").mkdir()
        self.calls.clear()
        with self.assertRaises(FileExistsError):
            workflow.approve_resequence(
                self.out_dir, reviewer="fixture reviewer", note="Reviewed the roll."
            )
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
