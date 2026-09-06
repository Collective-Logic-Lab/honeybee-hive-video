"""Command-line entry point for portable hive-video utilities."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .fragments import create_fragment
from .progress import BeeProgress
from .sources import fragment_filename, resolve_source


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hive-video",
        description="Local, reproducible utilities for honey bee hive video.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    fragment = commands.add_parser(
        "fragment",
        help="Extract a frame as PNG or an interval as MP4.",
        description=(
            "Extract a frame or interval from a local video using FFmpeg and ffprobe. "
            "Frame indices are zero-based. Seconds use the nominal frame clock n/fps "
            "and select [ceil(start * fps), ceil((start + duration) * fps)); a still "
            "selects ceil(start * fps). Omitted or zero duration writes one PNG. "
            "Every output receives a JSON provenance sidecar; existing files are refused."
        ),
    )
    sources = fragment.add_mutually_exclusive_group(required=True)
    sources.add_argument("--video", type=Path, help="Explicit local source video path.")
    sources.add_argument(
        "--locator", help="Archive shorthand such as start4_side0_top; requires --data-dir."
    )
    fragment.add_argument(
        "--data-dir",
        type=Path,
        help="Explicit local search root for --locator; multiple matching copies are an error.",
    )
    start = fragment.add_mutually_exclusive_group(required=True)
    start.add_argument("--start-seconds", metavar="DECIMAL", help="Non-negative start in seconds.")
    start.add_argument(
        "--start-frame", type=int, metavar="INT", help="Zero-based start frame index."
    )
    duration = fragment.add_mutually_exclusive_group()
    duration.add_argument("--duration-seconds", metavar="DECIMAL", help="Duration in seconds.")
    duration.add_argument("--duration-frames", type=int, metavar="INT", help="Number of frames.")
    destination = fragment.add_mutually_exclusive_group()
    destination.add_argument("--out", type=Path, help="Explicit .png or .mp4 output path.")
    destination.add_argument(
        "--out-dir",
        type=Path,
        help="Directory for a generated filename (default: ./data/artifacts/fragments).",
    )
    fragment.add_argument(
        "--threads", type=int, default=1, help="FFmpeg thread limit (default: 1)."
    )
    fragment.add_argument(
        "--progress",
        choices=("auto", "plain", "off"),
        default="auto",
        help="Progress on stderr: animated on terminals, plain in logs, or off (default: auto).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.locator is not None and args.data_dir is None:
        parser.error("--locator requires an explicit --data-dir")
    if args.video is not None and args.data_dir is not None:
        parser.error("--data-dir applies only to --locator; --video already selects a source")
    unit = "seconds" if args.start_seconds is not None else "frames"
    if (unit == "seconds" and args.duration_frames is not None) or (
        unit == "frames" and args.duration_seconds is not None
    ):
        parser.error("start and duration must use the same units (seconds or frames)")
    if args.threads < 1:
        parser.error(f"--threads must be a positive integer; observed {args.threads}")
    start = args.start_seconds if unit == "seconds" else args.start_frame
    duration = args.duration_seconds if unit == "seconds" else args.duration_frames
    try:
        source = (
            resolve_source(args.locator, args.data_dir)
            if args.locator is not None
            else args.video.expanduser().resolve(strict=True)
        )
        filename = fragment_filename(source, start=start, duration=duration, unit=unit)
        output_dir = args.out_dir or Path.cwd() / "data" / "artifacts" / "fragments"
        output = args.out if args.out is not None else output_dir / filename
        if args.progress != "off":
            print(f"Source: {source}", file=sys.stderr)
        with BeeProgress(args.progress) as progress:
            result = create_fragment(
                source,
                output,
                start=start,
                duration=duration,
                unit=unit,
                threads=args.threads,
                on_progress=None if args.progress == "off" else progress.update,
            )
    except KeyboardInterrupt:
        print(f"{parser.prog}: cancelled", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        print(f"{parser.prog}: error: {error}", file=sys.stderr)
        return 1
    print(result)
    return 0
