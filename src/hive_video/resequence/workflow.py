"""Local unattended orchestration; scientific stages and their QC gate stay unchanged."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from . import cli

PROFILE = "edmond-2019-v1"


def file_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _checkout_identity(package_dir: Path) -> dict:
    checkout = package_dir.parent.parent
    lock = checkout / "uv.lock"
    identity = {"git_revision": None, "git_dirty": None, "lockfile_sha256": None}
    if (checkout / ".git").exists():
        identity["git_revision"] = subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
        ).strip()
        identity["git_dirty"] = bool(
            subprocess.check_output(
                ["git", "-C", str(checkout), "status", "--porcelain"], text=True
            ).strip()
        )
        if lock.is_file():
            identity["lockfile_sha256"] = file_sha256(lock)
    return identity


def _write_manifest(root: Path, manifest: dict) -> None:
    manifest["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    partial = root / "run.json.partial"
    partial.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    partial.replace(root / "run.json")


def _stage(command: list[str], log_path: Path) -> None:
    print(f"Resequencing: {command[0]}", flush=True)
    with (
        log_path.open("a") as log,
        contextlib.redirect_stdout(log),
        contextlib.redirect_stderr(log),
    ):
        print(json.dumps(command), flush=True)
        outcome = cli.main(command)
    if outcome != 0:
        raise RuntimeError(f"Resequencing {command[0]} returned {outcome}; see {log_path}")


def _commands(video: Path, root: Path) -> dict[str, list[str]]:
    """Tracked edmond-2019-v1 settings; no input-dependent threshold changes."""
    qc, order, review = root / "qc", root / "order", root / "review"
    segments = str(root / "segments/segments.csv")
    order_csv, edges = str(order / "greedy_order.csv"), str(order / "ranked_edges.csv")
    return {
        "detect": [
            "detect",
            str(video),
            "--out",
            str(qc),
            "--sample-width",
            "128",
            "--mad-z",
            "12",
            "--top-n",
            "400",
            "--threshold-mode",
            "mad",
        ],
        "summarize": [
            "summarize",
            "--candidates",
            str(qc / "candidates.csv"),
            "--out",
            str(qc / "jump_events.csv"),
            "--fps",
            "25",
            "--max-gap-frames",
            "1",
            "--sort-by",
            "avg_diff",
        ],
        "prepare-cuts": [
            "prepare-cuts",
            "--events",
            str(qc / "jump_events.csv"),
            "--out",
            str(qc / "cut_review.proposed.csv"),
        ],
        "build-segments": [
            "build-segments",
            str(video),
            "--jumps",
            str(qc / "cut_review.proposed.csv"),
            "--input-kind",
            "cut-review",
            "--count-frames",
            "--out",
            segments,
        ],
        "order": [
            "order",
            "--segments",
            segments,
            "--out",
            str(order),
            "--window-frames",
            "10",
            "--sample-width",
            "96",
            "--signature",
            "trajectory",
            "--top-k",
            "10",
        ],
        "qc": [
            "qc",
            str(video),
            "--segments",
            segments,
            "--order-csv",
            order_csv,
            "--detector-metadata",
            str(qc / "metadata.json"),
            "--out-dir",
            str(review),
            "--max-robust-z",
            "15",
            "--min-margin-ratio",
            "2",
        ],
        "review": [
            "review",
            str(video),
            "--segments",
            segments,
            "--ranked-edges",
            edges,
            "--order-csv",
            order_csv,
            "--join-filter-csv",
            str(review / "auto_qc.flagged_joins.csv"),
            "--out",
            str(review / "qc_roll_flagged_joins.mp4"),
            "--seconds-each-side",
            "2",
            "--fps",
            "25",
            "--scale-width",
            "824",
            "--caption-mode",
            "pillow",
        ],
        "render": [
            "render",
            "--video",
            str(video),
            "--qc-summary",
            str(review / "auto_qc.summary.json"),
            "--detector-metadata",
            str(qc / "metadata.json"),
            "--segments",
            segments,
            "--ranked-edges",
            edges,
            "--order-csv",
            order_csv,
            "--require-complete-order",
            "--out",
            str(root / "output/resequenced.archival.mp4"),
            "--fps",
            "25",
            "--scale-width",
            "824",
            "--safeword-file",
            str(root / "STOP"),
        ],
        "compress": [
            "compress",
            str(root / "output/resequenced.archival.mp4"),
            "--out",
            str(root / "output/resequenced.mp4"),
            "--quality",
            "low",
            "--preset",
            "medium",
            "--threads",
            "1",
        ],
    }


def _validate_qc(root: Path, video: Path) -> str:
    from .diagnostics.auto_qc_segment_joins import validate_summary_inputs

    summary = root / "review/auto_qc.summary.json"
    valid, message = validate_summary_inputs(
        summary,
        video,
        root / "segments/segments.csv",
        root / "order/greedy_order.csv",
        root / "qc/metadata.json",
    )
    if not valid:
        raise ValueError(message)
    decision = json.loads(summary.read_text())["decision"]
    if decision not in {"auto_pass", "manual_review_required"}:
        raise ValueError(f"Unexpected auto-QC decision: {decision!r}")
    return decision


def _artifacts(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): file_sha256(path)
        for folder in ("qc", "segments", "order", "review", "output")
        for path in sorted((root / folder).rglob("*"))
        if path.is_file()
    }


def _check_source(manifest: dict) -> None:
    from .diagnostics.auto_qc_segment_joins import input_fingerprint

    source = manifest["source"]
    if input_fingerprint(Path(source["path"]), hash_contents=False) != source:
        raise ValueError(f"Source video changed during the run: {source['path']}")


def _render(root: Path, manifest: dict, approval: Path | None = None) -> dict:
    _check_source(manifest)
    command = list(manifest["commands"]["render"])
    if approval is not None:
        command += ["--qc-approval", str(approval)]
    _stage(command, root / "stages.log")
    _check_source(manifest)
    _stage(manifest["commands"]["compress"], root / "stages.log")
    _check_source(manifest)
    output = root / "output/resequenced.mp4"
    if not output.is_file() or not output.stat().st_size:
        raise RuntimeError(f"Renderer completed without a nonempty video: {output}")
    manifest.update(status="complete", video=str(output))
    manifest["artifacts"] = _artifacts(root)
    _write_manifest(root, manifest)
    print(f"Resequencing complete: {output}")
    return manifest


def _record_failure(root: Path, manifest: dict, error: BaseException) -> None:
    manifest.update(status="failed", error=f"{type(error).__name__}: {error}")
    _write_manifest(root, manifest)
    error.add_note(f"Resequencing artifacts and stage log: {root}")


def run_resequence(video: Path, out_dir: Path, *, profile: str) -> dict:
    """Run to a validated render or a manual-review stop, in a new directory.

    Select ``edmond-2019-v1`` explicitly for the 25 fps unattended pilot method.
    Returns a run manifest with ``status``, ``video`` and ``review_video`` paths.
    A manual-review stop is a normal return; computation failures raise.
    Source cuts remain unreviewed, even if joins pass or are manually approved.
    """
    from hive_video._binaries import setup_ffmpeg
    from hive_video.fragment import _frame_rate, _probe

    from .diagnostics.auto_qc_segment_joins import input_fingerprint

    if profile != PROFILE:
        raise ValueError(f"Expected profile {PROFILE!r}; got {profile!r}")
    video, root = Path(video).expanduser().resolve(), Path(out_dir).expanduser().resolve()
    if not video.is_file():
        raise FileNotFoundError(f"Source video does not exist: {video}")
    if root.exists():
        raise FileExistsError(f"Choose a new resequencing output directory: {root}")
    binaries = setup_ffmpeg()
    probe = _probe(video, binaries["ffprobe"]["path"])
    if _frame_rate(probe, video) != 25:
        raise ValueError(f"Profile {PROFILE} requires 25 fps input: {video}")
    commands = _commands(video, root)
    package_dir = Path(__file__).resolve().parents[1]
    manifest = {
        "schema_version": 1,
        "profile": profile,
        "status": "running",
        "cut_review_status": "unreviewed_pilot",
        "join_qc_decision": None,
        "run_dir": str(root),
        "source": input_fingerprint(video, hash_contents=False),
        "source_probe": probe,
        "commands": commands,
        "video": None,
        "review_video": None,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "software": {
            **_checkout_identity(package_dir),
            "python": sys.version,
            "executable": sys.executable,
            "packages": {
                name: version(name) for name in ("hive-video", "numpy", "opencv-python", "pillow")
            },
            "source_sha256": {
                str(p.relative_to(package_dir)): file_sha256(p)
                for p in sorted(package_dir.rglob("*.py"))
            },
        },
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "threads": {
                name: os.environ.get(name)
                for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")
            },
        },
        "binaries": binaries,
        "randomness": "none",
        "remote_artifacts": None,
    }
    root.mkdir(parents=True, exist_ok=False)
    _write_manifest(root, manifest)
    try:
        for stage in ("detect", "summarize", "prepare-cuts", "build-segments", "order", "qc"):
            _check_source(manifest)
            _stage(commands[stage], root / "stages.log")
        _check_source(manifest)
        decision = _validate_qc(root, video)
        manifest["join_qc_decision"] = decision
        if decision == "auto_pass":
            return _render(root, manifest)
        _stage(commands["review"], root / "stages.log")
        _check_source(manifest)
        from .diagnostics.approve_manual_join_qc import artifact_fingerprint, review_artifact_paths

        for path in review_artifact_paths(root / "review/auto_qc.summary.json").values():
            artifact_fingerprint(path)
        review_video = root / "review/qc_roll_flagged_joins.mp4"
        manifest.update(status="manual_review_required", review_video=str(review_video))
        manifest["artifacts"] = _artifacts(root)
        _write_manifest(root, manifest)
        print(f"Manual review required. Watch: {review_video}")
        return manifest
    except (Exception, KeyboardInterrupt) as error:
        _record_failure(root, manifest, error)
        raise


def approve_resequence(out_dir: Path, *, reviewer: str, note: str) -> dict:
    """After watching every flagged join, record approval and finish the render.

    This approves the unchanged joins only. A bad join needs correction through
    the staged tools; this function neither edits nor recalculates an order.
    """
    from .diagnostics.approve_manual_join_qc import create_approval

    if not reviewer.strip() or not note.strip():
        raise ValueError("Supply a nonempty reviewer and note describing your join review")
    root = Path(out_dir).expanduser().resolve()
    manifest = json.loads((root / "run.json").read_text())
    if manifest["status"] != "manual_review_required":
        raise ValueError(f"Expected manual_review_required; got {manifest['status']!r}")
    video = Path(manifest["source"]["path"])
    _check_source(manifest)
    if manifest["profile"] != PROFILE or manifest["commands"] != _commands(video, root):
        raise ValueError(f"Run settings or location changed: {root}")
    if manifest["artifacts"] != _artifacts(root):
        raise ValueError(f"Run artifacts changed; the saved review is stale: {root}")
    if _validate_qc(root, video) != "manual_review_required":
        raise ValueError("The current QC report no longer requests manual review")
    if (root / "output").exists():
        raise FileExistsError(f"Unexpected rendering output already exists: {root / 'output'}")
    approval = root / "review/manual_approval.json"
    try:
        create_approval(root / "review/auto_qc.summary.json", approval, reviewer, note)
        return _render(root, manifest, approval)
    except (Exception, KeyboardInterrupt) as error:
        _record_failure(root, manifest, error)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hive-video resequence")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser(
        "run", help="Resequence automatically, stopping for flagged join review."
    )
    run.add_argument("--video", type=Path, required=True)
    run.add_argument("--out-dir", type=Path, required=True)
    run.add_argument("--profile", choices=(PROFILE,), required=True)
    finish = subparsers.add_parser(
        "finish", help="Approve the reviewed joins and finish rendering."
    )
    finish.add_argument("--out-dir", type=Path, required=True)
    finish.add_argument("--reviewer", required=True)
    finish.add_argument("--note", required=True)
    args = parser.parse_args(argv)
    if args.command == "run":
        run_resequence(args.video, args.out_dir, profile=args.profile)
    else:
        approve_resequence(args.out_dir, reviewer=args.reviewer, note=args.note)
    return 0
