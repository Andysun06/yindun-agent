# -*- mode: python ; coding: utf-8 -*-
# 隐盾安全智能体 PyInstaller 打包配置（onedir 便携目录形态，windowed）
#
# 主推形态是"免安装单文件 exe"：见 tools/yindun_agent_portable.spec + tools/build_portable.py
# 本文件保留为备选（onedir：目录内多文件，双击主 exe，启动更快）：
#   pyinstaller tools\yindun_agent.spec --noconfirm    （产物在 dist/YindunSecurityAgent/）
# 注意：tiktoken 已不再是本项目依赖（token 计数为内置字符估算），故不再收集。
from PyInstaller.utils.hooks import collect_all, collect_submodules
import os

datas = []
binaries = []
hiddenimports = []

for pkg in [
    "chromadb",
    "langchain_core",
    "langchain_text_splitters",
    "langchain_ollama",
    "langchain_openai",
    "langchain_chroma",
    "langchain",
    "onnxruntime",
    "pydantic",
    "PyPDF2",
    "pymupdf",
    "pdfplumber",
    "docx",
    "openpyxl",
    "PIL",
    "huggingface_hub",
]:
    try:
        d, b, h = collect_all(pkg)
    except Exception as exc:
        print(f"[spec] 跳过可选依赖 {pkg}: {exc}")
        continue
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("yindun")
# ★ 路径基准锚定仓库根目录（spec 内的相对路径以 spec 所在目录为基准，故必须用绝对路径）
_ROOT = os.path.normpath(os.path.join(SPECPATH, ".."))
datas += [
    # ★ Web 前端资源必须打进包：冻结后前端由 file:// 从这里加载
    (os.path.join(_ROOT, "yindun", "app", "web"), os.path.join("yindun", "app", "web")),
    # ★ 内置插件必须打进包：插件按【文件路径】加载，PyInstaller 不会自动收集
    (os.path.join(_ROOT, "yindun", "plugins", "builtin"), os.path.join("yindun", "plugins", "builtin")),
    (os.path.join(_ROOT, "docs", "使用说明.pdf"), "."),
    (os.path.join(_ROOT, "docs", "插件开发.md"), "."),
    (os.path.join(_ROOT, "assets", "demo_contract.txt"), "assets"),
    (os.path.join(_ROOT, "assets", "知识库演示文档"), os.path.join("assets", "知识库演示文档")),
]

a = Analysis(
    [os.path.join(_ROOT, "run.py")],
    pathex=[_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["PySide6", "shiboken6", "tkinter", "torch", "tensorflow",
              "transformers", "tokenizers", "safetensors", "hf_xet",
              "sentence_transformers", "accelerate", "IPython", "jupyter"],
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
