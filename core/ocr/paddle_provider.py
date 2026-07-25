#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""PaddleOCR adapter with optional dependency loading."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from .base import OCRResult, BaseOCRProvider, extract_reimbursement_fields
from core.inventory_line_extraction import TextBox
from core.local_ocr_assets import has_required_models, required_model_paths


SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
# Electronic invoices use small numeric table cells; rendering at 4x preserves
# quantity digits that are frequently lost at the lower default scale.
PDF_RENDER_SCALE = 4


def extract_text_boxes(raw_result) -> list[TextBox]:
    """Normalize Paddle's nested polygon output into positioned text boxes."""
    boxes: list[TextBox] = []

    def walk(node) -> None:
        if not isinstance(node, (list, tuple)):
            return
        if (
            len(node) >= 2
            and isinstance(node[0], (list, tuple))
            and node[0]
            and isinstance(node[0][0], (list, tuple))
            and len(node[0][0]) >= 2
            and isinstance(node[1], (list, tuple))
            and len(node[1]) >= 1
            and isinstance(node[1][0], str)
        ):
            try:
                boxes.append(TextBox(node[1][0], float(node[0][0][0]), float(node[0][0][1])))
            except (TypeError, ValueError):
                return
            return
        for child in node:
            walk(child)

    walk(raw_result)
    return boxes


class PaddleOCRProvider(BaseOCRProvider):
    """Local PaddleOCR provider.

    PaddleOCR is intentionally imported lazily so the app can run in mock mode
    even when local OCR dependencies are unavailable.
    """

    name = "paddle"

    def __init__(self, lang: str | None = None):
        self.lang = lang or os.environ.get("PADDLEOCR_LANG", "ch")

    def recognize(self, file_path: str) -> OCRResult:
        extension = os.path.splitext(file_path or "")[1].lower()
        if extension and extension not in SUPPORTED_IMAGE_EXTENSIONS and extension != ".pdf":
            return OCRResult(
                provider=self.name,
                error=f"PaddleOCR does not support this file type yet: {extension}",
            )

        model_root = os.environ.get("PADDLE_OCR_BASE_DIR", "").strip()
        model_paths = required_model_paths(model_root) if model_root else ()
        if not model_root or not has_required_models(model_root):
            return OCRResult(
                provider=self.name,
                error="PaddleOCR models are incomplete under PADDLE_OCR_BASE_DIR",
            )

        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            return OCRResult(
                provider=self.name,
                error=f"PaddleOCR is not installed: {exc}",
            )

        try:
            ocr = PaddleOCR(
                use_angle_cls=True,
                lang=self.lang,
                show_log=False,
                det_model_dir=str(model_paths[0]),
                rec_model_dir=str(model_paths[1]),
                cls_model_dir=str(model_paths[2]),
            )
            if extension == ".pdf":
                raw_result = self._recognize_pdf(ocr, file_path)
            else:
                raw_result = ocr.ocr(file_path, cls=True)
            text, confidence = self._flatten_result(raw_result)
            return OCRResult(
                text=text,
                fields=extract_reimbursement_fields(text),
                provider=self.name,
                confidence=confidence,
                raw={"result": raw_result},
            )
        except Exception as exc:
            return OCRResult(provider=self.name, error=f"PaddleOCR recognition failed: {exc}")

    def _recognize_pdf(self, ocr, file_path: str):
        """Render each scanned PDF page locally before PaddleOCR recognition."""
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:
            raise RuntimeError("pypdfium2 is required for local scanned-PDF OCR") from exc

        document = pdfium.PdfDocument(file_path)
        raw_pages = []
        with TemporaryDirectory(prefix="rm-paddle-pdf-") as temporary_dir:
            for index in range(len(document)):
                page = document[index]
                image = page.render(scale=PDF_RENDER_SCALE).to_pil()
                image_path = Path(temporary_dir) / f"page-{index + 1}.png"
                image.save(image_path)
                raw_pages.append(ocr.ocr(str(image_path), cls=True))
        return raw_pages

    def _flatten_result(self, raw_result) -> tuple[str, float | None]:
        texts = []
        scores = []

        def walk(node):
            if isinstance(node, dict):
                for value in node.values():
                    walk(value)
                return
            if not isinstance(node, (list, tuple)):
                return
            if (
                len(node) >= 2
                and isinstance(node[1], (list, tuple))
                and len(node[1]) >= 2
                and isinstance(node[1][0], str)
            ):
                texts.append(node[1][0])
                try:
                    scores.append(float(node[1][1]))
                except (TypeError, ValueError):
                    pass
                return
            for child in node:
                walk(child)

        walk(raw_result)
        confidence = sum(scores) / len(scores) if scores else None
        return "\n".join(texts), confidence
