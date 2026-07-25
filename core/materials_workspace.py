"""Persistent local workspaces for reimbursement-material preparation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
import re
import shutil
import tempfile
from typing import Any, Mapping, MutableMapping
from uuid import NAMESPACE_URL, uuid4, uuid5
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from core.materials_assistant import (
    InventoryLine,
    QuotaSlot,
    Receipt,
    find_inventory_templates,
    inspect_inventory_template,
    parse_amount_to_cents,
    render_inventory_documents,
    validate_inventory_lines,
)
from core.inventory_line_extraction import RecognizedLineItem


SCHEMA_VERSION = 1
WORKSPACE_FILENAME = "workspace.json"
MAX_RESTORE_ENTRIES = 1000
MAX_RESTORE_MEMBER_BYTES = 100 * 1024 * 1024
MAX_RESTORE_TOTAL_BYTES = 250 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass(frozen=True)
class TemplateValidation:
    """Result of checking the paired official inventory workbook templates."""

    inbound_path: Path | None
    outbound_path: Path | None
    errors: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.errors and self.inbound_path is not None and self.outbound_path is not None


@dataclass(frozen=True)
class ExportResult:
    """A completed quota export package."""

    output_dir: Path
    slot_id: str
    total_cents: int


def validate_template_directory(template_dir: str | Path) -> TemplateValidation:
    """Require one usable inbound template and one usable outbound template."""
    directory = Path(template_dir).resolve()
    errors: list[str] = []
    if not directory.is_dir():
        return TemplateValidation(None, None, (f"模板目录不存在或不是文件夹：{directory}",))

    inbound_path = _validate_template_kind(directory, "入库单", errors)
    outbound_path = _validate_template_kind(directory, "出库单", errors)
    if inbound_path is not None and inbound_path == outbound_path:
        errors.append("入库单和出库单模板不能是同一文件")
    return TemplateValidation(inbound_path, outbound_path, tuple(errors))


def package_preflight(state: Mapping[str, Any], slot_id: str) -> list[str]:
    """Return every export-blocking issue for one quota package without raising."""
    source = state if isinstance(state, Mapping) else {}
    errors: list[str] = []
    validation = validate_template_directory(source.get("template_dir", ""))
    if not validation.valid:
        errors.extend(f"模板无效：{error}" for error in validation.errors)

    quota = _find_quota_record(source.get("quotas"), slot_id)
    if quota is None:
        return errors + [f"额度 {slot_id} 不存在"]

    capacity_cents = _record_value(quota, "capacity_cents")
    try:
        capacity_cents = _parse_cents(capacity_cents)
    except ValueError:
        errors.append(f"额度 {slot_id} 上限必须是有限整数分")
        capacity_cents = None

    receipt_ids = _receipt_ids_for_quota(quota)
    if not receipt_ids:
        errors.append(f"额度 {slot_id} 无分配票据")
        return errors
    for receipt_id in sorted({receipt_id for receipt_id in receipt_ids if receipt_ids.count(receipt_id) > 1}):
        errors.append(f"额度 {slot_id} 重复分配票据 {receipt_id}")
    for receipt_id in _receipt_ids_reused_by_other_quotas(source.get("quotas"), slot_id, receipt_ids):
        errors.append(f"票据 {receipt_id} 被多个额度重复分配")

    receipt_records = _records_by_id(source.get("receipts"), "receipt_id")
    receipt_total_cents = 0
    receipts_have_valid_totals = True
    for receipt_id in receipt_ids:
        record = receipt_records.get(receipt_id)
        if record is None:
            errors.append(f"额度 {slot_id} 分配的票据 {receipt_id} 不存在")
            receipts_have_valid_totals = False
            continue
        total_cents, amount_error = _receipt_total_cents(record)
        _validate_receipt_record(record, receipt_id, total_cents, amount_error, errors)
        if total_cents is None or total_cents <= 0:
            receipts_have_valid_totals = False
        else:
            receipt_total_cents += total_cents

    if capacity_cents is not None and receipt_total_cents > capacity_cents:
        errors.append(f"额度 {slot_id} 的票据合计超过额度")

    raw_lines = _lines_for_slot(source.get("lines_by_slot"), slot_id)
    if not raw_lines:
        errors.append(f"额度 {slot_id} 无物品明细")
        return errors

    lines, line_errors = _inventory_lines_from_state(raw_lines)
    errors.extend(line_errors)
    if line_errors:
        return errors
    try:
        validate_inventory_lines(lines)
    except ValueError as exc:
        errors.append(f"物品明细校验失败：{exc}")
        return errors

    if receipts_have_valid_totals:
        try:
            validate_inventory_lines(lines, expected_total_cents=receipt_total_cents)
        except ValueError as exc:
            errors.append(f"物品明细合计与票据价税合计不一致：{exc}")
    return errors


def export_quota_package(
    workspace_root: str | Path,
    state: dict[str, Any],
    slot_id: str,
) -> ExportResult:
    """Export one validated quota as inventory workbooks plus source receipts."""
    errors = package_preflight(state, slot_id)
    if errors:
        raise ValueError("导出前检查失败：\n" + "\n".join(errors))

    quota = _find_quota_record(state.get("quotas"), slot_id)
    if quota is None:
        raise ValueError(f"额度 {slot_id} 不存在")
    receipt_records = _records_by_id(state.get("receipts"), "receipt_id")
    receipt_ids = _receipt_ids_for_quota(quota)
    receipts = [receipt_records[receipt_id] for receipt_id in receipt_ids]
    total_cents = sum(_receipt_total_cents(receipt)[0] or 0 for receipt in receipts)
    lines, line_errors = _inventory_lines_from_state(_lines_for_slot(state.get("lines_by_slot"), slot_id))
    if line_errors:
        raise ValueError("导出前检查失败：\n" + "\n".join(line_errors))

    workspace_dir = Path(workspace_root).resolve()
    imports_dir = (workspace_dir / "imports").resolve()
    for receipt in receipts:
        source_path = Path(str(_record_value(receipt, "source_path"))).resolve()
        try:
            source_path.relative_to(imports_dir)
        except ValueError as exc:
            raise ValueError("票据源文件必须位于工作区 imports 目录") from exc
    exports_dir = workspace_dir / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    label = str(_record_value(quota, "label", "")).strip() or slot_id
    base_name = f"{_safe_workspace_name(label)}-{Decimal(total_cents) / Decimal(100):.2f}"
    output_dir = exports_dir / base_name
    index = 2
    while output_dir.exists():
        output_dir = exports_dir / f"{base_name}-{index:02d}"
        index += 1

    staging_dir = Path(tempfile.mkdtemp(prefix=".staging-", dir=exports_dir))
    published = False
    try:
        render_inventory_documents(state["template_dir"], staging_dir, lines, expected_total_cents=total_cents)
        originals_dir = staging_dir / "原始票据"
        originals_dir.mkdir()
        receipt_indexes: dict[date, int] = {}
        for receipt in receipts:
            invoice_date = _parse_date(_record_value(receipt, "invoice_date"))
            receipt_total_cents, _ = _receipt_total_cents(receipt)
            if invoice_date is None or receipt_total_cents is None:
                raise ValueError("导出前检查失败：票据信息不完整")
            receipt_indexes[invoice_date] = receipt_indexes.get(invoice_date, 0) + 1
            source_path = Path(str(_record_value(receipt, "source_path")))
            copied_name = (
                f"{invoice_date:%y%m%d}_{receipt_indexes[invoice_date]:02d}_"
                f"{Decimal(receipt_total_cents) / Decimal(100):.2f}{source_path.suffix}"
            )
            shutil.copy2(source_path, originals_dir / copied_name)

        pending_state = deepcopy(state)
        pending_exports = pending_state.get("exports")
        if not isinstance(pending_exports, list):
            pending_exports = []
            pending_state["exports"] = pending_exports
        pending_exports.append({
            "path": (Path("exports") / output_dir.name).as_posix(),
            "slot_id": slot_id,
            "total_cents": total_cents,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        })
        staging_dir.rename(output_dir)
        published = True
        save_workspace(workspace_dir, pending_state)
    except Exception:
        shutil.rmtree(output_dir if published else staging_dir, ignore_errors=True)
        raise

    state["exports"] = pending_exports
    return ExportResult(output_dir, slot_id, total_cents)


def default_workspace_state(name: str = "", template_dir: str = "") -> dict[str, Any]:
    """Return the complete, JSON-compatible state for a new workspace."""
    return {
        "schema_version": SCHEMA_VERSION,
        "name": str(name),
        "template_dir": str(template_dir),
        "ocr_provider": "",
        "step": "",
        "receipts": [],
        "quotas": [],
        "lines_by_slot": {},
        "exports": [],
    }


def create_workspace(root: str | Path, name: str, template_dir: str = "") -> tuple[Path, dict[str, Any]]:
    """Create a uniquely named workspace below ``root/workspaces``."""
    root_path = Path(root).resolve()
    workspaces_dir = root_path / "workspaces"
    workspaces_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_workspace_name(name)
    workspace_dir = workspaces_dir / f"{safe_name}-{uuid4().hex}"
    workspace_dir.mkdir()
    for directory_name in ("imports", "records", "exports", "backups"):
        (workspace_dir / directory_name).mkdir()
    state = default_workspace_state(name, template_dir)
    save_workspace(workspace_dir, state)
    return workspace_dir, state


def export_workspace_backup(workspace_root: str | Path) -> Path:
    """Create a compressed snapshot that always satisfies restore limits."""
    workspace_dir = Path(workspace_root).resolve()
    if not (workspace_dir / WORKSPACE_FILENAME).is_file():
        raise ValueError(f"Workspace file is missing: {workspace_dir / WORKSPACE_FILENAME}")

    backups_dir = workspace_dir / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    archive = backups_dir / f"workspace-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}.zip"
    source_files = [
        path
        for path in workspace_dir.rglob("*")
        if path.is_file() and not path.is_relative_to(backups_dir)
    ]
    _validate_backup_source_files(source_files)
    try:
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as backup:
            for path in source_files:
                backup.write(path, path.relative_to(workspace_dir).as_posix())
        with ZipFile(archive) as backup:
            _validate_backup_members(backup.infolist())
    except Exception:
        archive.unlink(missing_ok=True)
        raise
    return archive


def restore_workspace_backup(home: str | Path, archive: str | Path) -> Path:
    """Safely restore a ZIP snapshot as a new workspace below ``home``."""
    home_dir = Path(home).resolve()
    workspaces_dir = home_dir / "workspaces"
    staging_dir = workspaces_dir / f".restore-{uuid4().hex}.staging"
    restored_dir = workspaces_dir / f"restored-{uuid4().hex}"
    restored = False
    try:
        with ZipFile(archive) as backup:
            members = backup.infolist()
            _validate_backup_members(members)
            if WORKSPACE_FILENAME not in {member.filename for member in members}:
                raise ValueError(f"Backup archive must contain {WORKSPACE_FILENAME} at its root")

            workspaces_dir.mkdir(parents=True, exist_ok=True)
            staging_dir.mkdir()
            copied_bytes = 0
            for member in members:
                relative_path = PurePosixPath(member.filename.replace("\\", "/"))
                destination = staging_dir.joinpath(*relative_path.parts)
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with backup.open(member) as source, destination.open("wb") as target:
                    copied_bytes = _copy_backup_member(source, target, member.filename, copied_bytes)
            restored_state = load_workspace(staging_dir)
            _rewrite_restored_import_paths(restored_state, staging_dir, restored_dir)
            save_workspace(staging_dir, restored_state)
            staging_dir.rename(restored_dir)
            restored = True
    except BadZipFile as exc:
        raise ValueError("Backup archive is not a valid ZIP file") from exc
    finally:
        if not restored:
            shutil.rmtree(staging_dir, ignore_errors=True)
            shutil.rmtree(restored_dir, ignore_errors=True)
    return restored_dir


def _validate_backup_members(members: list[Any]) -> None:
    if len(members) > MAX_RESTORE_ENTRIES:
        raise ValueError("Backup archive has too many entries")

    declared_total = 0
    for member in members:
        name = member.filename
        normalized_name = name.replace("\\", "/")
        posix_path = PurePosixPath(normalized_name)
        windows_path = PureWindowsPath(name)
        if (
            not name
            or posix_path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or any(part == ".." for part in posix_path.parts)
        ):
            raise ValueError(f"Backup archive contains unsafe path: {name}")
        if member.file_size > MAX_RESTORE_MEMBER_BYTES:
            raise ValueError(f"Backup archive member too large: {name}")
        declared_total += member.file_size
        if declared_total > MAX_RESTORE_TOTAL_BYTES:
            raise ValueError("Backup archive total size exceeds limit")


def _validate_backup_source_files(source_files: list[Path]) -> None:
    if len(source_files) > MAX_RESTORE_ENTRIES:
        raise ValueError("Workspace backup has too many entries to restore")

    total_size = 0
    for path in source_files:
        file_size = path.stat().st_size
        relative_name = path.name
        if file_size > MAX_RESTORE_MEMBER_BYTES:
            raise ValueError(
                f"Workspace backup member exceeds restore limit: {relative_name}"
            )
        total_size += file_size
        if total_size > MAX_RESTORE_TOTAL_BYTES:
            raise ValueError("Workspace backup total size exceeds restore limit")


def _copy_backup_member(source: Any, target: Any, member_name: str, copied_bytes: int) -> int:
    member_bytes = 0
    while chunk := source.read(64 * 1024):
        member_bytes += len(chunk)
        copied_bytes += len(chunk)
        if member_bytes > MAX_RESTORE_MEMBER_BYTES:
            raise ValueError(f"Backup archive member too large: {member_name}")
        if copied_bytes > MAX_RESTORE_TOTAL_BYTES:
            raise ValueError("Backup archive total size exceeds limit")
        target.write(chunk)
    return copied_bytes


def _rewrite_restored_import_paths(
    state: MutableMapping[str, Any], workspace_dir: Path, restored_dir: Path
) -> None:
    """Point restored receipts to their matching files inside the new imports directory."""
    imports_dir = (workspace_dir / "imports").resolve()
    for receipt in state.get("receipts", []):
        if not isinstance(receipt, MutableMapping):
            continue
        source_path = receipt.get("source_path")
        if not isinstance(source_path, str) or not source_path:
            raise ValueError("Backup receipt source path cannot be matched to restored imports")
        restored_source = _restored_import_path(imports_dir, source_path)
        if restored_source is None:
            raise ValueError(f"Backup receipt source path cannot be matched to restored imports: {source_path}")
        receipt["source_path"] = str(
            restored_dir / "imports" / restored_source.relative_to(imports_dir)
        )


def _restored_import_path(imports_dir: Path, source_path: str) -> Path | None:
    normalized_source = source_path.replace("\\", "/")
    source_parts = PurePosixPath(normalized_source).parts
    for index in range(len(source_parts) - 1, -1, -1):
        if source_parts[index].lower() != "imports":
            continue
        candidate = (imports_dir.joinpath(*source_parts[index + 1:])).resolve()
        try:
            candidate.relative_to(imports_dir)
        except ValueError:
            break
        if candidate.is_file():
            return candidate
        break

    filename = PureWindowsPath(source_path).name or PurePosixPath(normalized_source).name
    matches = [path for path in imports_dir.rglob(filename) if path.is_file()] if filename else []
    return matches[0].resolve() if len(matches) == 1 else None


def migrate_legacy_workspace(root: str | Path) -> Path | None:
    """Copy a pre-workspace-layout root state into its deterministic legacy workspace."""
    root_path = Path(root).resolve()
    legacy_state_path = root_path / WORKSPACE_FILENAME
    if not legacy_state_path.is_file():
        return None

    workspaces_dir = root_path / "workspaces"
    legacy_id = uuid5(NAMESPACE_URL, str(root_path)).hex
    workspace_dir = workspaces_dir / f"legacy-{legacy_id}"
    if workspace_dir.is_dir():
        return workspace_dir

    try:
        legacy_state = json.loads(legacy_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Unable to read legacy workspace file: {legacy_state_path}") from exc
    if not isinstance(legacy_state, Mapping):
        raise ValueError("Legacy workspace file must be an object")

    workspaces_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = workspaces_dir / f".legacy-{uuid4().hex}.staging"
    legacy_imports = root_path / "imports"
    try:
        staging_dir.mkdir()
        for directory_name in ("imports", "records", "exports", "backups"):
            legacy_directory = root_path / directory_name
            migrated_directory = staging_dir / directory_name
            if legacy_directory.is_dir():
                shutil.copytree(legacy_directory, migrated_directory)
            else:
                migrated_directory.mkdir()

        state = _normalized_state(legacy_state)
        _rewrite_legacy_import_paths(state, root_path, legacy_imports, workspace_dir / "imports")
        save_workspace(staging_dir, state)
        staging_dir.rename(workspace_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    return workspace_dir


def load_workspace(workspace_dir: str | Path) -> dict[str, Any]:
    """Load a workspace, returning a valid empty state for absent or bad JSON."""
    path = Path(workspace_dir) / WORKSPACE_FILENAME
    try:
        with path.open("r", encoding="utf-8") as handle:
            stored = json.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(f"Workspace file is missing: {path}") from exc
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Unable to read workspace file: {path}") from exc
    _validate_loaded_workspace(stored)
    return _normalized_state(stored)


def save_workspace(workspace_dir: str | Path, state: Mapping[str, Any]) -> None:
    """Atomically write workspace state without touching receipt source files."""
    directory = Path(workspace_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / WORKSPACE_FILENAME
    normalized = _normalized_state(state)
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, prefix=".workspace-", suffix=".tmp", delete=False
        ) as handle:
            temporary_name = handle.name
            json.dump(normalized, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_name, target)
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass


def workspace_receipts(state: Mapping[str, Any]) -> list[Receipt]:
    """Restore valid receipt records from a workspace state."""
    receipts: list[Receipt] = []
    for record in state.get("receipts", []):
        if isinstance(record, Receipt):
            receipts.append(record)
            continue
        if not isinstance(record, Mapping):
            continue
        try:
            receipts.append(
                Receipt(
                    receipt_id=str(record["receipt_id"]),
                    source_path=str(record.get("source_path", "")),
                    invoice_date=_parse_date(record.get("invoice_date")),
                    total_cents=_optional_int(record.get("total_cents")),
                    vendor_name=str(record.get("vendor_name", "")),
                    invoice_number=str(record.get("invoice_number", "")),
                    ocr_text=str(record.get("ocr_text", "")),
                    is_material=_parse_bool(record.get("is_material", True)),
                    extraction_note=str(record.get("extraction_note", "")),
                    line_items=_recognized_line_items(record.get("line_items")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return receipts


def workspace_quotas(state: Mapping[str, Any]) -> list[QuotaSlot]:
    """Restore valid quota records from a workspace state."""
    quotas: list[QuotaSlot] = []
    for record in state.get("quotas", []):
        if isinstance(record, QuotaSlot):
            quotas.append(record)
            continue
        if not isinstance(record, Mapping):
            continue
        try:
            quotas.append(
                QuotaSlot(
                    slot_id=str(record["slot_id"]),
                    capacity_cents=int(record["capacity_cents"]),
                    label=str(record.get("label", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return quotas


def assign_receipt_to_slot(state: dict[str, Any], receipt_id: str, slot_id: str | None) -> None:
    """Move one receipt to a quota slot, or remove it from every slot."""
    quotas = state.get("quotas")
    if not isinstance(quotas, list):
        raise ValueError("额度列表无效")

    target = None
    if slot_id is not None:
        target = _find_quota_record(quotas, slot_id)
        if not isinstance(target, MutableMapping):
            raise ValueError(f"额度 {slot_id} 不存在")

        receipts = _records_by_id(state.get("receipts"), "receipt_id")
        receipt = receipts.get(receipt_id)
        if receipt is None:
            raise ValueError(f"票据 {receipt_id} 不存在")
        if not _parse_bool(_record_value(receipt, "confirmed")):
            raise ValueError(f"票据 {receipt_id} 尚未人工确认")
        if not _parse_bool(_record_value(receipt, "is_material", True)):
            raise ValueError(f"票据 {receipt_id} 不是材料票据")
        total_cents, amount_error = _receipt_total_cents(receipt)
        if amount_error or total_cents is None or total_cents <= 0:
            raise ValueError(f"票据 {receipt_id} 金额无效")

        assigned_ids = _receipt_ids_for_quota(target)
        if len(assigned_ids) != len(set(assigned_ids)):
            raise ValueError(f"额度 {slot_id} 存在重复票据")
        assigned_total = 0
        for assigned_id in assigned_ids:
            if assigned_id == receipt_id:
                continue
            assigned_receipt = receipts.get(assigned_id)
            assigned_cents, assigned_error = _receipt_total_cents(assigned_receipt)
            if assigned_receipt is None or assigned_error or assigned_cents is None or assigned_cents <= 0:
                raise ValueError(f"额度 {slot_id} 已分配票据金额无效")
            assigned_total += assigned_cents
        try:
            capacity_cents = _parse_cents(_record_value(target, "capacity_cents"))
        except ValueError as exc:
            raise ValueError(f"额度 {slot_id} 上限无效") from exc
        if assigned_total + total_cents > capacity_cents:
            raise ValueError(f"票据合计超过额度 {slot_id}")

    for quota in quotas:
        if isinstance(quota, MutableMapping):
            quota["receipt_ids"] = [
                assigned_id for assigned_id in _receipt_ids_for_quota(quota)
                if assigned_id != receipt_id
            ]
    if target is not None:
        target["receipt_ids"].append(receipt_id)


def _normalized_state(state: Any) -> dict[str, Any]:
    source = state if isinstance(state, Mapping) else {}
    defaults = default_workspace_state(
        _string(source.get("name")), _string(source.get("template_dir"))
    )
    defaults["step"] = _string(source.get("step"))
    defaults["ocr_provider"] = _string(source.get("ocr_provider"))
    defaults["receipts"] = _normalized_records(source.get("receipts"), Receipt, _receipt_record)
    defaults["quotas"] = _normalized_records(source.get("quotas"), QuotaSlot, _quota_record)
    if isinstance(source.get("lines_by_slot"), Mapping):
        defaults["lines_by_slot"] = dict(source["lines_by_slot"])
    if isinstance(source.get("exports"), list):
        defaults["exports"] = source["exports"]
    return defaults


def _rewrite_legacy_import_paths(
    state: MutableMapping[str, Any], root_path: Path, legacy_imports: Path, migrated_imports: Path
) -> None:
    """Point legacy receipt records at their copied files when their source was under root/imports."""
    legacy_imports = legacy_imports.resolve()
    for receipt in state.get("receipts", []):
        if not isinstance(receipt, MutableMapping):
            continue
        source_value = receipt.get("source_path")
        if not isinstance(source_value, str) or not source_value:
            continue
        source_path = Path(source_value)
        candidate = source_path if source_path.is_absolute() else root_path / source_path
        try:
            relative_path = candidate.resolve().relative_to(legacy_imports)
        except ValueError:
            continue
        receipt["source_path"] = str(migrated_imports / relative_path)


def _validate_loaded_workspace(state: Any) -> None:
    if not isinstance(state, Mapping):
        raise ValueError("工作区文件必须是对象")
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"工作区版本不兼容：{state.get('schema_version')!r}")
    expected_types = {
        "receipts": list,
        "quotas": list,
        "lines_by_slot": Mapping,
        "exports": list,
    }
    for field, expected_type in expected_types.items():
        if not isinstance(state.get(field), expected_type):
            raise ValueError(f"工作区字段 {field} 类型无效")


def _receipt_record(receipt: Receipt) -> dict[str, Any]:
    return {
        "receipt_id": receipt.receipt_id,
        "source_path": receipt.source_path,
        "invoice_date": _date_record_value(receipt.invoice_date),
        "total_cents": receipt.total_cents,
        "vendor_name": receipt.vendor_name,
        "invoice_number": receipt.invoice_number,
        "ocr_text": receipt.ocr_text,
        "is_material": receipt.is_material,
        "extraction_note": receipt.extraction_note,
        "line_items": [
            {
                "name": item.name,
                "specification": item.specification,
                "unit": item.unit,
                "quantity": item.quantity,
                "unit_price_cents": item.unit_price_cents,
                "amount_cents": item.amount_cents,
                "confidence": item.confidence,
            }
            for item in receipt.line_items
        ],
    }


def _quota_record(quota: QuotaSlot) -> dict[str, Any]:
    return {"slot_id": quota.slot_id, "capacity_cents": quota.capacity_cents, "label": quota.label}


def _recognized_line_items(value: Any) -> tuple[RecognizedLineItem, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    items: list[RecognizedLineItem] = []
    for record in value:
        if not isinstance(record, Mapping):
            continue
        try:
            unit_price_cents = record.get("unit_price_cents")
            amount_cents = record.get("amount_cents")
            items.append(RecognizedLineItem(
                name=str(record.get("name", "")),
                specification=str(record.get("specification", "")),
                unit=str(record.get("unit", "")),
                quantity=str(record.get("quantity", "")),
                unit_price_cents=int(unit_price_cents) if unit_price_cents is not None else None,
                amount_cents=int(amount_cents) if amount_cents is not None else None,
                confidence=str(record.get("confidence", "pending")),
            ))
        except (TypeError, ValueError):
            continue
    return tuple(items)


def _safe_workspace_name(name: str) -> str:
    safe_name = _SAFE_NAME.sub("-", str(name)).strip("-_")
    return safe_name or "materials"


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError("invoice_date must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError:
        return datetime.fromisoformat(value).date()


def _normalized_records(records: Any, record_type: type, serializer) -> list[dict[str, Any]]:
    if not isinstance(records, list):
        return []

    normalized_records: list[dict[str, Any]] = []
    for record in records:
        if isinstance(record, record_type):
            normalized_records.append(serializer(record))
            continue
        if not isinstance(record, Mapping):
            continue

        normalized = dict(record)
        if "confirmed" in normalized:
            normalized["confirmed"] = _parse_bool(normalized["confirmed"])
        if record_type is Receipt:
            restored = workspace_receipts({"receipts": [record]})
        else:
            restored = workspace_quotas({"quotas": [record]})
        if restored:
            normalized.update(serializer(restored[0]))
        normalized_records.append(normalized)
    return normalized_records


def _date_record_value(value: date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def _parse_bool(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "0", "false", "off", "no"}:
            return False
        if normalized in {"1", "true", "on", "yes"}:
            return True
    if value in (None, 0, False):
        return False
    if value in (1, True):
        return True
    return bool(value)


def _validate_template_kind(directory: Path, keyword: str, errors: list[str]) -> Path | None:
    templates = find_inventory_templates(directory, keyword)
    if not templates:
        errors.append(f"缺少{keyword}模板")
        return None
    if len(templates) != 1:
        errors.append(f"{keyword}模板必须恰好一个，当前找到 {len(templates)} 个")
        return None
    template_path = templates[0]
    try:
        inspect_inventory_template(template_path, is_inbound=keyword == "入库单")
    except (BadZipFile, OSError, ValueError, KeyError) as exc:
        errors.append(f"{keyword}模板不可用：{template_path.name}（{exc}）")
        return None
    return template_path


def _find_quota_record(quotas: Any, slot_id: str) -> Any | None:
    if not isinstance(quotas, list):
        return None
    for quota in quotas:
        if str(_record_value(quota, "slot_id")) == slot_id:
            return quota
    return None


def _receipt_ids_for_quota(quota: Any) -> list[str]:
    receipt_ids = _record_value(quota, "receipt_ids")
    if not isinstance(receipt_ids, (list, tuple)):
        return []
    return [str(receipt_id) for receipt_id in receipt_ids if str(receipt_id)]


def _receipt_ids_reused_by_other_quotas(quotas: Any, slot_id: str, receipt_ids: list[str]) -> list[str]:
    if not isinstance(quotas, list):
        return []
    selected = set(receipt_ids)
    reused: set[str] = set()
    for quota in quotas:
        if str(_record_value(quota, "slot_id")) == slot_id:
            continue
        reused.update(selected.intersection(_receipt_ids_for_quota(quota)))
    return sorted(reused)


def _records_by_id(records: Any, key: str) -> dict[str, Any]:
    if not isinstance(records, list):
        return {}
    result: dict[str, Any] = {}
    for record in records:
        record_id = _record_value(record, key)
        if record_id is not None:
            result.setdefault(str(record_id), record)
    return result


def _record_value(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(key, default)
    return getattr(record, key, default)


def _validate_receipt_record(
    record: Any,
    receipt_id: str,
    total_cents: int | None,
    amount_error: str | None,
    errors: list[str],
) -> None:
    confirmed = _record_value(record, "confirmed")
    extraction_note = str(_record_value(record, "extraction_note", ""))
    if not _parse_bool(confirmed) and "人工已确认" not in extraction_note:
        errors.append(f"票据 {receipt_id} 尚未人工确认")
    if not _parse_bool(_record_value(record, "is_material", True)):
        errors.append(f"票据 {receipt_id} 不是材料票据")

    if amount_error:
        errors.append(f"票据 {receipt_id} {amount_error}")
    elif total_cents is None or total_cents <= 0:
        errors.append(f"票据 {receipt_id} 缺少正金额")
    try:
        valid_date = _parse_date(_record_value(record, "invoice_date")) is not None
    except (TypeError, ValueError):
        valid_date = False
    if not valid_date:
        errors.append(f"票据 {receipt_id} 缺少日期")
    source_path = _record_value(record, "source_path", "")
    if not source_path or not Path(str(source_path)).is_file():
        errors.append(f"票据 {receipt_id} 源文件不存在")


def _receipt_total_cents(record: Any) -> tuple[int | None, str | None]:
    total_cents = _record_value(record, "total_cents")
    if total_cents not in (None, ""):
        try:
            return _parse_cents(total_cents), None
        except ValueError:
            return None, "total_cents 必须是有限整数分"
    for key in ("total", "amount"):
        amount = _record_value(record, key)
        if amount in (None, ""):
            continue
        try:
            return parse_amount_to_cents(amount), None
        except (TypeError, ValueError):
            return None, "金额格式无效"
    return None, None


def _lines_for_slot(lines_by_slot: Any, slot_id: str) -> list[Any]:
    if not isinstance(lines_by_slot, Mapping):
        return []
    lines = lines_by_slot.get(slot_id)
    return list(lines) if isinstance(lines, (list, tuple)) else []


def _inventory_lines_from_state(raw_lines: list[Any]) -> tuple[list[InventoryLine], list[str]]:
    lines: list[InventoryLine] = []
    errors: list[str] = []
    for index, raw_line in enumerate(raw_lines, start=1):
        if not isinstance(raw_line, Mapping):
            errors.append(f"物品明细校验失败：第 {index} 行不是有效记录")
            continue
        try:
            inventory_date = _parse_date(raw_line.get("inventory_date", raw_line.get("date")))
            if inventory_date is None:
                raise ValueError("缺少日期")
            lines.append(
                InventoryLine(
                    inventory_date=inventory_date,
                    name=str(raw_line.get("name", "")),
                    specification=str(raw_line.get("specification", "")),
                    unit=str(raw_line.get("unit", "")),
                    quantity=raw_line.get("quantity", ""),
                    unit_price_cents=_line_amount_cents(raw_line, "unit_price"),
                    amount_cents=_line_amount_cents(raw_line, "amount"),
                    supplier_name=str(raw_line.get("supplier_name", "")),
                )
            )
        except (TypeError, ValueError) as exc:
            errors.append(f"物品明细校验失败：第 {index} 行{exc}")
    return lines, errors


def _line_amount_cents(line: Mapping[str, Any], money_key: str) -> int:
    cents_key = f"{money_key}_cents"
    if cents_key in line:
        try:
            return _parse_cents(line[cents_key])
        except ValueError as exc:
            raise ValueError(f"{cents_key} {exc}") from exc
    if money_key not in line:
        raise ValueError(f"缺少{money_key}")
    return parse_amount_to_cents(line[money_key])


def _parse_cents(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("必须是有限整数分")
    if isinstance(value, int):
        return value
    if not isinstance(value, (str, float, Decimal)):
        raise ValueError("必须是有限整数分")
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError("必须是有限整数分") from None
    if not parsed.is_finite() or parsed != parsed.to_integral_value():
        raise ValueError("必须是有限整数分")
    return int(parsed)
