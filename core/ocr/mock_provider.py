#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Dependency-free OCR provider for local development and tests."""

from __future__ import annotations

from .base import OCRResult, BaseOCRProvider, extract_reimbursement_fields


MOCK_OCR_TEXT = """商家：深圳市某某电子科技有限公司
日期：2026-07-01
金额：128.50
票据号：RM20260701001
商品：STM32开发板 电容 电阻 传感器
"""


class MockOCRProvider(BaseOCRProvider):
    """Return deterministic OCR text so the reimbursement flow is testable."""

    name = "mock"

    def recognize(self, file_path: str) -> OCRResult:
        fields = extract_reimbursement_fields(MOCK_OCR_TEXT)
        return OCRResult(
            text=MOCK_OCR_TEXT,
            fields=fields,
            provider=self.name,
            confidence=1.0,
            raw={"mock": True, "file_path": file_path},
        )
