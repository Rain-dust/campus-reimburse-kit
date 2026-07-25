import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.ocr import OCRResult
from core.local_ocr_worker import (
    recognize_with_worker,
    result_from_payload,
    result_to_payload,
    run_worker,
)


class LocalOCRWorkerTests(unittest.TestCase):
    def test_result_round_trip_preserves_positioned_raw_output(self):
        result = OCRResult(
            text="排烟机",
            fields={"reimbursement_amount": "170.20"},
            provider="paddle",
            confidence=0.98,
            raw={"result": [[[[10, 20]], ["排烟机", 0.98]]]},
        )

        restored = result_from_payload(result_to_payload(result))

        self.assertEqual(restored.text, "排烟机")
        self.assertEqual(restored.fields["reimbursement_amount"], "170.20")
        self.assertEqual(restored.raw, result.raw)
        self.assertEqual(restored.confidence, 0.98)

    def test_result_payload_converts_numpy_like_values(self):
        class ArrayValue:
            def tolist(self):
                return [[10.0, 20.0], [30.0, 40.0]]

        payload = result_to_payload(
            OCRResult(provider="paddle", raw={"result": ArrayValue()})
        )

        self.assertEqual(
            payload["raw"]["result"],
            [[10.0, 20.0], [30.0, 40.0]],
        )

    def test_parent_returns_error_when_worker_times_out(self):
        with patch(
            "core.local_ocr_worker.subprocess.run",
            side_effect=subprocess.TimeoutExpired("worker", 90),
        ):
            result = recognize_with_worker("scan.png", timeout_seconds=90)

        self.assertEqual(result.provider, "paddle")
        self.assertIn("timed out", result.error)

    def test_parent_returns_error_on_nonzero_exit(self):
        with patch(
            "core.local_ocr_worker.subprocess.run",
            return_value=SimpleNamespace(returncode=3221225477),
        ):
            result = recognize_with_worker("scan.png")

        self.assertIn("exited", result.error)
        self.assertIn("3221225477", result.error)

    def test_parent_rejects_malformed_worker_output(self):
        def write_bad_output(command, **_kwargs):
            Path(command[-1]).write_text("not-json", encoding="utf-8")
            return SimpleNamespace(returncode=0)

        with patch("core.local_ocr_worker.subprocess.run", side_effect=write_bad_output):
            result = recognize_with_worker("scan.png")

        self.assertIn("invalid output", result.error)

    def test_parent_returns_valid_worker_result(self):
        expected = OCRResult(text="票据文本", provider="paddle", confidence=0.91)

        def write_result(command, **_kwargs):
            Path(command[-1]).write_text(
                json.dumps(result_to_payload(expected), ensure_ascii=False),
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0)

        with patch("core.local_ocr_worker.subprocess.run", side_effect=write_result):
            result = recognize_with_worker("scan.png")

        self.assertEqual(result.text, "票据文本")
        self.assertEqual(result.confidence, 0.91)
        self.assertIsNone(result.error)

    def test_worker_writes_provider_result_atomically(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            expected = OCRResult(text="本地识别", provider="paddle")
            with patch("core.local_ocr_worker.get_ocr_provider") as get_provider, patch(
                "core.local_ocr_worker.recognize_safely", return_value=expected
            ):
                status = run_worker("scan.png", output)

            self.assertEqual(status, 0)
            get_provider.assert_called_once_with("paddle")
            self.assertEqual(
                result_from_payload(json.loads(output.read_text(encoding="utf-8"))).text,
                "本地识别",
            )
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
