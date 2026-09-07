"""Explicit stage commands for the portable resequencing tools."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence


def _render(argv: list[str]) -> int:
    from . import reassemble_video_from_segments as renderer
    from .diagnostics.approve_manual_join_qc import validate_approval
    from .diagnostics.auto_qc_segment_joins import (
        load_complete_order,
        load_segments,
        validate_summary_inputs,
    )

    gate = argparse.ArgumentParser(
        prog="hive-video resequence render",
        add_help=False,
        allow_abbrev=False,
        description=(
            "Render only after the existing join-QC gate passes for the exact inputs. "
            "Also requires renderer --order-csv and --require-complete-order. "
            "This gate does not certify the source-cut review or absolute chronology."
        ),
    )
    gate.add_argument("--qc-summary", type=Path, required=True)
    gate.add_argument("--video", type=Path, required=True, help="Exact source covered by join QC.")
    gate.add_argument("--detector-metadata", type=Path, required=True)
    gate.add_argument("--qc-approval", type=Path, help="Required for manual_review_required.")
    if "--help" in argv or "-h" in argv:
        gate.print_help()
        renderer.parse_args(["--help"])
        return 0
    inputs, renderer_argv = gate.parse_known_args(argv)
    settings = renderer.parse_args(renderer_argv)
    if settings.order_csv is None or not settings.require_complete_order:
        gate.error("render requires --order-csv and --require-complete-order")
    valid, message = validate_summary_inputs(
        inputs.qc_summary,
        inputs.video,
        settings.segments,
        settings.order_csv,
        inputs.detector_metadata,
    )
    if not valid:
        raise ValueError(message)
    segments = load_segments(
        settings.segments.expanduser().resolve(), inputs.video.expanduser().resolve()
    )
    if any(segment.source_video is None for segment in segments.values()):
        raise ValueError("Render requires an explicit source_video in every segment row")
    load_complete_order(settings.order_csv.expanduser().resolve(), segments)
    summary = json.loads(inputs.qc_summary.expanduser().read_text())
    decision = summary["decision"]
    if decision == "manual_review_required":
        if inputs.qc_approval is None:
            raise ValueError("Join QC requires manual review; supply its validated --qc-approval")
        valid, message = validate_approval(inputs.qc_summary, inputs.qc_approval)
        if not valid:
            raise ValueError(message)
    elif decision != "auto_pass":
        raise ValueError(f"Unexpected auto-QC decision: {decision!r}")
    return renderer.main(renderer_argv)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hive-video resequence",
        description=(
            "Run one explicit resequencing stage. Render requires an input-bound QC decision "
            "and, when flagged, a current manual approval. Stage parameters and diagnostic "
            "helper functions preserve the established method. Heavy stages require "
            "the hive-video[resequence] extra."
        ),
    )
    parser.add_argument(
        "stage",
        choices=(
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
        ),
    )
    parser.add_argument("stage_args", nargs=argparse.REMAINDER, help="Arguments for that stage.")
    args = parser.parse_args(argv)
    try:
        if args.stage == "detect":
            from .detect_video_discontinuities import main as run
        elif args.stage == "summarize":
            from .summarize_jump_events import main as run
        elif args.stage == "prepare-cuts":
            from .prepare_cut_review import main as run
        elif args.stage == "build-segments":
            from .build_segments_from_jumps import main as run
        elif args.stage == "order":
            from .order_video_segments import main as run
        elif args.stage == "qc":
            from .diagnostics.auto_qc_segment_joins import main as run
        elif args.stage == "review":
            from .diagnostics.make_join_review_video import main as run
        elif args.stage == "approve":
            from .diagnostics.approve_manual_join_qc import main as run
        elif args.stage == "diagnose":
            from .diagnostics.diagnose_segment_discontinuities import main as run
        elif args.stage == "render":
            return _render(args.stage_args)
        else:
            from .compress_resequenced import main as run
        result = run(args.stage_args)
    except ModuleNotFoundError as error:
        if error.name in {"numpy", "cv2", "PIL"}:
            raise RuntimeError(
                f"Resequencing stage {args.stage!r} requires {error.name}; "
                "install the optional dependencies from the checkout with: "
                "uv tool install '.[resequence]'. For APIs in another project, use: "
                "uv add '/absolute/path/to/honeybee-hive-video[resequence]'"
            ) from error
        raise
    return 0 if result is None else result
