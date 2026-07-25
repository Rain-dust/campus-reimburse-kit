#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""RM reimbursement constants and lightweight business helpers."""

from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import io
import os
import re


SYSTEM_NAME = "RM 队伍报账系统"
DEFAULT_TEAM_NAME = "未命名 RoboMaster 队伍"

SUBMITTER_GROUPS = [
    "机械组",
    "电控组",
    "视觉组",
    "算法组",
    "运营组",
    "宣传组",
    "管理组",
    "其他",
]

EXPENSE_CATEGORIES = [
    "材料费",
    "加工费",
    "电子元器件",
    "机械零件",
    "工具耗材",
    "交通费",
    "住宿费",
    "餐饮费",
    "宣传物料",
    "赛事报名",
    "物流快递",
    "其他",
]

REIMBURSEMENT_STATUSES = [
    "draft",
    "submitted",
    "reviewing",
    "approved",
    "paid",
    "rejected",
]

STATUS_LABELS = {
    "draft": "草稿",
    "submitted": "已提交",
    "reviewing": "审核中",
    "approved": "已通过",
    "paid": "已打款",
    "rejected": "已驳回",
}

PAYMENT_METHODS = [
    "微信",
    "支付宝",
    "银行卡",
    "现金",
    "对公转账",
    "其他",
]

RM_EXPORT_HEADERS = [
    "报账编号",
    "提交时间",
    "提交人",
    "所属组别",
    "费用类别",
    "报账金额",
    "消费日期",
    "商家/收款方",
    "发票号/票据号",
    "支付方式",
    "项目/赛事/活动",
    "用途说明",
    "报账状态",
    "审核人",
    "审核时间",
    "驳回原因",
    "文件路径",
]

CATEGORY_KEYWORDS = {
    "机械零件": ["螺丝", "轴承", "同步带", "电机座", "联轴器", "法兰", "滑轨", "导轨"],
    "材料费": ["铝型材", "铝材", "碳板", "钢材", "亚克力", "板材", "螺母"],
    "加工费": ["加工", "CNC", "线切割", "激光切割", "车床", "铣床", "阳极氧化"],
    "电子元器件": ["STM32", "PCB", "电容", "电阻", "传感器", "舵机", "电调", "芯片", "开发板"],
    "宣传物料": ["打印", "海报", "横幅", "展板", "贴纸", "队服", "易拉宝"],
    "物流快递": ["顺丰", "圆通", "中通", "快递", "运费", "京东物流", "韵达"],
    "交通费": ["高铁", "火车", "滴滴", "出租车", "公交", "地铁", "机票", "打车"],
    "住宿费": ["酒店", "宾馆", "住宿", "民宿"],
    "餐饮费": ["餐饮", "饭店", "外卖", "食堂", "美团", "饿了么"],
    "赛事报名": ["报名费", "参赛费", "赛事", "认证"],
    "工具耗材": ["工具", "耗材", "胶水", "扎带", "热缩管", "砂纸", "钻头"],
}


def classify_expense(text: str | None, merchant_name: str | None = None) -> str:
    """Classify an RM reimbursement with simple keyword rules."""
    haystack = f"{text or ''} {merchant_name or ''}".lower()
    best_category = "其他"
    best_score = 0

    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for keyword in keywords if keyword.lower() in haystack)
        if score > best_score:
            best_category = category
            best_score = score

    return best_category if best_score else "其他"


def clean_amount(value: str | int | float | Decimal | None) -> Decimal | None:
    """Normalize user/OCR amount text into Decimal, preserving empty values."""
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    cleaned = re.sub(r"[^\d.\-]", "", str(value))
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def format_amount(value: Decimal | str | int | float | None) -> str:
    """Format an amount for forms and CSV export."""
    amount = clean_amount(value)
    return "" if amount is None else f"{amount:.2f}"


ALLOWED_STATUS_TRANSITIONS = {
    "draft": {"submitted"},
    "submitted": {"reviewing", "approved", "rejected"},
    "reviewing": {"approved", "rejected"},
    "approved": {"paid"},
    "paid": set(),
    "rejected": {"submitted"},
}


def can_transition_status(current_status: str | None, target_status: str | None) -> bool:
    """Return whether a reimbursement status transition is allowed."""
    current = current_status or "draft"
    target = target_status or ""
    return target in ALLOWED_STATUS_TRANSITIONS.get(current, set())


def transition_reimbursement_status(
    record,
    target_status: str,
    reviewer_name: str | None = None,
    reject_reason: str | None = None,
    now: datetime | None = None,
):
    """Apply a validated reimbursement status transition to a record-like object."""
    current_status = getattr(record, "reimbursement_status", None) or "draft"
    if not can_transition_status(current_status, target_status):
        raise ValueError(f"Invalid reimbursement status transition: {current_status} -> {target_status}")

    cleaned_reason = (reject_reason or "").strip()
    if target_status == "rejected" and not cleaned_reason:
        raise ValueError("Reject reason is required when rejecting a reimbursement")

    record.reimbursement_status = target_status
    if reviewer_name:
        record.reviewer_name = reviewer_name
    if target_status in {"reviewing", "approved", "rejected", "paid"}:
        record.reviewed_at = now or datetime.now()
    if target_status == "approved":
        record.reject_reason = ""
    elif target_status == "rejected":
        record.reject_reason = cleaned_reason
    elif target_status == "submitted":
        record.reject_reason = ""

    return record


def record_amount(record) -> Decimal:
    """Read a reimbursement amount from a record-like object, falling back safely."""
    amount = clean_amount(getattr(record, "reimbursement_amount", None))
    if amount is not None:
        return amount
    amount = clean_amount(getattr(record, "amount_in_figures", None))
    if amount is not None:
        return amount
    amount = clean_amount(getattr(record, "total_amount", None))
    return amount or Decimal("0")


def record_expense_date(record):
    return getattr(record, "expense_date", None) or getattr(record, "invoice_date", None)


def record_merchant_name(record) -> str:
    return getattr(record, "merchant_name", None) or getattr(record, "seller_name", None) or ""


def format_date(value) -> str:
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    return str(value) if value else ""


def format_datetime(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return str(value) if value else ""


def build_reimbursement_export_row(record) -> list[str]:
    """Build one finance CSV row with the agreed Phase 1/3 field order."""
    status = getattr(record, "reimbursement_status", "") or ""
    return [
        getattr(record, "reimbursement_no", "") or "",
        format_datetime(getattr(record, "created_at", None)),
        getattr(record, "submitter_name", "") or "",
        getattr(record, "submitter_group", "") or "",
        getattr(record, "expense_category", "") or "",
        format_amount(record_amount(record)),
        format_date(record_expense_date(record)),
        record_merchant_name(record),
        getattr(record, "invoice_number", "") or "",
        getattr(record, "payment_method", "") or "",
        getattr(record, "project_name", "") or "",
        getattr(record, "description", None) or getattr(record, "remarks", None) or "",
        STATUS_LABELS.get(status, status),
        getattr(record, "reviewer_name", None) or getattr(record, "reviewer", None) or "",
        format_datetime(getattr(record, "reviewed_at", None)),
        getattr(record, "reject_reason", "") or "",
        getattr(record, "file_path", None) or getattr(record, "image_path", None) or "",
    ]


def build_reimbursement_csv(records) -> bytes:
    """Build an Excel-friendly UTF-8-SIG CSV for reimbursement records."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(RM_EXPORT_HEADERS)
    for record in records:
        writer.writerow(build_reimbursement_export_row(record))
    return output.getvalue().encode("utf-8-sig")


def build_reimbursement_statistics(records) -> dict:
    """Aggregate finance-facing reimbursement metrics."""
    stats = {
        "total_count": 0,
        "total_amount": Decimal("0"),
        "approved_amount": Decimal("0"),
        "paid_amount": Decimal("0"),
        "pending_amount": Decimal("0"),
        "rejected_amount": Decimal("0"),
        "group_totals": {},
        "category_totals": {},
        "status_counts": {},
    }

    for record in records:
        amount = record_amount(record)
        status = getattr(record, "reimbursement_status", None) or "submitted"
        group = getattr(record, "submitter_group", None) or "未填写"
        category = getattr(record, "expense_category", None) or "其他"

        stats["total_count"] += 1
        stats["total_amount"] += amount
        stats["status_counts"][status] = stats["status_counts"].get(status, 0) + 1
        stats["group_totals"][group] = stats["group_totals"].get(group, Decimal("0")) + amount
        stats["category_totals"][category] = stats["category_totals"].get(category, Decimal("0")) + amount

        if status == "approved":
            stats["approved_amount"] += amount
        elif status == "paid":
            stats["paid_amount"] += amount
        elif status in {"submitted", "reviewing"}:
            stats["pending_amount"] += amount
        elif status == "rejected":
            stats["rejected_amount"] += amount

    return stats


def _parse_filter_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def filter_reimbursement_records(records, filters: dict | None) -> list:
    """Filter record-like objects using the same simple criteria as /records."""
    filters = filters or {}
    status = (filters.get("status") or "").strip()
    group = (filters.get("submitter_group") or "").strip()
    category = (filters.get("expense_category") or "").strip()
    keyword = (filters.get("keyword") or "").strip().lower()
    date_from = _parse_filter_date(filters.get("date_from"))
    date_to = _parse_filter_date(filters.get("date_to"))

    matched = []
    for record in records:
        if status and getattr(record, "reimbursement_status", None) != status:
            continue
        if group and getattr(record, "submitter_group", None) != group:
            continue
        if category and getattr(record, "expense_category", None) != category:
            continue

        expense_date = record_expense_date(record)
        if (date_from or date_to) and not expense_date:
            continue
        if date_from and expense_date and expense_date < date_from:
            continue
        if date_to and expense_date and expense_date > date_to:
            continue

        if keyword:
            haystack = " ".join(
                str(value or "")
                for value in [
                    getattr(record, "submitter_name", ""),
                    record_merchant_name(record),
                    getattr(record, "description", ""),
                    getattr(record, "remarks", ""),
                    getattr(record, "invoice_number", ""),
                ]
            ).lower()
            if keyword not in haystack:
                continue

        matched.append(record)

    return matched


def is_safe_upload_filename(filename: str | None) -> bool:
    """Allow only one safe uploaded filename, never a path."""
    if not filename:
        return False
    if os.path.isabs(filename):
        return False
    normalized = filename.replace("\\", "/")
    if "/" in normalized:
        return False
    if normalized in {".", ".."} or ".." in normalized.split("/"):
        return False
    if os.path.basename(normalized) != normalized:
        return False
    return True


def receipt_file_kind(filename: str | None) -> str:
    """Classify receipt files for preview rendering."""
    if not is_safe_upload_filename(filename):
        return "missing"
    extension = os.path.splitext(filename)[1].lower()
    if extension in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
        return "image"
    if extension == ".pdf":
        return "pdf"
    return "download"
