import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.local_ocr_assets import (
    bundle_root,
    bundled_inventory_templates_path,
    bundled_ocr_models_path,
    configure_bundled_ocr_assets,
    has_required_models,
    required_model_paths,
)

try:
    from tools.prepare_local_ocr_assets import prepare_local_ocr_assets
except ModuleNotFoundError:
    prepare_local_ocr_assets = None


class LocalOcrAssetsTests(unittest.TestCase):
    def test_asset_preparation_cli_runs_from_the_project_root(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "assets" / "ocr_models"
            self._write_complete_models(source)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/prepare_local_ocr_assets.py",
                    "--source",
                    str(source),
                    "--target",
                    str(target),
                ],
                capture_output=True,
                check=False,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(has_required_models(target))

    def test_portable_build_script_prepares_assets_and_uses_the_onedir_spec(self):
        script_path = Path("scripts/build_materials_portable.bat")
        self.assertTrue(script_path.is_file())
        script = script_path.read_text(encoding="utf-8")

        self.assertIn("install_local_ocr.bat", script)
        self.assertIn("tools\\prepare_local_ocr_assets.py", script)
        self.assertIn("packaging\\materials_desktop.spec", script)
        self.assertNotIn("--onefile", script)
        self.assertTrue(script.isascii())

    def test_portable_verify_script_checks_embedded_templates_and_runs_the_smoke_test(self):
        script_path = Path("scripts/verify_materials_portable.bat")
        self.assertTrue(script_path.is_file())
        script = script_path.read_text(encoding="utf-8")

        self.assertIn("--smoke", script)
        self.assertIn("LOCAL_MATERIALS_SMOKE_OK", script)
        self.assertIn("inventory_templates", script)
        self.assertIn("TEMPLATE_COUNT", script)
        self.assertNotIn("run_materials_desktop.py", script)
        self.assertNotIn("pip ", script)
        self.assertTrue(script.isascii())

    def test_legacy_build_script_delegates_to_the_portable_build(self):
        script = Path("scripts/build_materials_exe.bat").read_text(encoding="utf-8")

        self.assertIn("build_materials_portable.bat", script)
        self.assertNotIn("--onefile", script)

    def test_portable_spec_is_onedir_and_bundles_local_models(self):
        spec_path = Path("packaging/materials_desktop.spec")
        self.assertTrue(spec_path.is_file())
        spec = spec_path.read_text(encoding="utf-8")

        self.assertIn("COLLECT(", spec)
        self.assertIn("'assets' / 'ocr_models'", spec)
        self.assertIn("collect_all(", spec)
        self.assertIn("'paddle'", spec)
        self.assertIn("'paddleocr'", spec)
        self.assertIn("paddle.jit.sot", spec)
        self.assertIn("collect_data_files('paddle'", spec)
        self.assertIn("collect_dynamic_libs('paddle'", spec)
        self.assertNotIn("onefile=True", spec)

    def test_asset_preparation_copies_only_the_complete_required_model_tree(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "assets" / "ocr_models"
            self._write_complete_models(source)
            (source / "unexpected" / "model.bin").parent.mkdir(parents=True)
            (source / "unexpected" / "model.bin").write_bytes(b"not bundled")

            self.assertIsNotNone(prepare_local_ocr_assets)
            prepared = prepare_local_ocr_assets(source, target)

            self.assertEqual(prepared, target.resolve())
            self.assertTrue(has_required_models(target))
            self.assertFalse((target / "unexpected").exists())

    def test_asset_preparation_rejects_incomplete_source_without_creating_target(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "assets" / "ocr_models"
            required_model_paths(source)[0].mkdir(parents=True)

            self.assertIsNotNone(prepare_local_ocr_assets)
            with self.assertRaisesRegex(ValueError, "complete PaddleOCR models"):
                prepare_local_ocr_assets(source, target)
            self.assertFalse(target.exists())

    def test_asset_preparation_replaces_existing_complete_target(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "assets" / "ocr_models"
            self._write_complete_models(source)
            self._write_complete_models(target)
            stale_file = target / "stale-model.txt"
            stale_file.write_text("old", encoding="utf-8")

            self.assertIsNotNone(prepare_local_ocr_assets)
            prepare_local_ocr_assets(source, target)

            self.assertTrue(has_required_models(target))
            self.assertFalse(stale_file.exists())

    def test_bundle_paths_locate_models_below_the_bundle_root(self):
        with patch("core.local_ocr_assets.bundle_root", return_value=Path("C:/bundle")):
            self.assertEqual(bundled_ocr_models_path(), Path("C:/bundle/ocr_models"))

    def test_bundle_paths_locate_inventory_templates_below_the_bundle_root(self):
        with patch("core.local_ocr_assets.bundle_root", return_value=Path("C:/bundle")):
            self.assertEqual(
                bundled_inventory_templates_path(),
                Path("C:/bundle/inventory_templates"),
            )

    def test_source_tree_bundle_paths_fall_back_to_assets_directory(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            models = root / "assets" / "ocr_models"
            templates = root / "assets" / "inventory_templates"
            models.mkdir(parents=True)
            templates.mkdir(parents=True)

            with patch("core.local_ocr_assets.bundle_root", return_value=root):
                self.assertEqual(bundled_ocr_models_path(), models)
                self.assertEqual(bundled_inventory_templates_path(), templates)

    def test_frozen_onedir_bundle_root_uses_the_pyinstaller_internal_directory(self):
        with TemporaryDirectory() as directory:
            release_dir = Path(directory) / "release"
            executable = release_dir / "澶у垱鎶ラ攢鏉愭枡鍔╂墜.exe"
            internal_root = release_dir / "_internal"
            with patch(
                "core.local_ocr_assets.sys",
                SimpleNamespace(
                    frozen=True,
                    executable=str(executable),
                    _MEIPASS=str(internal_root),
                ),
            ):
                self.assertEqual(bundle_root(), internal_root)

    def test_required_model_paths_describe_the_complete_bundled_model_tree(self):
        root = Path("models")
        self.assertEqual(required_model_paths(root), (
            root / "whl" / "det" / "ch" / "ch_PP-OCRv4_det_infer",
            root / "whl" / "rec" / "ch" / "ch_PP-OCRv4_rec_infer",
            root / "whl" / "cls" / "ch_ppocr_mobile_v2.0_cls_infer",
        ))

    def test_required_models_need_both_inference_files_in_every_model_directory(self):
        with TemporaryDirectory() as directory:
            models = Path(directory)
            for model_path in required_model_paths(models):
                model_path.mkdir(parents=True)

            self.assertFalse(has_required_models(models))

            for model_path in required_model_paths(models):
                (model_path / "inference.pdmodel").write_bytes(b"model")
                (model_path / "inference.pdiparams").write_bytes(b"parameters")

            self.assertTrue(has_required_models(models))

    def test_configure_copies_bundled_models_only_when_source_is_complete(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            source_models = bundle / "ocr_models"
            for model_path in required_model_paths(source_models):
                model_path.mkdir(parents=True)
                (model_path / "inference.pdmodel").write_bytes(b"model")
                (model_path / "inference.pdiparams").write_bytes(b"parameters")
            home = root / "home"

            target = configure_bundled_ocr_assets(bundle, home)

            self.assertEqual(target, home / "ocr_models")
            self.assertEqual(os.environ["PADDLE_OCR_BASE_DIR"], str(target))
            self.assertEqual(os.environ["PADDLEOCR_HOME"], str(target))
            self.assertEqual(
                (target / "whl" / "det" / "ch" / "ch_PP-OCRv4_det_infer" / "inference.pdmodel").read_bytes(),
                b"model",
            )

    def test_configure_keeps_the_user_selected_home_without_an_explicit_override(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            source_models = bundle / "ocr_models"
            self._write_complete_models(source_models)
            user_home = root / "鍚庡嫟"

            with patch.dict(
                os.environ,
                {"RM_MATERIALS_OCR_HOME": "", "PUBLIC": str(root / "public")},
                clear=False,
            ):
                target = configure_bundled_ocr_assets(bundle, user_home)

            self.assertEqual(target, user_home / "ocr_models")
            self.assertTrue(has_required_models(target))

    def test_configure_uses_explicit_ocr_cache_override(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            source_models = bundle / "ocr_models"
            self._write_complete_models(source_models)
            configured_home = root / "ascii-cache"

            with patch.dict(
                os.environ, {"RM_MATERIALS_OCR_HOME": str(configured_home)}, clear=False
            ):
                target = configure_bundled_ocr_assets(bundle, root / "鍚庡嫟")

            self.assertEqual(target, configured_home / "ocr_models")
            self.assertTrue(has_required_models(target))

    def test_configure_does_not_overwrite_existing_local_models_or_copy_missing_source(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            (bundle / "ocr_models" / "whl").mkdir(parents=True)
            (bundle / "ocr_models" / "whl" / "model.bin").write_bytes(b"bundled")
            home = root / "home"
            existing = home / "ocr_models" / "whl" / "model.bin"
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"local")

            configure_bundled_ocr_assets(bundle, home)
            self.assertEqual(existing.read_bytes(), b"local")

            empty_home = root / "empty-home"
            configure_bundled_ocr_assets(root / "missing-bundle", empty_home)
            self.assertEqual(os.environ["PADDLEOCR_HOME"], str(empty_home / "ocr_models"))
            self.assertFalse((empty_home / "ocr_models").exists())

    def test_configure_does_not_copy_an_incomplete_bundled_model_tree(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            partial_model = required_model_paths(bundle / "ocr_models")[0]
            partial_model.mkdir(parents=True)
            home = root / "home"

            configure_bundled_ocr_assets(bundle, home)

            self.assertFalse((home / "ocr_models").exists())

    @staticmethod
    def _write_complete_models(root: Path) -> None:
        for model_path in required_model_paths(root):
            model_path.mkdir(parents=True)
            (model_path / "inference.pdmodel").write_bytes(b"model")
            (model_path / "inference.pdiparams").write_bytes(b"parameters")
