from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "src/pipeline/slurm/resequence"
SIGNATURE_CASES = (
    ("resequence_stage1_array.sh", "DETECT_SIGNATURE", "detect_video_discontinuities.py"),
    ("resequence_stage1_array.sh", "EVENTS_SIGNATURE", "summarize_jump_events.py"),
    ("resequence_stage1_array.sh", "CUT_REVIEW_SIGNATURE", "prepare_cut_review.py"),
    ("resequence_stage1a_review_array.sh", "SEGMENTS_SIGNATURE", "build_segments_from_jumps.py"),
    ("resequence_stage1a_review_array.sh", "ORDER_SIGNATURE", "order_video_segments.py"),
    (
        "resequence_stage1a_review_array.sh",
        "AUTO_QC_INPUT_SIGNATURE",
        "diagnostics/auto_qc_segment_joins.py",
    ),
    (
        "resequence_stage1a_review_array.sh",
        "REVIEW_SIGNATURE",
        "diagnostics/make_join_review_video.py",
    ),
    ("resequence_stage2_array.sh", "REASSEMBLE_SIGNATURE", "reassemble_video_from_segments.py"),
    ("resequence_upload.sh", "CURRENT_REASSEMBLE_PREFIX", "reassemble_video_from_segments.py"),
)


class PackageLauncherSignatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="hive package signatures ")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for _, _, module in SIGNATURE_CASES:
            path = self.root / "src/hive_video/resequence" / module
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# deterministic source fixture: {module}\n")
        self.resolver = self.root / "src/hive_video/_binaries.py"
        self.resolver.write_text("# deterministic binary resolver fixture\n")
        self.environment = {
            "PATH": os.environ["PATH"],
            "HIVE_VIDEO_ROOT": str(self.root),
            "SCRATCH_ROOT": str(self.root / "scratch"),
            "SSL_CERT_FILE": "",
            "CANDIDATES_FINGERPRINT": "candidates",
            "EVENTS_FINGERPRINT": "events",
            "CUTS_FINGERPRINT": "cuts",
            "SEGMENTS_FINGERPRINT": "segments",
            "ORDER_FINGERPRINT": "order",
        }

    def signature(self, worker: str, variable: str) -> str:
        # Evaluate the actual tracked code-identity assignments without running
        # a worker, allocation, environment sync, media command, or transfer.
        text = (SCRIPTS / worker).read_text().replace("\\\n", "")
        statements = [
            line.strip()
            for line in text.splitlines()
            if line.strip().startswith(variable + "=")
            and any(
                field in line
                for field in ("|tool=", "|detector_implementation=", "|binary_resolver=")
            )
        ]
        self.assertTrue(statements, f"Missing code identity for {variable} in {worker}")
        script = (
            'source "$1"\n'
            + f'{variable}="baseline"\n'
            + "\n".join(statements)
            + "\nprintf '%s\\n' \"${"
            + variable
            + '}"\n'
        )
        result = subprocess.run(
            ["bash", "-c", script, "bash", str(SCRIPTS / "common.sh")],
            env=self.environment,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def step_needed(self, marker: Path, signature: str, output: Path) -> bool:
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"\nhv_step_needed "$2" "$3" "$4"',
                "bash",
                str(SCRIPTS / "common.sh"),
                str(marker),
                signature,
                str(output),
            ],
            env=self.environment,
            capture_output=True,
            text=True,
        )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return result.returncode == 0

    def test_canonical_source_edit_invalidates_each_existing_completion_marker(self) -> None:
        marker = self.root / "complete"
        output = self.root / "result"
        output.write_text("validated fixture output\n")
        for worker, variable, module in SIGNATURE_CASES:
            with self.subTest(worker=worker, signature=variable):
                before = self.signature(worker, variable)
                marker.write_text(before + "\n")
                self.assertFalse(self.step_needed(marker, before, output))
                source = self.root / "src/hive_video/resequence" / module
                source.write_text(source.read_text() + "# changed implementation\n")
                after = self.signature(worker, variable)
                self.assertNotEqual(before, after)
                self.assertTrue(self.step_needed(marker, after, output))

    def test_detector_edit_invalidates_auto_qc_signature(self) -> None:
        worker = "resequence_stage1a_review_array.sh"
        variable = "AUTO_QC_INPUT_SIGNATURE"
        before = self.signature(worker, variable)
        detector = self.root / "src/hive_video/resequence/detect_video_discontinuities.py"
        detector.write_text(detector.read_text() + "# changed direct scientific dependency\n")
        self.assertNotEqual(before, self.signature(worker, variable))

    def test_resolver_edit_invalidates_media_steps_only(self) -> None:
        output = self.root / "media output"
        output.write_text("validated fixture output\n")
        for worker, variable, _ in SIGNATURE_CASES:
            with self.subTest(worker=worker, signature=variable):
                before = self.signature(worker, variable)
                marker = self.root / "complete"
                marker.write_text(before + "\n")
                self.assertFalse(self.step_needed(marker, before, output))
                self.resolver.write_text(self.resolver.read_text() + "# changed resolution\n")
                after = self.signature(worker, variable)
                uses_media = variable not in {"EVENTS_SIGNATURE", "CUT_REVIEW_SIGNATURE"}
                self.assertEqual(before != after, uses_media)
                self.assertEqual(self.step_needed(marker, after, output), uses_media)

    def test_upload_and_compute_bind_identical_reassembly_code(self) -> None:
        computed = self.signature("resequence_stage2_array.sh", "REASSEMBLE_SIGNATURE")
        uploaded = self.signature("resequence_upload.sh", "CURRENT_REASSEMBLE_PREFIX")
        pattern = r"(?:^|\|)(tool|binary_resolver)=([^|]+)"
        computed_fields = re.findall(pattern, computed)
        uploaded_fields = re.findall(pattern, uploaded)
        self.assertEqual([field for field, _ in computed_fields], ["tool", "binary_resolver"])
        self.assertEqual(computed_fields, uploaded_fields)


class PackageLauncherBinaryTests(unittest.TestCase):
    def check_cluster_pair(self, *, load_module: bool) -> None:
        with tempfile.TemporaryDirectory(prefix="hive binary pins ") as temporary:
            root = Path(temporary).resolve()
            cluster_bin = root / "cluster bin"
            venv_bin = root / "venv bin"
            module_log = root / "module.log"
            for directory in (cluster_bin, venv_bin):
                directory.mkdir()
                for name in ("ffmpeg", "ffprobe"):
                    executable = directory / name
                    executable.write_text("#!/bin/sh\nexit 0\n")
                    executable.chmod(0o755)
            environment = {
                "PATH": "/usr/bin:/bin" if load_module else f"{cluster_bin}:/usr/bin:/bin",
                "HIVE_VIDEO_ROOT": str(root),
                "SCRATCH_ROOT": str(root / "scratch"),
                "SSL_CERT_FILE": "",
                "HIVE_VIDEO_FFMPEG": "/inherited/unselected/ffmpeg",
                "HIVE_VIDEO_FFPROBE": "/inherited/unselected/ffprobe",
                "FIXTURE_CLUSTER_BIN": str(cluster_bin),
                "FIXTURE_VENV_BIN": str(venv_bin),
                "FIXTURE_MODULE_LOG": str(module_log),
                "FIXTURE_PYTHON": sys.executable,
            }
            script = """
source "$1"
module() {
  printf '%s\n' "$*" > "$FIXTURE_MODULE_LOG"
  export PATH="$FIXTURE_CLUSTER_BIN:$PATH"
}
hv_require_ffmpeg
export PATH="$FIXTURE_VENV_BIN:$PATH"
"$FIXTURE_PYTHON" - <<'PY'
import json
import shutil
from hive_video._binaries import resolve_binary
print(json.dumps({
    "resolved": [resolve_binary(name) for name in ("ffmpeg", "ffprobe")],
    "path": [shutil.which(name) for name in ("ffmpeg", "ffprobe")],
}))
PY
"""
            result = subprocess.run(
                ["bash", "-c", script, "bash", str(SCRIPTS / "common.sh")],
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            )
            report = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual(
                report["resolved"], [str(cluster_bin / name) for name in ("ffmpeg", "ffprobe")]
            )
            self.assertEqual(
                report["path"], [str(venv_bin / name) for name in ("ffmpeg", "ffprobe")]
            )
            self.assertEqual(module_log.exists(), load_module)
            if load_module:
                self.assertEqual(module_log.read_text(), "load ffmpeg-6.0-gcc-12.1.0\n")

    def test_existing_pair_stays_selected_after_venv_path_prepend(self) -> None:
        self.check_cluster_pair(load_module=False)

    def test_loaded_cluster_module_stays_selected_after_venv_path_prepend(self) -> None:
        self.check_cluster_pair(load_module=True)


class PackageLauncherSyntaxTests(unittest.TestCase):
    def test_resequence_shell_scripts_parse(self) -> None:
        for script in sorted(SCRIPTS.glob("*.sh")):
            with self.subTest(script=script.name):
                subprocess.run(["bash", "-n", str(script)], check=True, capture_output=True)

    def test_embedded_python_has_no_legacy_download_or_resequence_imports(self) -> None:
        for script in sorted(SCRIPTS.glob("*.sh")):
            with self.subTest(script=script.name):
                self.assertNotRegex(
                    script.read_text(), r"\b(?:from|import) src\.(?:download|resequence)\b"
                )

    def test_launchers_do_not_reference_the_removed_resequence_directory(self) -> None:
        for script in sorted(SCRIPTS.glob("*.sh")):
            with self.subTest(script=script.name):
                self.assertNotIn("src/resequence/", script.read_text())


if __name__ == "__main__":
    unittest.main()
