"""Run local OCR in a disposable child process.

Paddle includes native libraries that can terminate the Python process.  This
module keeps that failure outside the desktop UI and converts every observable
worker failure into a normal ``OCRResult`` for manual confirmation.
"""

from __future__ import annotations

from collections.abc import Mapping
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import uuid

from core.ocr import OCRResult, get_ocr_provider, recognize_safely


DEFAULT_TIMEOUT_SECONDS = 120


def _json_safe(value):
    """Convert Paddle/NumPy result values into JSON-compatible values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    to_list = getattr(value, "tolist", None)
    if callable(to_list):
        return _json_safe(to_list())
    to_item = getattr(value, "item", None)
    if callable(to_item):
        return _json_safe(to_item())
    return str(value)


def result_to_payload(result: OCRResult) -> dict:
    """Serialize an OCR result without depending on NumPy's JSON behavior."""
    return {
        "text": result.text or "",
        "fields": _json_safe(result.fields or {}),
        "provider": result.provider or "",
        "confidence": _json_safe(result.confidence),
        "raw": _json_safe(result.raw),
        "error": result.error,
    }


def result_from_payload(payload: Mapping) -> OCRResult:
    """Validate and restore an OCR result received from a worker."""
    if not isinstance(payload, Mapping):
        raise ValueError("OCR worker payload must be an object")

    fields = payload.get("fields", {})
    raw = payload.get("raw")
    if not isinstance(fields, Mapping):
        raise ValueError("OCR worker fields must be an object")
    if raw is not None and not isinstance(raw, Mapping):
        raise ValueError("OCR worker raw output must be an object or null")

    confidence = payload.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError) as exc:
            raise ValueError("OCR worker confidence must be numeric or null") from exc

    return OCRResult(
        text=str(payload.get("text") or ""),
        fields=dict(fields),
        provider=str(payload.get("provider") or "paddle"),
        confidence=confidence,
        raw=dict(raw) if raw is not None else None,
        error=str(payload["error"]) if payload.get("error") else None,
    )


def _write_payload(output_path: Path, payload: Mapping) -> None:
    """Write a worker response atomically so the parent never reads half JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(
        f".{output_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _suppress_windows_crash_dialogs() -> None:
    """Prevent a failed native OCR worker from blocking on a Windows dialog."""
    if os.name != "nt":
        return
    try:
        # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    except (AttributeError, OSError):
        pass


def run_worker(source_path: str | Path, output_path: str | Path) -> int:
    """Run PaddleOCR once and write a normalized result for the parent process."""
    _suppress_windows_crash_dialogs()
    try:
        provider = get_ocr_provider("paddle")
        result = recognize_safely(provider, str(source_path))
    except Exception as exc:
        result = OCRResult(provider="paddle", error=f"OCR worker failed: {exc}")

    try:
        _write_payload(Path(output_path), result_to_payload(result))
    except OSError:
        return 1
    return 0


def worker_command(source_path: Path, output_path: Path) -> list[str]:
    """Build the hidden worker command for source and frozen applications."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--ocr-worker", str(source_path), str(output_path)]
    launcher = Path(__file__).resolve().parent.parent / "run_materials_desktop.py"
    return [
        sys.executable,
        str(launcher),
        "--ocr-worker",
        str(source_path),
        str(output_path),
    ]


def _worker_error(message: str) -> OCRResult:
    return OCRResult(provider="paddle", error=message)


def recognize_with_worker(
    file_path: str | Path,
    timeout_seconds: int | float = DEFAULT_TIMEOUT_SECONDS,
) -> OCRResult:
    """Recognize one receipt without exposing the UI process to native crashes."""
    source_path = Path(file_path)
    with TemporaryDirectory(prefix="rm-ocr-worker-") as temporary_dir:
        output_path = Path(temporary_dir) / "result.json"
        command = worker_command(source_path, output_path)
        run_options = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "timeout": timeout_seconds,
            "check": False,
        }
        if os.name == "nt":
            run_options["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            completed = subprocess.run(command, **run_options)
        except subprocess.TimeoutExpired:
            return _worker_error(
                f"Local OCR worker timed out after {timeout_seconds} seconds"
            )
        except OSError as exc:
            return _worker_error(f"Local OCR worker could not start: {exc}")

        if completed.returncode != 0:
            return _worker_error(
                f"Local OCR worker exited with code {completed.returncode}"
            )

        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            return result_from_payload(payload)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return _worker_error(f"Local OCR worker returned invalid output: {exc}")
