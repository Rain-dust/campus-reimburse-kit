"""Standalone local UI for the university reimbursement materials helper."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import date
from pathlib import Path
import os
import secrets
import shutil
import tempfile
import uuid
import webbrowser

from flask import Flask, abort, flash, redirect, render_template, request, send_file, url_for
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from core.materials_assistant import (
    Receipt, allocate_receipts_to_quota_slots, build_inventory_line_drafts, build_readable_filename,
    extract_pdf_text, ingest_receipt, parse_amount_to_cents,
    validate_inventory_lines,
    verified_pdf_line_items,
)
from core.local_ocr_assets import (
    bundle_root,
    bundled_inventory_templates_path,
    bundled_ocr_models_path,
    configure_bundled_ocr_assets,
    has_required_models,
)
from core.local_ocr_worker import recognize_with_worker
from core.materials_workspace import (
    _inventory_lines_from_state,
    assign_receipt_to_slot,
    create_workspace,
    export_workspace_backup,
    export_quota_package,
    load_workspace,
    migrate_legacy_workspace,
    package_preflight,
    restore_workspace_backup,
    save_workspace,
    validate_template_directory,
    workspace_quotas,
    workspace_receipts,
)


ALLOWED = {".pdf"}
WIZARD_STEPS = ("选择模板", "导入票据", "确认票据", "设置额度", "填写入出库明细", "检查并导出")
MAX_RECEIPT_BYTES = 25 * 1024 * 1024
MAX_REQUEST_BYTES = 300 * 1024 * 1024


def default_home() -> Path:
    root = os.environ.get("RM_MATERIALS_HOME")
    if root:
        return Path(root)
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "RMReimbursementMaterials"


def create_materials_app(home: str | Path | None = None) -> Flask:
    root = Path(home) if home else default_home()
    root.mkdir(parents=True, exist_ok=True)
    bundled_models_available = has_required_models(bundled_ocr_models_path())
    bundled_templates = bundled_inventory_templates_path().resolve()
    template_folder = bundle_root() / "app" / "templates"
    app = Flask("rm_materials_desktop", template_folder=str(template_folder))
    app.config.update(
        SECRET_KEY=os.environ.get("RM_MATERIALS_SECRET") or secrets.token_hex(32),
        MATERIALS_HOME=root,
        MATERIALS_OCR_PROVIDER=os.environ.get("RM_OCR_PROVIDER", "paddle") if bundled_models_available else "mock",
        MATERIALS_OFFLINE_OCR=not bundled_models_available,
        MATERIALS_TEMPLATE_DIR=bundled_templates,
        MAX_RECEIPT_BYTES=MAX_RECEIPT_BYTES,
        MAX_CONTENT_LENGTH=MAX_REQUEST_BYTES,
    )
    if bundled_models_available:
        configure_bundled_ocr_assets(bundle_root(), root)
    migrate_legacy_workspace(root)
    app.jinja_env.globals["receipt_source_name"] = _receipt_source_name
    app.jinja_env.globals["receipt_display_name"] = _receipt_display_name

    @app.errorhandler(ValueError)
    def handle_workspace_error(exc: ValueError):
        flash(f"工作区无法加载：{exc}")
        return redirect(url_for("index"))

    @app.errorhandler(OSError)
    def handle_storage_error(exc: OSError):
        app.logger.exception("Local materials workspace storage error", exc_info=exc)
        if request.method == "POST":
            flash("本地文件写入失败，请检查磁盘空间和文件夹权限。")
            return redirect(url_for("index"))
        return "本地文件写入失败，请检查磁盘空间和文件夹权限后重试。", 500

    @app.errorhandler(RequestEntityTooLarge)
    def handle_request_too_large(_exc: RequestEntityTooLarge):
        workspace_id = (request.view_args or {}).get("workspace_id")
        if request.endpoint == "import_receipts" and isinstance(workspace_id, str):
            flash("本次导入的票据总大小过大，请分批导入。")
            return _redirect_step(workspace_id, 2)
        flash("上传文件过大")
        return redirect(url_for("index"))

    def guarded_workspace(workspace_id: str, target_step: int):
        workspace_dir = _workspace_path(root, workspace_id)
        state = load_workspace(workspace_dir)
        missing_step = _missing_step(state, target_step)
        if missing_step is not None:
            flash(f"请先完成第 {missing_step} 步：{WIZARD_STEPS[missing_step - 1]}")
            return None, None, _redirect_step(workspace_id, missing_step)
        return workspace_dir, state, None

    @app.get("/")
    def index():
        workspaces = _workspace_summaries(root / "workspaces")
        return render_template("materials/base.html", workspaces=workspaces, steps=WIZARD_STEPS)

    @app.post("/workspace/new")
    def new_workspace():
        workspace_dir, _ = create_workspace(
            root,
            request.form.get("name", "").strip(),
            str(app.config["MATERIALS_TEMPLATE_DIR"]),
        )
        return redirect(url_for("wizard_step", workspace_id=workspace_dir.name, step=1))

    @app.post("/workspace/<workspace_id>/delete")
    def delete_workspace(workspace_id: str):
        workspace_dir = _workspace_path(root, workspace_id)
        try:
            workspace_name = load_workspace(workspace_dir).get("name") or workspace_id
        except ValueError:
            workspace_name = workspace_id
        shutil.rmtree(workspace_dir)
        flash(f"已删除工作区：{workspace_name}")
        return redirect(url_for("index"))

    @app.post("/workspace/restore")
    def restore_workspace():
        upload = request.files.get("backup")
        if upload is None or not upload.filename:
            flash("请选择工作区备份 ZIP 文件")
            return redirect(url_for("index"))
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=root, prefix=".restore-", suffix=".zip", delete=False
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
            upload.save(temporary_path)
            workspace_dir = restore_workspace_backup(root, temporary_path)
        except (OSError, ValueError) as exc:
            flash(f"恢复备份失败：{exc}")
            return redirect(url_for("index"))
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return redirect(url_for("wizard_step", workspace_id=workspace_dir.name, step=1))

    @app.get("/workspace/<workspace_id>/backup")
    def download_workspace_backup(workspace_id: str):
        workspace_dir = _workspace_path(root, workspace_id)
        archive = export_workspace_backup(workspace_dir)
        return send_file(
            archive,
            as_attachment=True,
            download_name=f"{workspace_id}-backup.zip",
            mimetype="application/zip",
        )

    @app.get("/workspace/<workspace_id>/step/<int:step>")
    def wizard_step(workspace_id: str, step: int):
        if step not in range(1, len(WIZARD_STEPS) + 1):
            abort(404)
        workspace_dir, state, guarded = guarded_workspace(workspace_id, step)
        if guarded is not None:
            return guarded
        preflight_errors = {
            quota["slot_id"]: package_preflight(state, quota["slot_id"])
            for quota in state["quotas"]
        }
        return render_template(
            "materials/wizard.html",
            workspace_id=workspace_id,
            state=state,
            step=step,
            steps=WIZARD_STEPS,
            preflight_errors=preflight_errors,
            quota_summaries=_quota_summaries(state),
            next_action=_wizard_guidance(state, step)[0],
            blocking_errors=_wizard_guidance(state, step)[1],
            workspace_name=state["name"],
        )

    @app.post("/workspace/<workspace_id>/templates")
    def update_templates(workspace_id: str):
        workspace_dir = _workspace_path(root, workspace_id)
        state = load_workspace(workspace_dir)
        template_dir = request.form.get("template_dir", "").strip()
        try:
            validation = validate_template_directory(template_dir)
        except ValueError as exc:
            _flash_value_error(exc)
            return _redirect_step(workspace_id, 1)
        except OSError:
            flash("模板目录无法读取，请检查路径和访问权限。")
            return _redirect_step(workspace_id, 1)
        if not validation.valid:
            for error in validation.errors:
                flash(f"模板无效：{error}")
            return _redirect_step(workspace_id, 1)
        state["template_dir"] = str(Path(template_dir).resolve())
        state["ocr_provider"] = (
            "mock" if app.config["MATERIALS_OFFLINE_OCR"]
            else request.form.get("ocr_provider", "").strip() or state["ocr_provider"]
        )
        save_workspace(workspace_dir, state)
        return _redirect_step(workspace_id, 2)

    @app.post("/workspace/<workspace_id>/receipts/import")
    def import_receipts(workspace_id: str):
        workspace_dir, state, guarded = guarded_workspace(workspace_id, 2)
        if guarded is not None:
            return guarded
        uploads = [upload for upload in request.files.getlist("receipts") if upload.filename]
        if not uploads:
            flash("请至少选择一份票据，再点击“导入本地票据”。")
            return _redirect_step(workspace_id, 2)
        unsupported = [
            Path(upload.filename).name
            for upload in uploads
            if Path(upload.filename).suffix.lower() not in ALLOWED
        ]
        if unsupported:
            flash(
                "不支持的票据格式："
                + "、".join(unsupported)
                + "。仅支持 PDF；图片通常是付款记录，请不要作为发票导入。"
            )
            return _redirect_step(workspace_id, 2)
        recognizer = None
        if app.config["MATERIALS_OFFLINE_OCR"]:
            provider = "mock"
            flash("未找到随包 OCR 模型，已使用离线手工确认模式。")
        else:
            provider = (
                request.form.get("ocr_provider", "").strip()
                or state.get("ocr_provider", "").strip()
                or app.config["MATERIALS_OCR_PROVIDER"]
            )
            if provider.lower() in {"paddle", "paddleocr"}:
                recognizer = recognize_with_worker
        staging_dir = workspace_dir / ".import-staging" / uuid.uuid4().hex
        staged_files: list[tuple[Path, Path]] = []
        moved_files: list[Path] = []
        records: list[dict] = []
        pending_state = deepcopy(state)
        try:
            staging_dir.mkdir(parents=True)
            for upload in uploads:
                original_filename = upload.filename or ""
                suffix = Path(original_filename).suffix.lower()
                safe_base = secure_filename(Path(original_filename).stem)
                stored_name = f"{uuid.uuid4().hex}-{safe_base}{suffix}" if safe_base else f"{uuid.uuid4().hex}{suffix}"
                staged = staging_dir / stored_name
                final = workspace_dir / "imports" / stored_name
                upload.save(staged)
                if staged.stat().st_size > app.config["MAX_RECEIPT_BYTES"]:
                    limit_mb = app.config["MAX_RECEIPT_BYTES"] // (1024 * 1024)
                    flash(
                        f"单个票据不能超过 {limit_mb} MB：{Path(original_filename).name}"
                    )
                    return _redirect_step(workspace_id, 2)
                try:
                    receipt = ingest_receipt(
                        staged,
                        provider,
                        recognizer=recognizer,
                    )
                except (OSError, ValueError):
                    raise
                except Exception:
                    flash("票据识别不可用，请手动确认票据内容")
                    receipt = Receipt(uuid.uuid4().hex, str(staged), None, None)
                record = _receipt_record(receipt, uuid.uuid4().hex)
                record["source_path"] = str(final)
                record["original_filename"] = Path(original_filename).name
                records.append(record)
                staged_files.append((staged, final))

            pending_state["receipts"].extend(records)
            for staged, final in staged_files:
                staged.replace(final)
                moved_files.append(final)
            save_workspace(workspace_dir, pending_state)
        except (OSError, ValueError):
            for moved_file in moved_files:
                moved_file.unlink(missing_ok=True)
            raise
        else:
            state["receipts"] = pending_state["receipts"]
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)
            try:
                staging_dir.parent.rmdir()
            except OSError:
                pass
        flash(f"本批已导入 {len(records)} 份 PDF，当前共 {len(state['receipts'])} 份；可继续从其他文件夹导入。")
        return _redirect_step(workspace_id, 3)

    @app.post("/workspace/<workspace_id>/receipts/<receipt_id>")
    def confirm_receipt(workspace_id: str, receipt_id: str):
        workspace_dir, state, guarded = guarded_workspace(workspace_id, 3)
        if guarded is not None:
            return guarded
        receipt = _find_record(state["receipts"], "receipt_id", receipt_id)
        if receipt is None:
            abort(404)
        try:
            _update_receipt_from_form(workspace_dir, state, receipt, request.form)
        except ValueError as exc:
            _flash_value_error(exc)
            return _redirect_step(workspace_id, 3)
        _fill_empty_inventory_drafts(state)
        save_workspace(workspace_dir, state)
        remaining = sum(not item.get("confirmed", False) for item in state["receipts"])
        if remaining:
            flash(f"已确认 {_receipt_display_name(receipt, state['receipts'].index(receipt) + 1)}，还剩 {remaining} 张未确认。")
            return _redirect_step(workspace_id, 3)
        flash("全部票据已确认，可以设置报销额度。")
        return _redirect_step(workspace_id, 4)

    @app.post("/workspace/<workspace_id>/receipts/confirm-all")
    def confirm_all_receipts(workspace_id: str):
        workspace_dir, state, guarded = guarded_workspace(workspace_id, 3)
        if guarded is not None:
            return guarded
        if not state["receipts"]:
            flash("当前没有可确认的票据。")
            return _redirect_step(workspace_id, 3)

        pending_state = deepcopy(state)
        try:
            for index, receipt in enumerate(pending_state["receipts"], start=1):
                try:
                    _update_receipt_from_form(
                        workspace_dir,
                        pending_state,
                        receipt,
                        request.form,
                    )
                except ValueError as exc:
                    name = _receipt_display_name(receipt, index)
                    raise ValueError(f"第 {index} 张票据（{name}）：{exc}") from exc
        except ValueError as exc:
            _flash_value_error(exc)
            return _redirect_step(workspace_id, 3)

        _fill_empty_inventory_drafts(pending_state)
        save_workspace(workspace_dir, pending_state)
        flash(f"已确认全部 {len(pending_state['receipts'])} 张票据，可以设置报销额度。")
        return _redirect_step(workspace_id, 4)

    @app.post("/workspace/<workspace_id>/receipts/<receipt_id>/delete")
    def delete_receipt(workspace_id: str, receipt_id: str):
        workspace_dir, state, guarded = guarded_workspace(workspace_id, 3)
        if guarded is not None:
            return guarded
        receipt = _find_record(state["receipts"], "receipt_id", receipt_id)
        if receipt is None:
            abort(404)

        source_path = _workspace_import_path(workspace_dir, receipt)
        pending_state = deepcopy(state)
        pending_state["receipts"] = [
            record for record in pending_state["receipts"]
            if str(record.get("receipt_id")) != receipt_id
        ]
        for quota in pending_state.get("quotas", []):
            if isinstance(quota, dict):
                quota["receipt_ids"] = [
                    assigned_id for assigned_id in quota.get("receipt_ids", [])
                    if str(assigned_id) != receipt_id
                ]
        _fill_empty_inventory_drafts(pending_state)
        save_workspace(workspace_dir, pending_state)

        if source_path is not None:
            try:
                source_path.unlink(missing_ok=True)
            except OSError:
                flash("票据记录已删除，但原始副本清理失败；请手动检查 imports 文件夹。")
                return _redirect_step(workspace_id, 3)
        flash("已删除票据，并从全部额度分配中移除。")
        return _redirect_step(workspace_id, 3)

    @app.get("/workspace/<workspace_id>/receipts/<receipt_id>/source")
    def receipt_source(workspace_id: str, receipt_id: str):
        workspace_dir = _workspace_path(root, workspace_id)
        state = load_workspace(workspace_dir)
        receipt = _find_record(state["receipts"], "receipt_id", receipt_id)
        if receipt is None:
            abort(404)
        source_path = _workspace_import_path(workspace_dir, receipt)
        if source_path is None or not source_path.is_file():
            abort(404)
        return send_file(
            source_path,
            as_attachment=source_path.suffix.lower() not in {".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".webp"},
        )

    @app.post("/workspace/<workspace_id>/quotas")
    def add_quota(workspace_id: str):
        workspace_dir, state, guarded = guarded_workspace(workspace_id, 4)
        if guarded is not None:
            return guarded
        try:
            capacity_cents = parse_amount_to_cents(request.form.get("capacity", ""))
            if capacity_cents <= 0:
                raise ValueError("额度必须大于零")
        except ValueError as exc:
            _flash_value_error(exc)
            return _redirect_step(workspace_id, 4)
        state["quotas"].append({
            "slot_id": uuid.uuid4().hex[:8],
            "capacity_cents": capacity_cents,
            "label": request.form.get("label", "").strip(),
            "receipt_ids": [],
        })
        save_workspace(workspace_dir, state)
        return _redirect_step(workspace_id, 5)

    @app.post("/workspace/<workspace_id>/quotas/<slot_id>/update")
    def update_quota(workspace_id: str, slot_id: str):
        workspace_dir, state, guarded = guarded_workspace(workspace_id, 4)
        if guarded is not None:
            return guarded
        quota = _find_record(state["quotas"], "slot_id", slot_id)
        if quota is None:
            abort(404)
        try:
            capacity_cents = parse_amount_to_cents(request.form.get("capacity", ""))
            if capacity_cents <= 0:
                raise ValueError("额度必须大于零")
            assigned_ids = set(quota.get("receipt_ids", []))
            assigned_total_cents = sum(
                receipt.total_cents or 0
                for receipt in workspace_receipts(state)
                if receipt.receipt_id in assigned_ids
            )
            if capacity_cents < assigned_total_cents:
                raise ValueError("额度不能小于已分配票据合计")
        except ValueError as exc:
            _flash_value_error(exc)
            return _redirect_step(workspace_id, 4)

        quota["label"] = request.form.get("label", "").strip()
        quota["capacity_cents"] = capacity_cents
        save_workspace(workspace_dir, state)
        flash("额度已更新")
        return _redirect_step(workspace_id, 4)

    @app.post("/workspace/<workspace_id>/quotas/<slot_id>/delete")
    def delete_quota(workspace_id: str, slot_id: str):
        workspace_dir, state, guarded = guarded_workspace(workspace_id, 4)
        if guarded is not None:
            return guarded
        quota = _find_record(state["quotas"], "slot_id", slot_id)
        if quota is None:
            abort(404)

        state["quotas"] = [
            record for record in state["quotas"] if record.get("slot_id") != slot_id
        ]
        state["lines_by_slot"].pop(slot_id, None)
        save_workspace(workspace_dir, state)
        flash("额度已删除，原票据仍保留且可重新分配")
        return _redirect_step(workspace_id, 4)

    @app.post("/workspace/<workspace_id>/quotas/<slot_id>/assign")
    def assign_receipts(workspace_id: str, slot_id: str):
        workspace_dir, state, guarded = guarded_workspace(workspace_id, 5)
        if guarded is not None:
            return guarded
        quota = _find_record(state["quotas"], "slot_id", slot_id)
        if quota is None:
            abort(404)
        selected_ids = set(request.form.getlist("receipt_ids"))
        existing_ids = set(quota.get("receipt_ids", []))
        try:
            for receipt_id in existing_ids - selected_ids:
                assign_receipt_to_slot(state, receipt_id, None)
            for receipt_id in selected_ids:
                assign_receipt_to_slot(state, receipt_id, slot_id)
        except ValueError as exc:
            _flash_value_error(exc)
            return _redirect_step(workspace_id, 5)
        _fill_empty_inventory_drafts(state)
        save_workspace(workspace_dir, state)
        return _redirect_step(workspace_id, 5)

    @app.post("/workspace/<workspace_id>/quotas/<slot_id>/lines")
    def save_lines(workspace_id: str, slot_id: str):
        workspace_dir, state, guarded = guarded_workspace(workspace_id, 5)
        if guarded is not None:
            return guarded
        if _find_record(state["quotas"], "slot_id", slot_id) is None:
            abort(404)
        fields = (
            "inventory_date", "name", "specification", "unit", "quantity",
            "unit_price", "amount", "supplier_name",
        )
        values = {field: request.form.getlist(field) for field in fields}
        count = max((len(items) for items in values.values()), default=0)
        lines = [
            {field: values[field][index] if index < len(values[field]) else "" for field in fields}
            for index in range(count)
        ]
        state["lines_by_slot"][slot_id] = [
            line for line in lines if any(value.strip() for value in line.values())
        ]
        save_workspace(workspace_dir, state)
        return _redirect_step(workspace_id, 6)

    @app.post("/workspace/<workspace_id>/quotas/auto-assign")
    def auto_assign_receipts(workspace_id: str):
        workspace_dir, state, guarded = guarded_workspace(workspace_id, 5)
        if guarded is not None:
            return guarded
        for record in state["receipts"]:
            if isinstance(record, dict) and record.get("confirmed"):
                _refresh_record_line_items(workspace_dir, record)
        confirmed_ids = {
            str(receipt.get("receipt_id")) for receipt in state["receipts"]
            if receipt.get("confirmed")
        }
        receipts = [
            receipt for receipt in workspace_receipts(state) if receipt.receipt_id in confirmed_ids
        ]
        try:
            allocation = allocate_receipts_to_quota_slots(receipts, workspace_quotas(state))
            for quota in state["quotas"]:
                for receipt_id in list(quota.get("receipt_ids", [])):
                    assign_receipt_to_slot(state, receipt_id, None)
            for package in allocation.packages:
                for receipt_id in package.receipt_ids:
                    assign_receipt_to_slot(state, receipt_id, package.slot_id)
            _fill_empty_inventory_drafts(state)
        except ValueError as exc:
            _flash_value_error(exc)
            return _redirect_step(workspace_id, 5)
        save_workspace(workspace_dir, state)
        return _redirect_step(workspace_id, 5)

    @app.post("/workspace/<workspace_id>/export/<slot_id>")
    def export_package(workspace_id: str, slot_id: str):
        workspace_dir, state, guarded = guarded_workspace(workspace_id, 6)
        if guarded is not None:
            return guarded
        try:
            export_quota_package(workspace_dir, state, slot_id)
        except ValueError as exc:
            _flash_value_error(exc)
        except OSError:
            flash("导出失败，请检查磁盘空间和目录权限。")
        else:
            flash("材料包已生成")
        return _redirect_step(workspace_id, 6)

    @app.post("/workspace/<workspace_id>/exports/<path:export_name>/open")
    def open_export(workspace_id: str, export_name: str):
        workspace_dir = _workspace_path(root, workspace_id)
        export_dir = _export_directory(workspace_dir, export_name)
        try:
            if os.name == "nt":
                os.startfile(str(export_dir))
            elif not webbrowser.open(export_dir.as_uri()):
                raise OSError("unable to open export directory")
        except OSError:
            flash("无法打开材料包目录，请检查目录权限。")
        return _redirect_step(workspace_id, 6)

    return app


def _workspace_summaries(workspaces_dir: Path) -> list[dict[str, str]]:
    """Load user-facing workspace names without letting one bad file hide the list."""
    if not workspaces_dir.exists():
        return []
    try:
        directories = [path for path in workspaces_dir.iterdir() if path.is_dir()]
    except OSError:
        return []

    def modified_time(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0

    summaries: list[dict[str, str]] = []
    for path in sorted(directories, key=modified_time, reverse=True):
        try:
            state = load_workspace(path)
        except ValueError:
            summaries.append({
                "id": path.name,
                "name": path.name,
                "error": "无法读取工作区，请删除后重新创建或从备份恢复。",
            })
            continue
        summaries.append({
            "id": path.name,
            "name": str(state.get("name") or "未命名工作区"),
            "error": "",
        })
    return summaries


def _workspace_path(root: Path, workspace_id: str) -> Path:
    workspace_dir = (root / "workspaces" / workspace_id).resolve()
    if workspace_dir.parent != (root / "workspaces").resolve() or not workspace_dir.is_dir():
        abort(404)
    return workspace_dir


def _export_directory(workspace_dir: Path, export_name: str) -> Path:
    exports_dir = (workspace_dir / "exports").resolve()
    export_dir = (exports_dir / export_name).resolve()
    try:
        export_dir.relative_to(exports_dir)
    except ValueError:
        abort(404)
    if not export_dir.is_dir():
        abort(404)
    return export_dir


def _receipt_record(receipt: Receipt, receipt_id: str) -> dict:
    record = asdict(receipt)
    record["receipt_id"] = receipt_id
    record["invoice_date"] = receipt.invoice_date.isoformat() if receipt.invoice_date else None
    record["confirmed"] = False
    return record


def _find_record(records: list[dict], key: str, value: str) -> dict | None:
    return next((record for record in records if str(record.get(key)) == value), None)


def _flash_value_error(exc: ValueError) -> None:
    for message in str(exc).splitlines():
        if message.strip():
            flash(message)


def _redirect_step(workspace_id: str, step: int):
    return redirect(url_for("wizard_step", workspace_id=workspace_id, step=step))


def _receipt_source_name(source_path: str) -> str:
    return Path(str(source_path).replace("\\", "/")).name or "未命名票据"


def _receipt_display_name(receipt: dict, sequence: int) -> str:
    """Expose the export-style name instead of the collision-safe stored name."""
    source_path = str(receipt.get("source_path", ""))
    date_value = str(receipt.get("date") or receipt.get("invoice_date") or "").strip()
    total_cents = receipt.get("total_cents")
    try:
        readable_receipt = Receipt(
            receipt_id=str(receipt.get("receipt_id", "receipt")),
            source_path=source_path,
            invoice_date=date.fromisoformat(date_value),
            total_cents=int(total_cents),
        )
        return build_readable_filename(readable_receipt, sequence)
    except (TypeError, ValueError):
        original_name = str(receipt.get("original_filename", "")).strip()
        if original_name:
            return Path(original_name).name
        stored_name = _receipt_source_name(source_path)
        return stored_name[33:] if len(stored_name) > 33 and stored_name[32] == "-" else stored_name


def _update_receipt_from_form(
    workspace_dir: Path,
    state: dict,
    receipt: dict,
    form,
) -> None:
    """Apply one keyed bulk row or a legacy single-row confirmation."""
    receipt_id = str(receipt.get("receipt_id", ""))
    keyed = f"total-{receipt_id}" in form

    def value(field: str) -> str:
        name = f"{field}-{receipt_id}" if keyed else field
        return str(form.get(name, "")).strip()

    previous_total_cents = receipt.get("total_cents")
    total = value("total")
    total_cents = parse_amount_to_cents(total)
    if total_cents <= 0:
        raise ValueError("票据金额必须大于零")

    date_value = value("date")
    vendor = value("vendor")
    invoice = value("invoice")
    material_name = f"is_material-{receipt_id}" if keyed else "is_material"
    receipt.update({
        "confirmed": True,
        "date": date_value,
        "total": total,
        "vendor": vendor,
        "invoice": invoice,
        "is_material": form.get(material_name) == "on",
        "invoice_date": date_value or None,
        "total_cents": total_cents,
        "vendor_name": vendor,
        "invoice_number": invoice,
    })
    _refresh_record_line_items(workspace_dir, receipt, previous_total_cents)
    if not receipt["is_material"]:
        for quota in state.get("quotas", []):
            if isinstance(quota, dict):
                quota["receipt_ids"] = [
                    assigned_id for assigned_id in quota.get("receipt_ids", [])
                    if str(assigned_id) != receipt_id
                ]


def _fill_empty_inventory_drafts(state: dict) -> None:
    """Populate only empty quota cards so auto-assignment never overwrites review work."""
    receipts_by_id = {receipt.receipt_id: receipt for receipt in workspace_receipts(state)}
    lines_by_slot = state.setdefault("lines_by_slot", {})
    for quota in state.get("quotas", []):
        if not isinstance(quota, dict):
            continue
        slot_id = str(quota.get("slot_id", ""))
        existing_lines = lines_by_slot.get(slot_id)
        if not slot_id or (
            existing_lines
            and not _is_bad_legacy_auto_draft(existing_lines)
            and not _is_automatic_inventory_draft(existing_lines)
        ):
            continue
        assigned_receipts = [
            receipts_by_id[receipt_id]
            for receipt_id in quota.get("receipt_ids", [])
            if receipt_id in receipts_by_id
        ]
        drafts = build_inventory_line_drafts(assigned_receipts)
        if drafts:
            lines_by_slot[slot_id] = drafts
        else:
            lines_by_slot.pop(slot_id, None)


def _is_bad_legacy_auto_draft(lines: object) -> bool:
    """Recognise only the faulty pre-fix drafts that can be safely regenerated."""
    if not isinstance(lines, list) or not lines:
        return False
    return all(
        isinstance(line, dict)
        and any(marker in str(line.get("name", "")) for marker in ("电子发票", "项目名称", "规格型号"))
        and str(line.get("quantity", "")).strip() == "1"
        and str(line.get("unit_price", "")).strip() == str(line.get("amount", "")).strip()
        for line in lines
    )


def _is_automatic_inventory_draft(lines: object) -> bool:
    """Allow a new OCR pass to replace only previous automatic suggestions."""
    return isinstance(lines, list) and bool(lines) and all(
        isinstance(line, dict)
        and (line.get("_auto_generated") is True or "recognition_status" in line)
        for line in lines
    )


def _refresh_record_line_items(
    workspace_dir: Path,
    record: dict,
    previous_total_cents: int | None = None,
) -> None:
    """Reconcile selectable PDFs and invalidate stale scan/image details."""
    source_path = _workspace_import_path(workspace_dir, record)
    if source_path is None:
        return
    total_cents = record.get("total_cents")
    has_selectable_pdf_text = (
        source_path.suffix.lower() == ".pdf"
        and bool(str(record.get("ocr_text", "")).strip())
        and bool(extract_pdf_text(source_path))
    )
    if has_selectable_pdf_text:
        record["line_items"] = [
            asdict(item) for item in verified_pdf_line_items(source_path, total_cents)
        ]
    elif previous_total_cents is not None and previous_total_cents != total_cents:
        record["line_items"] = []


def _workspace_import_path(workspace_dir: Path, receipt: dict) -> Path | None:
    source_path = Path(str(receipt.get("source_path", "")))
    if not source_path.is_absolute():
        source_path = workspace_dir / source_path
    source_path = source_path.resolve()
    imports_dir = (workspace_dir / "imports").resolve()
    try:
        source_path.relative_to(imports_dir)
    except ValueError:
        return None
    return source_path


def _quota_summaries(state: dict) -> dict[str, dict[str, int]]:
    receipts = {receipt.receipt_id: receipt for receipt in workspace_receipts(state)}
    quotas = {quota.slot_id: quota for quota in workspace_quotas(state)}
    summaries: dict[str, dict[str, int]] = {}
    for quota_record in state.get("quotas", []):
        if not isinstance(quota_record, dict):
            continue
        slot_id = str(quota_record.get("slot_id", ""))
        receipt_ids = quota_record.get("receipt_ids", [])
        if not isinstance(receipt_ids, list):
            receipt_ids = []
        selected = [receipts.get(str(receipt_id)) for receipt_id in receipt_ids]
        if any(receipt is None or receipt.total_cents is None for receipt in selected):
            receipt_count = 0
            receipt_total_cents = 0
        else:
            receipt_count = len(selected)
            receipt_total_cents = sum(receipt.total_cents or 0 for receipt in selected)

        lines, line_errors = _inventory_lines_from_state(
            list(state.get("lines_by_slot", {}).get(slot_id, []))
            if isinstance(state.get("lines_by_slot"), dict) else []
        )
        try:
            line_total_cents = validate_inventory_lines(lines) if not line_errors else 0
        except ValueError:
            line_total_cents = 0

        quota = quotas.get(slot_id)
        capacity_cents = quota.capacity_cents if quota is not None else 0
        summaries[slot_id] = {
            "receipt_count": receipt_count,
            "receipt_total_cents": receipt_total_cents,
            "line_total_cents": line_total_cents,
            "remaining_cents": capacity_cents - receipt_total_cents if quota is not None else 0,
        }
    return summaries


def _missing_step(state: dict, target_step: int) -> int | None:
    if target_step >= 2 and _template_errors(state):
        return 1
    if target_step >= 4 and not state["receipts"]:
        return 2
    if target_step >= 4 and any(not receipt.get("confirmed") for receipt in state["receipts"]):
        return 3
    if target_step >= 5 and not state["quotas"]:
        return 4
    return None


def _wizard_guidance(state: dict, step: int) -> tuple[str, list[str]]:
    errors = _template_errors(state)
    receipts = state["receipts"]
    quotas = state["quotas"]
    if step >= 3 and not receipts:
        errors.append("未导入票据")
    if step >= 4 and receipts and any(not receipt.get("confirmed") for receipt in receipts):
        errors.append("仍有未确认票据")
    if step >= 5 and not quotas:
        errors.append("未设置额度")
    if step >= 6 and quotas:
        if any(not quota.get("receipt_ids") for quota in quotas):
            errors.append("存在未分配票据的额度")
        lines_by_slot = state["lines_by_slot"]
        if any(not lines_by_slot.get(quota["slot_id"]) for quota in quotas):
            errors.append("存在未填写明细的额度")

    if _template_errors(state):
        next_action = "选择并保存有效的入库单、出库单模板"
    elif not receipts:
        next_action = "前往第 2 步导入票据"
    elif any(not receipt.get("confirmed") for receipt in receipts):
        next_action = "在第 3 步确认全部票据"
    elif not quotas:
        next_action = "在第 4 步设置额度"
    elif step >= 6 and errors:
        next_action = "在第 5 步分配票据并填写明细"
    else:
        next_action = "继续当前步骤"
    return next_action, errors


def _template_errors(state: dict) -> list[str]:
    try:
        validation = validate_template_directory(state.get("template_dir", ""))
    except OSError:
        return ["模板目录无法读取"]
    return [f"模板无效：{error}" for error in validation.errors]
