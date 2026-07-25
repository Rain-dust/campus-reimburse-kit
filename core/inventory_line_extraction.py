"""Extract reviewable inventory details from locally positioned receipt text."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Iterable


_UNITS = {"件", "个", "条", "套", "台", "只", "盒", "包", "张", "米"}
_COMBINED_QUANTITY_UNIT = re.compile(r"^(\d+(?:\.\d+)?)(件|个|条|套|台|只|盒|包|张|米)$")
_NUMBER = re.compile(r"^[-+]?\d+(?:\.\d+)?$")
_QUANTITY = re.compile(r"^\d+(?:\.\d{1,3})?$")
_MODEL = re.compile(r"\b([A-Za-z]{1,10}-?\d{1,8}[A-Za-z0-9-]*)\b")


@dataclass(frozen=True)
class TextBox:
    """One recognized text fragment and its top-left reading position."""

    text: str
    x: float
    y: float


@dataclass(frozen=True)
class RecognizedLineItem:
    """A locally extracted row that still requires a total-based confidence check."""

    name: str = ""
    specification: str = ""
    unit: str = ""
    quantity: str = ""
    unit_price_cents: int | None = None
    amount_cents: int | None = None
    confidence: str = "pending"


def extract_line_items_from_boxes(
    boxes: Iterable[TextBox], expected_total_cents: int | None
) -> list[RecognizedLineItem]:
    """Return item candidates, verified only when they reconcile to the receipt total."""
    positioned_boxes = _coalesce_character_boxes(
        [box for box in boxes if box.text.strip()]
    )
    candidates = [
        candidate
        for row in _group_rows(positioned_boxes)
        if (candidate := _parse_row(sorted(row, key=lambda box: box.x))) is not None
    ]
    reconciled = _reconcile(candidates, expected_total_cents)
    if any(candidate.confidence == "verified" for candidate in reconciled):
        return reconciled
    return _extract_wrapped_single_item(positioned_boxes, expected_total_cents) or reconciled


def _coalesce_character_boxes(
    boxes: list[TextBox], y_tolerance: float = 0.2, max_x_step: float = 20.5
) -> list[TextBox]:
    """Join adjacent one-character PDF glyphs without crossing visible columns."""
    rows: list[list[TextBox]] = []
    anchors: list[float] = []
    for box in sorted(boxes, key=lambda item: (item.y, item.x)):
        for index, anchor in enumerate(anchors):
            if abs(box.y - anchor) <= y_tolerance:
                rows[index].append(box)
                break
        else:
            anchors.append(box.y)
            rows.append([box])

    result: list[TextBox] = []
    for row in rows:
        ordered = sorted(row, key=lambda item: item.x)
        index = 0
        while index < len(ordered):
            current = ordered[index]
            token = current.text.strip()
            if len(token) != 1:
                result.append(current)
                index += 1
                continue

            fragments = [token]
            previous = current
            cursor = index + 1
            while cursor < len(ordered):
                candidate = ordered[cursor]
                candidate_token = candidate.text.strip()
                if (
                    len(candidate_token) != 1
                    or candidate.x - previous.x > max_x_step
                ):
                    break
                fragments.append(candidate_token)
                previous = candidate
                cursor += 1
            result.append(TextBox("".join(fragments), current.x, current.y))
            index = cursor
    return result


def _group_rows(boxes: Iterable[TextBox], tolerance: float = 8.0) -> list[list[TextBox]]:
    rows: list[list[TextBox]] = []
    anchors: list[float] = []
    for box in sorted((box for box in boxes if box.text.strip()), key=lambda box: -box.y):
        for index, anchor in enumerate(anchors):
            if abs(box.y - anchor) <= tolerance:
                rows[index].append(box)
                break
        else:
            anchors.append(box.y)
            rows.append([box])
    return rows


def _parse_row(row: list[TextBox]) -> RecognizedLineItem | None:
    tokens = _expand_tokens([_clean_token(box.text) for box in row])
    try:
        unit_index = next(index for index, token in enumerate(tokens) if token in _UNITS)
    except StopIteration:
        return None

    leading = [token for token in tokens[:unit_index] if token]
    if not leading:
        return None
    if len(leading) == 1:
        name = _display_name(leading[0])
        specification = _model_from_text(name)
    else:
        name = _display_name(" ".join(leading[:-1]))
        specification = _normalize_specification(leading[-1])

    numeric = [
        (token, _to_cents(token))
        for token in tokens[unit_index + 1:]
        if _to_cents(token) is not None
    ]
    quantity = numeric[0][0] if numeric and _NUMBER.fullmatch(numeric[0][0]) else ""
    money_values = [value for _, value in numeric[1:]] if quantity else []
    unit_price_cents = money_values[0] if len(money_values) >= 2 else None
    amount_cents = money_values[-1] if money_values else None

    return RecognizedLineItem(
        name=name,
        specification=specification,
        unit=tokens[unit_index],
        quantity=quantity,
        unit_price_cents=unit_price_cents,
        amount_cents=amount_cents,
    )


def _expand_tokens(tokens: list[str]) -> list[str]:
    expanded: list[str] = []
    for token in tokens:
        combined = _COMBINED_QUANTITY_UNIT.fullmatch(token)
        if combined:
            expanded.extend(combined.groups())
        elif token:
            expanded.append(token)
    return expanded


def _clean_token(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _structure_token(value: str) -> str:
    """Normalize structural labels without changing item description spacing."""
    return re.sub(r"\s+", "", value or "")


def _display_name(value: str) -> str:
    parts = [part.strip() for part in value.split("*") if part.strip()]
    if value.strip().startswith("*") and len(parts) >= 2:
        return " ".join(parts[1:])
    return parts[-1] if len(parts) >= 2 else value.strip()


def _normalize_specification(value: str) -> str:
    cleaned = _clean_token(value)
    pieces = cleaned.split()
    if (
        len(pieces) > 1
        and all(len(piece) == 1 and piece.isascii() and piece.isalnum() for piece in pieces)
        and any(piece.isdigit() for piece in pieces)
    ):
        return "".join(pieces)
    return cleaned


def _model_from_text(value: str) -> str:
    match = _MODEL.search(value)
    return match.group(1) if match else ""


def _to_cents(value: str) -> int | None:
    normalized = value.replace(",", "").replace("￥", "").replace("¥", "").strip()
    if not _NUMBER.fullmatch(normalized):
        return None
    try:
        amount = Decimal(normalized).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None
    return int(amount * 100)


def _reconcile(
    candidates: list[RecognizedLineItem], expected_total_cents: int | None
) -> list[RecognizedLineItem]:
    if expected_total_cents is None or expected_total_cents <= 0 or not candidates:
        return candidates

    total_cents = 0
    for candidate in candidates:
        if not candidate.quantity or candidate.unit_price_cents is None or candidate.amount_cents is None:
            return candidates
        quantity = Decimal(candidate.quantity)
        calculated_cents = int(
            (quantity * Decimal(candidate.unit_price_cents)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        if abs(calculated_cents - candidate.amount_cents) > 1:
            return candidates
        total_cents += candidate.amount_cents

    if abs(total_cents - expected_total_cents) > 1:
        return candidates
    return [replace(candidate, confidence="verified") for candidate in candidates]


def _extract_wrapped_single_item(
    boxes: list[TextBox], expected_total_cents: int | None
) -> list[RecognizedLineItem]:
    """Reconcile one wrapped, discounted invoice item by its column positions.

    Some electronic invoices wrap a product name onto a second visual row and
    place a discount on another row.  Their tax-exclusive columns cannot be
    copied into an inventory form, but a sole item's tax-inclusive unit price
    is safe to calculate when the quantity and receipt total reconcile exactly.
    """
    if expected_total_cents is None or expected_total_cents <= 0:
        return []

    header_names = {"项目名称", "规格型号", "单位", "数量", "单价", "金额"}
    headers = {
        _structure_token(box.text): box
        for box in boxes
        if _structure_token(box.text) in header_names
    }
    required_headers = {"项目名称", "单位", "数量", "单价", "金额"}
    if not required_headers.issubset(headers):
        return []

    name_header = headers["项目名称"]
    unit_header = headers["单位"]
    quantity_header = headers["数量"]
    price_header = headers["单价"]
    amount_header = headers["金额"]
    if not (name_header.x < unit_header.x < quantity_header.x < price_header.x < amount_header.x):
        return []

    lower_bound = max(header.y for header in headers.values()) + 8
    totals = [box.y for box in boxes if "合计" in _structure_token(box.text) and box.y > lower_bound]
    upper_bound = min(totals) if totals else lower_bound + 240
    body = [box for box in boxes if lower_bound <= box.y < upper_bound]

    unit_boxes = [
        box
        for box in body
        if unit_header.x - 20 <= box.x < quantity_header.x - 20
        and _clean_token(box.text) in _UNITS
    ]
    quantity_boxes = [
        box
        for box in body
        if quantity_header.x - 20 <= box.x < price_header.x - 20
        and _is_positive_quantity(_clean_token(box.text))
    ]
    units = {_clean_token(box.text) for box in unit_boxes}
    quantities = {_clean_token(box.text) for box in quantity_boxes}
    specification_header = headers.get("规格型号")
    name_max_x = (specification_header.x if specification_header else unit_header.x) - 20
    names = _wrapped_item_names(body, name_max_x)
    if len(units) != 1 or len(quantities) != 1 or len(names) != 1:
        return []

    quantity = next(iter(quantities))
    quantity_decimal = Decimal(quantity)
    unit_price_cents = (Decimal(expected_total_cents) / quantity_decimal).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    if unit_price_cents <= 0:
        return []
    unit_price = int(unit_price_cents)
    if abs(quantity_decimal * Decimal(unit_price) - Decimal(expected_total_cents)) > 1:
        return []

    name = next(iter(names))
    specification = ""
    if specification_header is not None:
        item_y = min(box.y for box in unit_boxes + quantity_boxes)
        specification = _wrapped_specification(
            body,
            specification_header.x - 20,
            unit_header.x - 20,
            item_y,
        )
    return [
        RecognizedLineItem(
            name=name,
            specification=specification or _model_from_text(name),
            unit=next(iter(units)),
            quantity=quantity,
            unit_price_cents=unit_price,
            amount_cents=expected_total_cents,
            confidence="verified",
        )
    ]


def _wrapped_item_names(boxes: list[TextBox], max_x: float) -> set[str]:
    starts = sorted(
        (box for box in boxes if box.x < max_x and "*" in _clean_token(box.text)),
        key=lambda box: box.y,
    )
    names: set[str] = set()
    for index, start in enumerate(starts):
        next_start_y = starts[index + 1].y if index + 1 < len(starts) else start.y + 80
        fragments = []
        for box in boxes:
            token = _clean_token(box.text)
            if (
                box.x < max_x
                and start.y <= box.y < min(start.y + 80, next_start_y)
                and token
                and not _NUMBER.fullmatch(token)
            ):
                fragments.append(token)
        name = _display_name(_join_wrapped_fragments(fragments))
        name = re.sub(r"RoboMast\s*er", "RoboMaster", name, flags=re.IGNORECASE)
        name = re.sub(r"(?<=[a-z])(?=[A-Z]\d)", " ", name)
        name = re.sub(r"(?<=\d)(?=[\u4e00-\u9fff])", " ", name)
        name = re.sub(r"\s+", " ", name).strip()
        name = _deduplicate_adjacent_words(name)
        if name:
            names.add(name)
    return names


def _wrapped_specification(
    boxes: list[TextBox], left: float, right: float, item_y: float
) -> str:
    fragments: list[str] = []
    for box in sorted(boxes, key=lambda item: (item.y, item.x)):
        token = _clean_token(box.text)
        if (
            left <= box.x < right
            and item_y - 2 <= box.y < item_y + 80
            and token
            and not _NUMBER.fullmatch(token)
            and (not fragments or fragments[-1] != token)
        ):
            fragments.append(token)
    return _normalize_specification(_join_wrapped_fragments(fragments))


def _join_wrapped_fragments(fragments: list[str]) -> str:
    result = ""
    for fragment in fragments:
        if not result:
            result = fragment
        elif _is_cjk(result[-1]) and _is_cjk(fragment[0]):
            result += fragment
        elif result[-1].isalnum() and fragment[0].isalpha() and fragment[0].isascii():
            result += fragment
        elif result[-1].isascii() and _is_cjk(fragment[0]):
            result += fragment
        else:
            result += " " + fragment
    return result


def _is_cjk(character: str) -> bool:
    return "\u4e00" <= character <= "\u9fff"


def _deduplicate_adjacent_words(value: str) -> str:
    words = value.split()
    result: list[str] = []
    for word in words:
        if not result or word != result[-1]:
            result.append(word)
    return " ".join(result)


def _is_positive_quantity(value: str) -> bool:
    if not _QUANTITY.fullmatch(value):
        return False
    try:
        return Decimal(value) > 0
    except InvalidOperation:
        return False
