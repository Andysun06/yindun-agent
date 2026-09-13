# -*- mode: python ; coding: utf-8 -*-
# 隐盾安全智能体 PyInstaller 打包配置（onedir，windowed）
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = []
binaries = []
hiddenimports = []

for pkg in [
    "tiktoken",
    "chromadb",
    "langchain_core",
    "langchain_text_splitters",
    "langchain_ollama",
    "langchain_openai",
    "langchain_chroma",
    "langchain",
    "onnxruntime",
    "pydantic",
]:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("yindun")

a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "torch", "tensorflow", "IPython", "jupyter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YindunSecurityAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="YindunSecurityAgent",
)
