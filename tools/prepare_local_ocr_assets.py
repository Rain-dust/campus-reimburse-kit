"""Prepare the minimal PaddleOCR model cache used by the portable release."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys
import uuid


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from core.local_ocr_assets import has_required_models, required_model_paths


def default_paddle_ocr_cache() -> Path:
    """Return the local PaddleOCR cache used as the packaging source."""
    return Path(
        os.environ.get("PADDLE_OCR_BASE_DIR")
        or os.environ.get("PADDLEOCR_HOME")
        or Path.home() / ".paddleocr"
    )


def prepare_local_ocr_assets(source_root: str | Path, target_root: str | Path) -> Path:
    """Copy only the complete detector, recognizer, and classifier model trees."""
    source = Path(source_root).resolve()
    target = Path(target_root).resolve()
    if not has_required_models(source):
        raise ValueError("complete PaddleOCR models are required before packaging")

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}-staging-{uuid.uuid4().hex}"
    backup = target.parent / f".{target.name}-backup-{uuid.uuid4().hex}"
    try:
        for model_path in required_model_paths(source):
            shutil.copytree(model_path, staging / model_path.relative_to(source))
        if not has_required_models(staging):
            raise ValueError("prepared PaddleOCR model assets are incomplete")
        if target.exists():
            target.replace(backup)
        staging.replace(target)
    except Exception:
        if target.exists() and backup.exists():
            shutil.rmtree(target)
        if backup.exists() and not target.exists():
            backup.replace(target)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Copy the complete local PaddleOCR cache for portable packaging."
    )
    parser.add_argument("--source", default=default_paddle_ocr_cache())
    parser.add_argument(
        "--target", default=Path("assets") / "ocr_models", help="Release asset destination."
    )
    args = parser.parse_args(argv)
    try:
        target = prepare_local_ocr_assets(args.source, args.target)
    except (OSError, ValueError) as exc:
        print(f"[ERROR] Cannot prepare local OCR assets: {exc}", file=sys.stderr)
        return 1
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
