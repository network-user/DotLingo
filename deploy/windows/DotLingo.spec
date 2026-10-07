# -*- mode: python ; coding: utf-8 -*-
from importlib.util import find_spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


project_root = Path(SPECPATH).resolve().parents[1]
src_root = project_root / "src"
icon = src_root / "dotlingo" / "assets" / "app_icon.ico"

if find_spec("llama_cpp") is None:
    raise RuntimeError("llama-cpp-python is required in the build environment; run deploy/windows/build.ps1")
if find_spec("pypdf") is None:
    raise RuntimeError("pypdf is required in the build environment; run deploy/windows/build.ps1")
if find_spec("winocr") is None:
    raise RuntimeError("winocr is required in the build environment; run deploy/windows/build.ps1")

llama_datas, llama_binaries, llama_hidden = collect_all("llama_cpp")
pypdf_datas, pypdf_binaries, pypdf_hidden = collect_all("pypdf")
docx_datas, docx_binaries, docx_hidden = collect_all("docx")
webview_datas, webview_binaries, webview_hidden = collect_all("webview")
clr_datas, clr_binaries, clr_hidden = collect_all("clr_loader")
pythonnet_datas, pythonnet_binaries, pythonnet_hidden = collect_all("pythonnet")
winocr_datas, winocr_binaries, winocr_hidden = collect_all("winocr")

datas = (
    llama_datas
    + pypdf_datas
    + docx_datas
    + webview_datas
    + clr_datas
    + pythonnet_datas
    + winocr_datas
    + [
        (str(src_root / "dotlingo" / "models.json"), "dotlingo"),
        (str(src_root / "dotlingo" / "assets"), "dotlingo/assets"),
        (str(src_root / "dotlingo" / "web"), "dotlingo/web"),
    ]
)
binaries = (
    llama_binaries
    + pypdf_binaries
    + docx_binaries
    + webview_binaries
    + clr_binaries
    + pythonnet_binaries
    + winocr_binaries
)
hiddenimports = (
    llama_hidden
    + pypdf_hidden
    + docx_hidden
    + webview_hidden
    + clr_hidden
    + pythonnet_hidden
    + winocr_hidden
    + [
        "winocr",
        "winrt.windows.media.ocr",
        "winrt.windows.globalization",
        "winrt.windows.storage.streams",
        "winrt.windows.graphics.imaging",
        "winrt.windows.foundation",
        "winrt.windows.foundation.collections",
        "winrt.system",
        "dotlingo.image_layout",
    ]
    + [
        "dotlingo.app",
        "dotlingo.api",
        "dotlingo.hardware",
        "dotlingo.model_download",
        "dotlingo.task_queue",
        "bottle",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
    ]
)

a = Analysis(
    [str(src_root / "dotlingo" / "__main__.py")],
    pathex=[str(src_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PySide6", "PyQt6", "PyQt5", "torch", "transformers", "jupyter"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DotLingo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DotLingo",
)
