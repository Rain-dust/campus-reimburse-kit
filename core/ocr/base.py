#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Shared OCR provider contracts and lightweight field extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import re


@dataclass
class OCRResult:
    """Normalized OCR output returned by every provider."""

    text: str = ""
    fields: dict = field(default_factory=dict)
    provider: str = ""
    confidence: float | None = None
    raw: dict | None = None
    error: str | None = None

    def __post_init__(self):
        self.text = self.text or ""
        self.fields = self.fields or {}


class BaseOCRProvider:
    """Base class for all OCR providers."""

    name = "base"

    def recognize(self, file_path: str) -> OCRResult:
        raise NotImplementedError


def normalize_ocr_date(value: str | None) -> str | None:
    """Normalize common OCR date formats to YYYY-MM-DD."""
    if not value:
        return None
    match = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", value)
    if not match:
        return None
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def extract_reimbursement_fields(ocr_text: str) -> dict:
    """Extract reimbursement fields with conservative regex rules."""
    text = ocr_text or ""
    fields = {}

    # Chinese VAT invoices may list item subtotals before the tax-inclusive total.
    # For reimbursement, the "价税合计（小写）" amount is authoritative.
    tax_inclusive_match = re.search(
        r"价税合计[\s\S]{0,160}?(?:小写[）)]?\s*)?[￥¥]\s*([0-9]+(?:\.[0-9]{1,2})?)",
        text,
        re.IGNORECASE,
    )
    if tax_inclusive_match:
        try:
            fields["reimbursement_amount"] = f"{Decimal(tax_inclusive_match.group(1)):.2f}"
        except InvalidOperation:
            pass

    amount_patterns = [
        r"(?:金额|合计|价税合计|小写)[:：]?\s*[¥￥]?\s*([0-9]+(?:\.[0-9]{1,2})?)",
        r"[¥￥]\s*([0-9]+(?:\.[0-9]{1,2})?)",
    ]
    for pattern in amount_patterns:
        if "reimbursement_amount" in fields:
            break
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                fields["reimbursement_amount"] = f"{Decimal(match.group(1)):.2f}"
            except InvalidOperation:
                pass
            break

    date_match = re.search(r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}(?:日)?)", text)
    normalized_date = normalize_ocr_date(date_match.group(1) if date_match else None)
    if normalized_date:
        fields["expense_date"] = normalized_date

    merchant_match = re.search(
        r"(?:商家|销售方|收款方|销售方名称|卖方名称)[:：]\s*([^\r\n]+)",
        text,
    )
    if merchant_match:
        fields["merchant_name"] = merchant_match.group(1).strip(" ：:\t")

    invoice_match = re.search(
        r"(?:票据号|发票号码|发票号|单据号)[:：]?\s*([A-Za-z0-9_\-]+)",
        text,
        re.IGNORECASE,
    )
    if invoice_match:
        fields["invoice_number"] = invoice_match.group(1).strip()

    return fields


def recognize_safely(provider: BaseOCRProvider, file_path: str) -> OCRResult:
    """Run OCR without allowing provider failures to interrupt callers."""
    provider_name = getattr(provider, "name", "unknown")
    try:
        result = provider.recognize(file_path)
    except Exception as exc:
        return OCRResult(provider=provider_name, error=str(exc))

    if isinstance(result, OCRResult):
        result.provider = result.provider or provider_name
        return result

    return OCRResult(
        provider=provider_name,
        error=f"Provider {provider_name} returned unsupported result type: {type(result).__name__}",
    )
