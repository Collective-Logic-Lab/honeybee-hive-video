"""Resolve the FFmpeg pair without changing PATH; see method HV-P002."""

from __future__ import annotations

import contextlib
import hashlib
import os
import platform
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

_NAMES = ("ffmpeg", "ffprobe")
_OVERRIDES = ("HIVE_VIDEO_FFMPEG", "HIVE_VIDEO_FFPROBE")


def _executable(value: str | Path, name: str) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{name} must name an absolute executable path, got {value!r}")
    if not path.is_file():
        raise FileNotFoundError(f"{name} executable is missing or not a file: {path}")
    if not os.access(path, os.X_OK):
        raise PermissionError(f"{name} is not executable: {path}")
    return str(path.resolve())


@lru_cache(maxsize=1)
def _managed_binaries() -> tuple[str, str]:
    # Only request a native build on explicitly supported platforms.
    machine = platform.machine().lower()
    supported = {
        "darwin": {"arm64", "aarch64", "x86_64", "amd64"},
        "linux": {"arm64", "aarch64", "x86_64", "amd64"},
        "win32": {"x86_64", "amd64"},
    }
    if machine not in supported.get(sys.platform, set()):
        raise RuntimeError(
            f"Managed FFmpeg is unsupported on {sys.platform}/{machine}; "
            "set HIVE_VIDEO_FFMPEG and HIVE_VIDEO_FFPROBE to a compatible executable pair."
        )
    # Lazy import keeps help and archive downloads independent of provisioning.
    from filelock import FileLock
    from portable_ffmpeg import FFmpegVersions, core, get_ffmpeg

    # The provider reports download progress to stdout. Keep CLI stdout usable
    # for the output path or JSON and surface provisioning on stderr instead.
    # The provider has a thread lock; serialize preparation across processes too.
    # A timeout fails rather than breaking another process's installation lock.
    with FileLock(str(core.CACHE_DIR.parent / "hive-video.lock"), timeout=60):
        with contextlib.redirect_stdout(sys.stderr):
            paths = get_ffmpeg(version=FFmpegVersions.V8)
    return tuple(_executable(path, name) for name, path in zip(_NAMES, paths, strict=True))


def _resolve_pair() -> tuple[str, str]:
    explicit = tuple(os.environ.get(name) for name in _OVERRIDES)
    if any(value is not None for value in explicit):
        if not all(explicit):
            raise ValueError(
                "Set both HIVE_VIDEO_FFMPEG and HIVE_VIDEO_FFPROBE to absolute executable paths."
            )
        return tuple(
            _executable(value, name) for name, value in zip(_OVERRIDES, explicit, strict=True)
        )

    found = tuple(shutil.which(name) for name in _NAMES)
    if all(found):
        return tuple(
            _executable(Path(path).absolute(), name)
            for name, path in zip(_NAMES, found, strict=True)
        )
    if "SLURM_JOB_ID" in os.environ:
        raise RuntimeError(
            "FFmpeg and ffprobe are unavailable in this Slurm task. Prepare them before "
            "submission and set HIVE_VIDEO_FFMPEG and HIVE_VIDEO_FFPROBE to their absolute paths."
        )
    return _managed_binaries()


def resolve_binary(name: str) -> str:
    """Return one executable from the selected pair, provisioning it if needed."""
    if name not in _NAMES:
        raise ValueError(f"Expected 'ffmpeg' or 'ffprobe', got {name!r}")
    return _resolve_pair()[_NAMES.index(name)]


def setup_ffmpeg() -> dict[str, dict[str, str]]:
    """Prepare and inspect both executables before offline or parallel work."""
    result = {}
    for name, executable in zip(_NAMES, _resolve_pair(), strict=True):
        completed = subprocess.run(
            [executable, "-version"], capture_output=True, text=True, check=False
        )
        if completed.returncode:
            raise RuntimeError(
                f"{name} version check failed (exit {completed.returncode}) for {executable}: "
                f"{completed.stderr.strip()[-4000:]}"
            )
        lines = completed.stdout.splitlines()
        if not lines or not lines[0].startswith(f"{name} version "):
            raise RuntimeError(f"Unexpected {name} version output from {executable}: {lines!r}")
        with Path(executable).open("rb") as handle:
            checksum = hashlib.file_digest(handle, "sha256").hexdigest()
        result[name] = {"path": executable, "version": lines[0], "sha256": checksum}
    return result
