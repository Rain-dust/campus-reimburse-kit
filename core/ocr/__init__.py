#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Pluggable OCR providers for the RM reimbursement workflow."""

from .base import BaseOCRProvider, OCRResult, extract_reimbursement_fields, recognize_safely
from .factory import get_ocr_provider
from .mock_provider import MockOCRProvider
from .paddle_provider import PaddleOCRProvider
from .tencent_provider import TencentOCRProvider

__all__ = [
    "BaseOCRProvider",
    "OCRResult",
    "MockOCRProvider",
    "PaddleOCRProvider",
    "TencentOCRProvider",
    "extract_reimbursement_fields",
    "get_ocr_provider",
    "recognize_safely",
]
