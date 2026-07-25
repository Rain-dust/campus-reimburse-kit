import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.ocr import (
    OCRResult,
    BaseOCRProvider,
    MockOCRProvider,
    PaddleOCRProvider,
    extract_reimbursement_fields,
    get_ocr_provider,
    recognize_safely,
)
from core.reimbursement import classify_expense
from core.local_ocr_assets import required_model_paths
from core.ocr.paddle_provider import PDF_RENDER_SCALE, extract_text_boxes


class OCRProviderTestCase(unittest.TestCase):
    def test_mock_provider_returns_structured_result(self):
        result = MockOCRProvider().recognize("receipt.pdf")

        self.assertIsInstance(result, OCRResult)
        self.assertEqual(result.provider, "mock")
        self.assertIn("深圳市某某电子科技有限公司", result.text)
        self.assertEqual(result.fields["reimbursement_amount"], "128.50")
        self.assertEqual(result.fields["expense_date"], "2026-07-01")
        self.assertEqual(result.fields["invoice_number"], "RM20260701001")
        self.assertIsNone(result.error)

    def test_paddle_provider_import_is_safe_when_dependency_missing(self):
        with patch.dict("sys.modules", {"paddleocr": None}):
            result = PaddleOCRProvider().recognize("receipt.png")

        self.assertIsInstance(result, OCRResult)
        self.assertEqual(result.provider, "paddle")
        self.assertIn("PaddleOCR", result.error)

    def test_paddle_provider_passes_complete_local_model_directories(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for model_path in required_model_paths(root):
                model_path.mkdir(parents=True)
                (model_path / "inference.pdmodel").write_bytes(b"model")
                (model_path / "inference.pdiparams").write_bytes(b"parameters")
            paddle_ocr = unittest.mock.MagicMock()
            paddle_ocr.return_value.ocr.return_value = []

            with patch.dict(os.environ, {"PADDLE_OCR_BASE_DIR": str(root)}), patch.dict(
                sys.modules, {"paddleocr": SimpleNamespace(PaddleOCR=paddle_ocr)}
            ):
                result = PaddleOCRProvider().recognize("receipt.png")

            self.assertIsNone(result.error)
            paddle_ocr.assert_called_once_with(
                use_angle_cls=True,
                lang="ch",
                show_log=False,
                det_model_dir=str(required_model_paths(root)[0]),
                rec_model_dir=str(required_model_paths(root)[1]),
                cls_model_dir=str(required_model_paths(root)[2]),
            )

    def test_paddle_provider_rejects_missing_local_models_without_initializing_paddle(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            model_path = required_model_paths(root)[0]
            model_path.mkdir(parents=True)
            (model_path / "inference.pdmodel").write_bytes(b"model")
            paddle_ocr = unittest.mock.MagicMock()

            with patch.dict(os.environ, {"PADDLE_OCR_BASE_DIR": str(root)}), patch.dict(
                sys.modules, {"paddleocr": SimpleNamespace(PaddleOCR=paddle_ocr)}
            ):
                result = PaddleOCRProvider().recognize("receipt.png")

            self.assertIn("PaddleOCR models are incomplete", result.error)
            paddle_ocr.assert_not_called()

    def test_paddle_raw_result_normalizes_polygon_to_text_boxes(self):
        raw = [[[[10, 20], [40, 20], [40, 30], [10, 30]], ["传感器", 0.99]]]

        boxes = extract_text_boxes(raw)

        self.assertEqual(len(boxes), 1)
        self.assertEqual((boxes[0].text, boxes[0].x, boxes[0].y), ("传感器", 10.0, 20.0))

    def test_paddle_pdf_render_scale_preserves_small_table_numbers(self):
        self.assertEqual(PDF_RENDER_SCALE, 4)

    def test_factory_defaults_to_mock_provider(self):
        provider = get_ocr_provider()

        self.assertIsInstance(provider, MockOCRProvider)

    def test_factory_unknown_provider_degrades_to_mock(self):
        provider = get_ocr_provider("does-not-exist")

        self.assertIsInstance(provider, MockOCRProvider)

    def test_extract_reimbursement_fields_from_mock_text(self):
        text = MockOCRProvider().recognize("receipt.jpg").text
        fields = extract_reimbursement_fields(text)

        self.assertEqual(fields["reimbursement_amount"], "128.50")
        self.assertEqual(fields["expense_date"], "2026-07-01")
        self.assertEqual(fields["merchant_name"], "深圳市某某电子科技有限公司")
        self.assertEqual(fields["invoice_number"], "RM20260701001")

    def test_extract_reimbursement_fields_prefers_tax_inclusive_total(self):
        text = """\
金额 854.87
税额 111.13
价税合计（大写）玖佰陆拾陆圆整
（小写）￥966.00
"""

        fields = extract_reimbursement_fields(text)

        self.assertEqual(fields["reimbursement_amount"], "966.00")

    def test_classifies_electronic_components_from_ocr_keywords(self):
        category = classify_expense("商品：STM32开发板 电容 电阻 传感器")

        self.assertEqual(category, "电子元器件")

    def test_recognize_safely_returns_error_result_when_provider_raises(self):
        class BrokenProvider(BaseOCRProvider):
            name = "broken"

            def recognize(self, file_path):
                raise RuntimeError("boom")

        result = recognize_safely(BrokenProvider(), "receipt.jpg")

        self.assertEqual(result.provider, "broken")
        self.assertEqual(result.text, "")
        self.assertEqual(result.fields, {})
        self.assertIn("boom", result.error)


if __name__ == "__main__":
    unittest.main()
