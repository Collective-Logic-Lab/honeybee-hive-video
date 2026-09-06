"""Local hive-video discovery and fragment names, independent of a checkout."""

from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

_LOCATOR = re.compile(r"start(?P<start>\d+)_side(?P<side>[01])_(?P<panel>top|bottom)")
_KEY = r"start(?P<start>\d+)_{1,2}\d{8}_\d{6}_side(?P<side>[01])_(?P<panel>top|bottom)"
_SOURCE_NAME = re.compile(rf"(?:reseq_(?:1_)?)?{_KEY}(?:\.(?:high|medium|low))?\.mp4")
_RUN_NAME = re.compile(rf"reseq_{_KEY}")
_GENERIC_RESEQUENCED = re.compile(r"resequenced(?:\.(?:high|medium|low))?\.mp4")
_EXCLUDED_DIRECTORIES = {"fragments", "qc", "review", "segments", "order", "parts"}


def _identity(match: re.Match[str]) -> str:
    return f"start{int(match['start']):02d}_side{match['side']}_{match['panel']}"


def parse_locator(locator: str) -> str:
    """Return a normalized archive start/side/panel identity.

    ``start`` is the archive's recording identifier, not an inferred calendar
    day. Leading zeros are accepted; output uses at least two start digits.
    """
    match = _LOCATOR.fullmatch(locator)
    if match is None:
        raise ValueError(
            f"Invalid locator {locator!r}; expected start4_side0_top "
            "(start number, side 0 or 1, panel top or bottom)"
        )
    return _identity(match)


def source_identity(source: str | Path) -> str | None:
    """Read identity from recognized archive/resequence names only.

    Generic ``resequenced.mp4`` outputs may inherit their known
    ``reseq_<archive-key>`` directory identity. Review clips and arbitrary
    filenames do not inherit identity from an otherwise unrelated parent.
    """
    source = Path(source)
    match = _SOURCE_NAME.fullmatch(source.name)
    if match is not None:
        return _identity(match)
    if _GENERIC_RESEQUENCED.fullmatch(source.name):
        for ancestor in source.parents:
            match = _RUN_NAME.fullmatch(ancestor.name)
            if match is not None:
                return _identity(match)
    return None


def resolve_source(locator: str, data_dir: str | Path) -> Path:
    """Resolve exactly one recognized local source below an explicit root.

    Raw, archival resequenced, and compressed copies are all candidates. Their
    frame orders and encodings may differ, so multiple matches are an error.
    Hidden, review, intermediate, and fragment directories are excluded;
    directory symlinks are not followed. No metadata or remote store is read.
    """
    identity = parse_locator(locator)
    root = Path(data_dir).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(f"Expected a data directory, observed {root}")
    matches: set[Path] = set()

    def fail_walk(error: OSError) -> None:
        raise error

    for directory, subdirectories, filenames in os.walk(root, onerror=fail_walk):
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if not name.startswith(".") and name not in _EXCLUDED_DIRECTORIES
        )
        for name in sorted(filenames):
            if not name.endswith(".mp4"):
                continue
            candidate = Path(directory) / name
            if source_identity(candidate) == identity and candidate.is_file():
                matches.add(candidate.resolve(strict=True))
    if not matches:
        raise FileNotFoundError(
            f"No local video matched {locator!r} below {root}; "
            "use --video for a source with an unrecognized filename"
        )
    if len(matches) > 1:
        listed = "\n".join(f"  {path}" for path in sorted(matches))
        raise ValueError(
            f"Ambiguous locator {locator!r}: {len(matches)} videos below {root}:\n"
            f"{listed}\nSelect the intended source with --video or a narrower --data-dir."
        )
    return next(iter(matches))


def _time_text(value: str | int | float | Decimal, unit: str, name: str) -> str:
    if unit not in {"seconds", "frames"}:
        raise ValueError(f"Expected unit 'seconds' or 'frames', observed {unit!r}")
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f"Expected numeric {name}, observed {value!r}") from error
    if not number.is_finite() or number < 0:
        raise ValueError(f"Expected finite non-negative {name}, observed {value!r}")
    if unit == "frames" and number != number.to_integral_value():
        raise ValueError(f"Expected integral {name} in frames, observed {value!r}")
    if number == 0:
        return "0"
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def fragment_filename(
    source: str | Path,
    *,
    start: str | int | float | Decimal,
    duration: str | int | float | Decimal | None = None,
    unit: str,
) -> str:
    """Name a fragment using requested units; zero duration denotes one PNG.

    Frame indices are zero-based. A generic source keeps its original stem
    rather than acquiring fabricated archive start/side/panel fields.
    """
    source = Path(source)
    label = source_identity(source) or source.stem
    start_text = _time_text(start, unit, "start")
    duration_text = _time_text(0 if duration is None else duration, unit, "duration")
    suffix = "s" if unit == "seconds" else "f"
    extension = "png" if duration_text == "0" else "mp4"
    return f"fragment_{label}_{start_text}{suffix}_{duration_text}{suffix}.{extension}"
