"""Configure the optional bundled PaddleOCR model cache without network access."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys


_REQUIRED_MODEL_FILES = ("inference.pdmodel", "inference.pdiparams")


def bundle_root() -> Path:
    """Return the source root or the PyInstaller onedir release directory."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def bundled_ocr_models_path() -> Path:
    """Return the bundled OCR model directory, if the bundle provides one."""
    return _bundled_asset_path(bundle_root(), "ocr_models")


def bundled_inventory_templates_path() -> Path:
    """Return the release-owned directory containing the official workbooks."""
    return _bundled_asset_path(bundle_root(), "inventory_templates")


def required_model_paths(models_root: str | Path) -> tuple[Path, Path, Path]:
    """Return the detector, recognizer, and classifier directories required offline."""
    root = Path(models_root)
    return (
        root / "whl" / "det" / "ch" / "ch_PP-OCRv4_det_infer",
        root / "whl" / "rec" / "ch" / "ch_PP-OCRv4_rec_infer",
        root / "whl" / "cls" / "ch_ppocr_mobile_v2.0_cls_infer",
    )


def has_required_models(models_root: str | Path) -> bool:
    """Return whether all offline model directories include their inference files."""
    return all(
        model_path.is_dir()
        and all((model_path / filename).is_file() for filename in _REQUIRED_MODEL_FILES)
        for model_path in required_model_paths(models_root)
    )


def _ocr_cache_home(user_home: str | Path) -> Path:
    """Return the user-selected OCR cache location or its explicit override."""
    configured_home = os.environ.get("RM_MATERIALS_OCR_HOME", "").strip()
    if configured_home:
        return Path(configured_home)
    return Path(user_home)


def configure_bundled_ocr_assets(root: str | Path, user_home: str | Path) -> Path:
    """Use bundled models when present, preserving an existing user-local cache."""
    source = _bundled_asset_path(Path(root), "ocr_models")
    target = _ocr_cache_home(user_home) / "ocr_models"
    if has_required_models(source) and not has_required_models(target):
        shutil.copytree(source, target, dirs_exist_ok=True)
    os.environ["PADDLE_OCR_BASE_DIR"] = str(target)
    os.environ["PADDLEOCR_HOME"] = str(target)
    return target


def _bundled_asset_path(root: Path, directory_name: str) -> Path:
    release_path = root / directory_name
    source_path = root / "assets" / directory_name
    if not release_path.exists() and source_path.exists():
        return source_path
    return release_path
