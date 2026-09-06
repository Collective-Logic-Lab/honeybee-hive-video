from __future__ import annotations

import io
import os
import threading
import unittest
from unittest import mock

from hive_video import progress as progress_module
from hive_video.progress import BeeProgress


class TerminalStream(io.StringIO):
    def __init__(self, encoding: str = "utf-8") -> None:
        super().__init__()
        self._encoding = encoding
        self.draws: list[str] = []
        self.moved = threading.Event()

    @property
    def encoding(self) -> str:
        return self._encoding

    def isatty(self) -> bool:
        return True

    def write(self, text: str) -> int:
        text.encode(self.encoding)
        if text.startswith("\r"):
            self.draws.append(text)
            if len(self.draws) > 1 and self.draws[-1] != self.draws[0]:
                self.moved.set()
        return super().write(text)


class BeeProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        term = mock.patch.dict(os.environ, {"TERM": "xterm"})
        term.start()
        self.addCleanup(term.stop)

    def test_redirected_auto_prints_plain_stages_without_frame_spam(self) -> None:
        stream = io.StringIO()
        with BeeProgress(stream=stream) as progress:
            progress.update("probing")
            progress.update("extracting", None, 100)
            for count in range(1, 101):
                progress.update("encoding", count, 100)
            progress.update("validating", None, 100)
            progress.update("finalizing", None, 100)
        text = stream.getvalue()
        self.assertEqual(len(text.splitlines()), 6)
        self.assertIn("Checking video", text)
        self.assertIn("Reading to fragment", text)
        self.assertIn("Packing frames", text)
        self.assertIn("Checking fragment", text)
        self.assertIn("Saving", text)
        self.assertIn("Honey packed", text)
        self.assertIn("100/100 frames", text)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\r", text)
        self.assertNotIn("🐝", text)

    def test_display_does_not_invent_counts_or_percentages(self) -> None:
        stream = io.StringIO()
        with BeeProgress(stream=stream) as progress:
            progress.update("extracting", None, 125)
            self.assertNotIn("frames", stream.getvalue())
            progress.update("encoding", 3, 125)
            progress.update("encoding", 9, 125)
            progress.update("validating", None, 125)
        text = stream.getvalue()
        self.assertIn("3/125 frames", text)
        self.assertIn("9/125 frames", text)
        self.assertNotIn("125/125", text)
        self.assertNotIn("%", text)

    def test_off_is_silent_and_starts_no_thread(self) -> None:
        stream = TerminalStream()
        with mock.patch.object(progress_module.threading, "Thread") as thread:
            with BeeProgress("off", stream=stream) as progress:
                progress.update("probing")
                progress.update("encoding", 5, 5)
        self.assertEqual(stream.getvalue(), "")
        thread.assert_not_called()

    def test_plain_can_be_forced_on_a_terminal(self) -> None:
        stream = TerminalStream()
        with mock.patch.object(progress_module.threading, "Thread") as thread:
            with BeeProgress("plain", stream=stream) as progress:
                progress.update("encoding", 2, 10)
        self.assertIn("2/10 frames", stream.getvalue())
        self.assertNotIn("\x1b", stream.getvalue())
        self.assertNotIn("🐝", stream.getvalue())
        thread.assert_not_called()

    def test_dumb_terminal_uses_plain_auto_mode(self) -> None:
        stream = TerminalStream()
        with mock.patch.dict(os.environ, {"TERM": "dumb"}):
            with BeeProgress(stream=stream) as progress:
                progress.update("probing")
        self.assertIn("Checking video", stream.getvalue())
        self.assertNotIn("\x1b", stream.getvalue())
        self.assertNotIn("🐝", stream.getvalue())

    def test_bee_moves_while_no_frame_counts_are_available_and_thread_stops(self) -> None:
        stream = TerminalStream()
        before = set(threading.enumerate())
        with mock.patch.object(
            progress_module.os, "get_terminal_size", return_value=os.terminal_size((90, 24))
        ):
            with BeeProgress(stream=stream):
                self.assertTrue(
                    stream.moved.wait(1), "Animation did not move while work was waiting"
                )
        self.assertIn("🐝", stream.getvalue())
        self.assertGreaterEqual(len(stream.draws), 3)
        self.assertNotIn("%", stream.getvalue())
        self.assertTrue(stream.getvalue().endswith("\n"))
        self.assertEqual(set(threading.enumerate()), before)

    def test_terminal_width_avoids_wrapping_with_emoji(self) -> None:
        stream = TerminalStream()
        with mock.patch.object(
            progress_module.os, "get_terminal_size", return_value=os.terminal_size((17, 24))
        ):
            with BeeProgress(stream=stream) as progress:
                progress.update("extracting", None, 10000)
                progress.update("encoding", 1234, 10000)
        for draw in stream.draws:
            text = draw.removeprefix("\r\x1b[2K")
            cells = len(text) + text.count("🐝")
            self.assertLessEqual(cells, 16)

    def test_ascii_stream_uses_ascii_bee(self) -> None:
        stream = TerminalStream(encoding="ascii")
        with BeeProgress(stream=stream) as progress:
            progress.update("probing")
        self.assertIn(">i<", stream.getvalue())
        self.assertNotIn("🐝", stream.getvalue())

    def test_failure_and_cancel_leave_newline_and_propagate_original_exception(self) -> None:
        for error, label in (
            (RuntimeError("failure"), "Failed"),
            (KeyboardInterrupt(), "Cancelled"),
        ):
            with self.subTest(error=type(error)):
                stream = TerminalStream()
                before = set(threading.enumerate())
                with self.assertRaises(type(error)) as raised:
                    with BeeProgress(stream=stream) as progress:
                        progress.update("encoding", 2, 5)
                        raise error
                self.assertIs(raised.exception, error)
                self.assertIn(label, stream.getvalue())
                self.assertNotIn("Honey packed", stream.getvalue())
                self.assertTrue(stream.getvalue().endswith("\n"))
                self.assertEqual(set(threading.enumerate()), before)

    def test_unknown_mode_is_an_explicit_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown progress mode"):
            BeeProgress("maybe")

    def test_animation_write_failure_is_reported_and_thread_is_stopped(self) -> None:
        stream = TerminalStream()
        failed_write = threading.Event()
        before = set(threading.enumerate())

        def fail_write(text: str) -> int:
            failed_write.set()
            raise OSError("Terminal disconnected")

        with self.assertRaisesRegex(OSError, "Terminal disconnected"):
            with BeeProgress(stream=stream):
                with mock.patch.object(stream, "write", side_effect=fail_write):
                    self.assertTrue(failed_write.wait(1))
        self.assertEqual(set(threading.enumerate()), before)

    def test_final_display_failure_keeps_original_operation_error(self) -> None:
        stream = TerminalStream()
        original = RuntimeError("Decoder failed")
        with self.assertRaisesRegex(RuntimeError, "Decoder failed") as raised:
            with BeeProgress(stream=stream):
                stream.close()
                raise original
        self.assertIs(raised.exception, original)
        self.assertIn("Progress display failed", raised.exception.__notes__[0])


if __name__ == "__main__":
    unittest.main()
