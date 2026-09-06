"""Exact ordinal-frame fragments; see method HV-F001.

The API has no repository, dataset, scheduler, or working-directory assumptions.
FFmpeg and FFprobe must be on PATH. Seconds refer to the nominal frame clock
``frame_index / fps``. Sequential decoding deliberately provides the reference
path: seeking to a late frame can take time proportional to its source position.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from fractions import Fraction
from importlib.metadata import version
from pathlib import Path
from typing import Any

ProgressCallback = Callable[[str, int | None, int | None], None]


def _number(value: int | float | str, name: str) -> Fraction:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite non-negative number, got {value!r}")
    try:
        result = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"{name} must be a finite non-negative number, got {value!r}") from exc
    if result < 0:
        raise ValueError(f"{name} must be non-negative, got {value!r}")
    return result


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    # All media commands request error-only logging. A decoder error is a
    # failure even when the tool returns zero after producing a partial result.
    if completed.returncode or completed.stderr.strip():
        raise RuntimeError(
            f"{command[0]} failed (exit {completed.returncode}): "
            f"{completed.stderr.strip()[-4000:]}\nCommand: {command!r}"
        )
    return completed


def _run_encoding(command: list[str], on_progress: ProgressCallback, total: int) -> None:
    """Read FFmpeg's output-frame counters without mixing errors into telemetry."""
    # A file for stderr avoids blocking on a full error pipe while stdout is
    # consumed. Keep the same error-only failure rule as the quiet API path.
    with tempfile.TemporaryFile() as diagnostics:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=diagnostics, text=True)
        try:
            if process.stdout is None:
                raise RuntimeError("FFmpeg progress stdout was not opened")
            completed = None
            for line in process.stdout:
                key, separator, value = line.strip().partition("=")
                if not separator:
                    continue
                if key == "frame":
                    completed = int(value)
                elif key == "progress":
                    # FFmpeg cannot report decoded prefix frames here. Stay
                    # indeterminate until an actual output frame is available.
                    if completed is not None and completed > 0:
                        on_progress("encoding", completed, total)
                    completed = None
            return_code = process.wait()
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            raise
        finally:
            if process.stdout is not None:
                process.stdout.close()
        size = diagnostics.seek(0, os.SEEK_END)
        diagnostics.seek(max(0, size - 8000))
        error = diagnostics.read().decode("utf-8", errors="replace").strip()
        if return_code or error:
            raise RuntimeError(
                f"{command[0]} failed (exit {return_code}): {error[-4000:]}\nCommand: {command!r}"
            )


def _probe(path: Path, ffprobe: str, *, count_frames: bool = False) -> dict[str, Any]:
    command = [ffprobe, "-v", "error", "-select_streams", "v:0"]
    if count_frames:
        command.append("-count_frames")
    command.extend(
        [
            "-show_entries",
            "stream=width,height,codec_name,pix_fmt,avg_frame_rate,r_frame_rate,"
            "nb_frames,nb_read_frames,duration,time_base,start_time",
            "-of",
            "json",
            str(path),
        ]
    )
    data = json.loads(_run(command).stdout)
    streams = data.get("streams", [])
    if len(streams) != 1:
        raise ValueError(f"Expected a readable video stream in {path}, found {len(streams)}")
    return streams[0]


def _frame_rate(probe: dict[str, Any], source: Path) -> Fraction:
    try:
        nominal = Fraction(probe["r_frame_rate"])
        average = Fraction(probe["avg_frame_rate"])
    except (KeyError, ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"Missing or invalid source frame rate for {source}: {probe}") from exc
    if nominal <= 0 or average <= 0 or nominal != average:
        raise ValueError(
            f"Expected equal positive nominal/average frame rates for {source}; "
            f"got {nominal} and {average}. Variable-rate inputs are unsupported."
        )
    return average


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _refuse_existing(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Refusing to replace existing output: {path}")


def create_fragment(
    source: str | Path,
    output: str | Path,
    *,
    start: int | float | str,
    unit: str,
    duration: int | float | str | None = None,
    threads: int = 1,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """Write a validated MP4 or PNG and its ``<output>.json`` provenance sidecar.

    ``unit`` is ``frames`` or ``seconds``. Frame values are integral, zero-based,
    and stop-exclusive. Seconds select ``ceil(start * fps)`` through
    ``ceil((start + duration) * fps)`` (exclusive). Zero/omitted duration writes
    one RGB PNG at the resolved start; a positive duration writes video-only
    H.264 CRF 18, medium preset, yuv420p. Stored dimensions are retained and
    automatic rotation is disabled. Odd dimensions are supported for PNG only.

    Rates must pass a nominal/average metadata equality screen; timestamps are
    not independently certified constant. No requested interval is truncated.
    Outputs are published only after decoding/count validation, without
    overwriting media or sidecars. The returned path is absolute. The sidecar
    identifies the source by path/stat/probe, not a whole-source checksum.

    An optional ``on_progress(stage, completed_frames, total_frames)`` callback
    receives operational status; only encoding reports observed output frames.
    The API prints nothing. Callback exceptions stop work and propagate; no
    callback runs after publication, and encoder completion precedes validation.
    """
    if unit not in {"frames", "seconds"}:
        raise ValueError(f"unit must be 'frames' or 'seconds', got {unit!r}")
    start_value = _number(start, "start")
    duration_value = _number(0 if duration is None else duration, "duration")
    if unit == "frames" and (start_value.denominator != 1 or duration_value.denominator != 1):
        raise ValueError(f"Frame start/duration must be integers, got {start!r}, {duration!r}")
    if isinstance(threads, bool) or not isinstance(threads, int) or threads < 1:
        raise ValueError(f"threads must be a positive integer, got {threads!r}")
    if on_progress is not None and not callable(on_progress):
        raise ValueError("on_progress must be callable or None")

    source = Path(source).expanduser().resolve()
    output = Path(output).expanduser()
    output = output.parent.resolve() / output.name
    if source == output.resolve():
        raise ValueError(f"Source and output must be different paths: {source}")
    image = duration_value == 0
    expected_suffix = ".png" if image else ".mp4"
    if output.suffix.lower() != expected_suffix:
        raise ValueError(
            f"Expected {expected_suffix} output for duration={duration!r}, got {output}"
        )
    if not source.is_file():
        raise FileNotFoundError(f"Source video is missing or not a file: {source}")
    sidecar = output.with_suffix(output.suffix + ".json")
    _refuse_existing(output)
    _refuse_existing(sidecar)
    executables = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe")}
    for name, executable in executables.items():
        if executable is None:
            raise FileNotFoundError(f"Required executable {name!r} is not on PATH")
    ffmpeg, ffprobe = str(executables["ffmpeg"]), str(executables["ffprobe"])

    source_stat = source.stat()
    if on_progress is not None:
        on_progress("probing", None, None)
    probe = _probe(source, ffprobe)
    fps = _frame_rate(probe, source)
    width, height = int(probe["width"]), int(probe["height"])
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid source dimensions {width}x{height}: {source}")
    if not image and (width % 2 or height % 2):
        raise ValueError(f"H.264 yuv420p requires even dimensions, got {width}x{height}: {source}")
    scale = fps if unit == "seconds" else Fraction(1)
    first = math.ceil(start_value * scale)
    stop = first + 1 if image else math.ceil((start_value + duration_value) * scale)
    frame_count = stop - first
    if frame_count <= 0:
        raise ValueError(
            f"Interval contains no frames: start={start}, duration={duration}, fps={fps}"
        )
    total_value = probe.get("nb_frames")
    total = int(total_value) if total_value not in (None, "N/A") else None
    if total is not None and (total <= 0 or stop > total):
        raise ValueError(
            f"Requested source frames [{first}, {stop}) exceed {total} reported frames: {source}"
        )
    # For containers without a frame-count header, extraction and the decoded
    # output count below establish whether the requested interval was complete.
    if total is None and unit == "seconds" and probe.get("duration") not in (None, "N/A"):
        available = Fraction(probe["duration"])
        if start_value >= available or (not image and start_value + duration_value > available):
            raise ValueError(f"Requested interval exceeds reported duration {available}s: {source}")

    started = datetime.now(timezone.utc).isoformat()
    tool_versions = {}
    for name, executable in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        tool_versions[name] = subprocess.check_output(
            [executable, "-version"], text=True
        ).splitlines()[0]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".fragment-", dir=output.parent) as temporary:
        staging = Path(temporary)
        media = staging / ("fragment" + expected_suffix)
        metadata = staging / "fragment.json"
        filters = f"trim=start_frame={first}:end_frame={stop},setpts=PTS-STARTPTS"
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-n",
            "-xerror",
            "-err_detect",
            "explode",
            "-threads",
            str(threads),
            "-noautorotate",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-filter_threads",
            str(threads),
            "-vf",
            filters,
            "-frames:v",
            str(frame_count),
            "-fps_mode",
            "passthrough",
        ]
        if image:
            command.extend(["-c:v", "png", "-pix_fmt", "rgb24", "-update", "1"])
        else:
            command.extend(
                [
                    "-c:v",
                    "libx264",
                    "-crf",
                    "18",
                    "-preset",
                    "medium",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                ]
            )
        command.extend(["-threads", str(threads), str(media)])
        if on_progress is None:
            _run(command)
        else:
            command[1:1] = ["-progress", "pipe:1", "-nostats", "-stats_period", "0.2"]
            on_progress("extracting", None, frame_count)
            _run_encoding(command, on_progress, frame_count)
        if on_progress is not None:
            on_progress("validating", None, frame_count)
        if not media.is_file() or media.stat().st_size == 0:
            raise RuntimeError(f"No media produced for source frames [{first}, {stop}): {source}")
        rendered = _probe(media, ffprobe, count_frames=True)
        observed_count = int(rendered.get("nb_read_frames", 0))
        if observed_count != frame_count:
            raise RuntimeError(
                f"Incomplete fragment from {source}: requested frames [{first}, {stop}) "
                f"({frame_count} frames), decoded {observed_count}"
            )
        if (int(rendered["width"]), int(rendered["height"])) != (width, height):
            raise RuntimeError(f"Fragment dimensions changed from {width}x{height}: {rendered}")
        expected_codec = "png" if image else "h264"
        expected_pixels = "rgb24" if image else "yuv420p"
        if rendered["codec_name"] != expected_codec or rendered["pix_fmt"] != expected_pixels:
            raise RuntimeError(f"Unexpected fragment codec or pixel format: {rendered}")
        if not image and Fraction(rendered["avg_frame_rate"]) != fps:
            raise RuntimeError(f"Fragment frame rate changed from {fps}: {rendered}")
        after = source.stat()
        if (source_stat.st_size, source_stat.st_mtime_ns, source_stat.st_ino) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ):
            raise RuntimeError(f"Source changed during fragment extraction: {source}")
        if on_progress is not None:
            on_progress("finalizing", None, frame_count)
        report = {
            "schema_version": 1,
            "method": "HV-F001",
            "source": {
                "path": str(source),
                "size_bytes": source_stat.st_size,
                "mtime_ns": source_stat.st_mtime_ns,
                "probe": probe,
                "sha256": None,
            },
            "request": {"start": str(start_value), "duration": str(duration_value), "unit": unit},
            "interval": {
                "start_frame": first,
                "stop_frame": stop,
                "frame_count": frame_count,
                "frame_rate": str(fps),
                "clock": "zero_based_nominal_frame_clock",
            },
            "encoding": {
                "codec": expected_codec,
                "pixel_format": expected_pixels,
                "crf": None if image else 18,
                "preset": None if image else "medium",
                "audio": False,
                "autorotate": False,
                "threads": threads,
            },
            "software": {
                "package": "hive-video",
                "version": version("hive-video"),
                "module_sha256": _sha256(Path(__file__)),
                "tools": tool_versions,
                "python": platform.python_version(),
                "platform": platform.platform(),
            },
            "output": {"path": str(output), "sha256": _sha256(media), "probe": rendered},
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "command": command,
        }
        metadata.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        # Atomic no-clobber links, on the same filesystem as the destination.
        # Publish media last: a visible final media path has its sidecar ready.
        os.link(metadata, sidecar)
        try:
            os.link(media, output)
        except BaseException:
            # Remove only the sidecar created by this attempt, retaining a
            # concurrent writer's media and preserving the original failure.
            if sidecar.exists() and os.path.samefile(metadata, sidecar):
                sidecar.unlink()
            raise
    return output
