from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs


project_root = Path(SPECPATH).parent
datas = []
binaries = []
hiddenimports = []
datas += collect_data_files('paddle', include_py_files=False)
binaries += collect_dynamic_libs('paddle')
for package in ('paddleocr', 'cv2', 'pypdfium2', 'webview'):
    package_datas, package_binaries, package_hiddenimports = collect_all(
        package, on_error='ignore'
    )
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

datas += [
    (str(project_root / 'app' / 'templates'), 'app/templates'),
    (str(project_root / 'app' / 'static'), 'app/static'),
    (str(project_root / 'assets' / 'ocr_models'), 'ocr_models'),
    (str(project_root / 'assets' / 'inventory_templates'), 'inventory_templates'),
]

a = Analysis(
    [str(project_root / 'run_materials_desktop.py')],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=['paddle.jit.sot'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RMReimbursementMaterials',
    console=False,
    upx=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='RMReimbursementMaterials',
    upx=False,
)
