"""A small terminal companion for long video operations."""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
import unicodedata
from typing import TextIO


class BeeProgress:
    """Animate elapsed work on a terminal; print stage changes in plain logs.

    Frame counts come only from the operation's callback. The bee keeps moving
    while a source prefix is decoded, without implying a completion percentage.
    """

    _LABELS = {
        "probing": "Checking video",
        "extracting": "Reading to fragment",
        "encoding": "Packing frames",
        "validating": "Checking fragment",
        "finalizing": "Saving",
    }

    def __init__(self, mode: str = "auto", *, stream: TextIO | None = None) -> None:
        if mode not in {"auto", "plain", "off"}:
            raise ValueError(f"Unknown progress mode {mode!r}; expected auto, plain, or off")
        self.stream = sys.stderr if stream is None else stream
        self.mode = mode
        self.animated = mode == "auto" and self.stream.isatty() and os.environ.get("TERM") != "dumb"
        self._stage = "Starting"
        self._completed: int | None = None
        self._total: int | None = None
        self._started = 0.0
        self._tick = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._render_error: Exception | None = None
        self._bee = "🐝"
        try:
            self._bee.encode(self.stream.encoding or "utf-8")
        except UnicodeEncodeError:
            self._bee = ">i<"

    def __enter__(self) -> BeeProgress:
        self._started = time.monotonic()
        if self.animated:
            self._draw()
            self._thread = threading.Thread(
                target=self._animate, name="hive-video-progress", daemon=True
            )
            self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if self._render_error is not None:
            if exc_value is not None:
                exc_value.add_note(f"Progress display failed: {self._render_error}")
                return False
            raise self._render_error
        try:
            if self.mode != "off":
                with self._lock:
                    self._stage = (
                        "Honey packed"
                        if exc_type is None
                        else "Cancelled"
                        if issubclass(exc_type, KeyboardInterrupt)
                        else "Failed"
                    )
                    if self.animated:
                        self._draw_locked()
                        self.stream.write("\n")
                        self.stream.flush()
                    else:
                        print(self._message(), file=self.stream, flush=True)
        except (OSError, ValueError) as error:
            if exc_value is None:
                raise
            exc_value.add_note(f"Progress display failed: {error}")
        return False

    def update(self, stage: str, completed: int | None = None, total: int | None = None) -> None:
        if self.mode == "off":
            return
        with self._lock:
            label = self._LABELS.get(stage, stage)
            changed = label != self._stage
            self._stage = label
            if completed is not None:
                self._completed = completed
            if total is not None:
                self._total = total
            if self.animated:
                self._draw_locked()
            elif changed:
                print(self._message(), file=self.stream, flush=True)

    def _message(self) -> str:
        elapsed = time.monotonic() - self._started
        counts = ""
        if self._completed is not None:
            denominator = f"/{self._total}" if self._total is not None else ""
            counts = f" | {self._completed}{denominator} frames"
        return f"{self._stage} | {elapsed:.1f}s{counts}"

    def _animate(self) -> None:
        while not self._stop.wait(0.1):
            try:
                self._draw()
            except (OSError, ValueError) as error:
                self._render_error = error
                self._stop.set()
                return

    def _draw(self) -> None:
        with self._lock:
            self._draw_locked()

    def _draw_locked(self) -> None:
        try:
            columns = os.get_terminal_size(self.stream.fileno()).columns
        except (AttributeError, OSError, ValueError):
            columns = shutil.get_terminal_size(fallback=(80, 24)).columns
        position = self._tick % 9
        self._tick += 1
        flight = f"[{'.' * position}{self._bee}{'.' * (8 - position)}]"
        prefix = flight if columns >= 55 else self._bee
        line = f"{prefix} {self._message()}"
        # Reserve the final terminal cell to avoid wrapping. Emoji occupy two
        # cells, so Python's character count alone would overrun narrow screens.
        budget = max(0, columns - 1)
        clipped = []
        for character in line:
            width = (
                0
                if unicodedata.combining(character)
                else 2
                if unicodedata.east_asian_width(character) in {"W", "F"}
                else 1
            )
            if width > budget:
                break
            clipped.append(character)
            budget -= width
        self.stream.write("\r\x1b[2K" + "".join(clipped))
        self.stream.flush()
