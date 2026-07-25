#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Factory helpers for selecting OCR providers."""

from __future__ import annotations

import os

from .base import BaseOCRProvider
from .mock_provider import MockOCRProvider
from .paddle_provider import PaddleOCRProvider
from .tencent_provider import TencentOCRProvider


def get_ocr_provider(provider_name: str | None = None) -> BaseOCRProvider:
    """Create an OCR provider by explicit name, env config, or mock fallback."""
    selected = (
        provider_name
        or os.environ.get("RM_OCR_PROVIDER")
        or os.environ.get("OCR_PROVIDER")
        or "mock"
    )
    normalized = selected.strip().lower()

    providers = {
        "mock": MockOCRProvider,
        "paddle": PaddleOCRProvider,
        "paddleocr": PaddleOCRProvider,
        "tencent": TencentOCRProvider,
        "tencentcloud": TencentOCRProvider,
    }
    provider_cls = providers.get(normalized, MockOCRProvider)
    return provider_cls()
