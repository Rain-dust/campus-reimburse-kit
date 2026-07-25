#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Tencent Cloud OCR adapter.

This keeps the existing Tencent OCR implementation available behind the new
provider interface, while failing safely when credentials or SDK dependencies
are not configured in the current environment.
"""

from __future__ import annotations

import json
import os

from core.reimbursement import clean_amount, format_amount

from .base import OCRResult, BaseOCRProvider, extract_reimbursement_fields, normalize_ocr_date


class TencentOCRProvider(BaseOCRProvider):
    """Adapter around the legacy Tencent VAT invoice OCR client."""

    name = "tencent"

    def recognize(self, file_path: str) -> OCRResult:
        if not self._is_configured():
            return OCRResult(
                provider=self.name,
                error="Tencent OCR is not configured or not implemented in this environment",
            )

        try:
            from core.invoice_formatter import InvoiceFormatter
            from core.ocr_api import OCRClient
        except ImportError as exc:
            return OCRResult(
                provider=self.name,
                error=f"Tencent OCR dependencies are not installed: {exc}",
            )

        try:
            response_json = OCRClient().recognize_vat_invoice(image_path=file_path)
            formatted_data = InvoiceFormatter.format_invoice_data(json_string=response_json)
            text = self._format_text(response_json, formatted_data)
            fields = self._extract_fields(text, formatted_data)
            return OCRResult(
                text=text,
                fields=fields,
                provider=self.name,
                raw={
                    "response": self._json_or_text(response_json),
                    "formatted": formatted_data,
                },
            )
        except Exception as exc:
            return OCRResult(provider=self.name, error=f"Tencent OCR recognition failed: {exc}")

    def _is_configured(self) -> bool:
        secret_id = os.environ.get("TENCENT_SECRET_ID")
        secret_key = os.environ.get("TENCENT_SECRET_KEY")
        if secret_id and secret_key:
            return True

        try:
            from app.models import Settings

            secret_id = secret_id or Settings.get_value("TENCENT_SECRET_ID")
            secret_key = secret_key or Settings.get_value("TENCENT_SECRET_KEY")
        except Exception:
            return False

        return bool(secret_id and secret_key)

    def _format_text(self, response_json: str, formatted_data: dict) -> str:
        if formatted_data:
            return json.dumps(formatted_data, ensure_ascii=False)
        return response_json or ""

    def _json_or_text(self, value: str):
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return value

    def _extract_fields(self, text: str, formatted_data: dict) -> dict:
        fields = extract_reimbursement_fields(text)

        basic_info = formatted_data.get("基本信息", {}) if formatted_data else {}
        seller_info = formatted_data.get("销售方信息", {}) if formatted_data else {}
        amount_info = formatted_data.get("金额信息", {}) if formatted_data else {}

        if basic_info.get("发票号码"):
            fields.setdefault("invoice_number", basic_info["发票号码"])
        if basic_info.get("开票日期标准格式") or basic_info.get("开票日期"):
            date = normalize_ocr_date(basic_info.get("开票日期标准格式") or basic_info.get("开票日期"))
            if date:
                fields.setdefault("expense_date", date)
        if seller_info.get("名称"):
            fields.setdefault("merchant_name", seller_info["名称"])

        amount = clean_amount(amount_info.get("价税合计(小写)") or amount_info.get("合计金额"))
        if amount is not None:
            fields.setdefault("reimbursement_amount", format_amount(amount))

        return fields
