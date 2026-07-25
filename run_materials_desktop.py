from __future__ import annotations

import argparse
from pathlib import Path
import sys
from threading import Thread
import webbrowser

from werkzeug.serving import make_server

from core.local_ocr_assets import (
    bundle_root,
    configure_bundled_ocr_assets,
    has_required_models,
    required_model_paths,
)
from core.local_ocr_worker import run_worker
from materials_desktop import create_materials_app, default_home


LOCAL_MATERIALS_SMOKE_OK = "LOCAL_MATERIALS_SMOKE_OK"


def configure_release_assets() -> Path:
    """Copy bundled models to the writable cache and require a complete local set."""
    target = configure_bundled_ocr_assets(bundle_root(), default_home())
    if not has_required_models(target):
        raise RuntimeError("Bundled local OCR models are missing or incomplete")
    return target


def _initialize_paddle_ocr(models_root: Path) -> None:
    """Load the local OCR engine so the release smoke test covers bundled imports."""
    try:
        from paddleocr import PaddleOCR
    except ImportError as exc:
        raise RuntimeError(f"Bundled PaddleOCR runtime is unavailable: {exc}") from exc
    detector, recognizer, classifier = required_model_paths(models_root)
    try:
        PaddleOCR(
            use_angle_cls=True,
            lang="ch",
            show_log=False,
            det_model_dir=str(detector),
            rec_model_dir=str(recognizer),
            cls_model_dir=str(classifier),
        )
    except Exception as exc:
        raise RuntimeError(f"Bundled PaddleOCR engine failed to initialize: {exc}") from exc


def run_smoke_test() -> str:
    """Verify the packaged application can create its local Flask UI without a GUI."""
    models_root = configure_release_assets()
    _initialize_paddle_ocr(models_root)
    response = create_materials_app().test_client().get("/")
    if response.status_code != 200:
        raise RuntimeError(f"Desktop application smoke request failed: {response.status_code}")
    return LOCAL_MATERIALS_SMOKE_OK


def _start_desktop(app) -> None:
    try:
        import webview
    except ImportError:
        webbrowser.open("http://127.0.0.1:5010")
        app.run(host="127.0.0.1", port=5010, debug=False)
        return
    server = make_server("127.0.0.1", 0, app)
    worker = Thread(target=server.serve_forever, daemon=False)
    worker.start()
    window = webview.create_window(
        "大创报销材料助手",
        f"http://127.0.0.1:{server.server_port}/",
        width=1280,
        height=860,
        min_size=(980, 680),
    )
    window.events.closed += server.shutdown
    webview.start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch the local reimbursement materials assistant.")
    parser.add_argument("--smoke", action="store_true", help="Verify the packaged app without a GUI.")
    parser.add_argument(
        "--ocr-worker",
        nargs=2,
        metavar=("INPUT", "OUTPUT"),
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    try:
        if args.ocr_worker:
            if getattr(sys, "frozen", False):
                configure_release_assets()
            return run_worker(*args.ocr_worker)
        if args.smoke:
            print(run_smoke_test())
            return 0
        if getattr(sys, "frozen", False):
            configure_release_assets()
        _start_desktop(create_materials_app())
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
