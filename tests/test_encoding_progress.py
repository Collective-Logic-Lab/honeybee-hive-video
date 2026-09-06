from __future__ import annotations

import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from hive_video.fragments import _run_encoding


class EncodingProgressTests(unittest.TestCase):
    def test_progress_uses_observed_positive_output_frames(self) -> None:
        command = [
            sys.executable,
            "-u",
            "-c",
            "print('frame=0\\nprogress=continue\\nframe=2\\nprogress=continue\\n' "
            "'frame=4\\nprogress=end', flush=True)",
        ]
        events = []
        _run_encoding(command, lambda *event: events.append(event), 4)
        self.assertEqual(events, [("encoding", 2, 4), ("encoding", 4, 4)])

    def test_callback_exception_terminates_and_reaps_live_child(self) -> None:
        command = [
            sys.executable,
            "-u",
            "-c",
            "import time; print('frame=1\\nprogress=continue', flush=True); time.sleep(3)",
        ]
        real_popen = subprocess.Popen

        for failure in (RuntimeError("callback failed"), KeyboardInterrupt("operator stop")):
            with self.subTest(failure=type(failure).__name__):
                children = []

                def start_child(*args, **kwargs):
                    child = real_popen(*args, **kwargs)
                    children.append(child)
                    return child

                def stop_callback(stage, completed, total):
                    self.assertIsNone(children[0].poll())
                    raise failure

                started = time.monotonic()
                try:
                    with mock.patch(
                        "hive_video.fragments.subprocess.Popen", side_effect=start_child
                    ):
                        with self.assertRaises(type(failure)) as raised:
                            _run_encoding(command, stop_callback, 4)
                    self.assertIs(raised.exception, failure)
                    self.assertEqual(len(children), 1)
                    self.assertIsNotNone(children[0].returncode)
                    self.assertTrue(children[0].stdout.closed)
                    self.assertLess(time.monotonic() - started, 2)
                finally:
                    for child in children:
                        if child.poll() is None:
                            child.kill()
                        child.wait(timeout=1)

    def test_large_error_stream_cannot_block_progress_and_retains_diagnostic_tail(self) -> None:
        command = [
            sys.executable,
            "-u",
            "-c",
            "import sys; sys.stderr.write('x' * 131072 + '\\nDIAGNOSTIC_TAIL\\n'); "
            "sys.stderr.flush(); print('frame=2\\nprogress=end', flush=True); sys.exit(7)",
        ]
        real_popen = subprocess.Popen
        children = []
        watchdogs = []

        def start_child(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            # A pipe deadlock regression must fail this test instead of hanging
            # the suite. Successful runs cancel this bounded fallback immediately.
            watchdog = threading.Timer(3, child.kill)
            watchdog.daemon = True
            watchdog.start()
            watchdogs.append(watchdog)
            return child

        events = []
        try:
            with mock.patch("hive_video.fragments.subprocess.Popen", side_effect=start_child):
                with self.assertRaises(RuntimeError) as raised:
                    _run_encoding(command, lambda *event: events.append(event), 2)
            self.assertIn("exit 7", str(raised.exception))
            self.assertIn("DIAGNOSTIC_TAIL", str(raised.exception))
            self.assertEqual(events, [("encoding", 2, 2)])
            self.assertEqual(children[0].returncode, 7)
        finally:
            for watchdog in watchdogs:
                watchdog.cancel()
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=1)

    def test_error_diagnostics_remain_failure_even_after_zero_exit(self) -> None:
        command = [
            sys.executable,
            "-u",
            "-c",
            "import sys; print('decode failure', file=sys.stderr); "
            "print('frame=2\\nprogress=end', flush=True)",
        ]
        with self.assertRaisesRegex(RuntimeError, r"exit 0.*decode failure"):
            _run_encoding(command, lambda *event: None, 2)


if __name__ == "__main__":
    unittest.main()
