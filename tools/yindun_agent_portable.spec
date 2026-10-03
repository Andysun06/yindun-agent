# -*- mode: python ; coding: utf-8 -*-
"""
隐盾安全智能体 · 免安装单文件（onefile）PyInstaller 配置

目标：产出一个 exe，双击即用；运行时配置/会话/审计/知识库数据写在 exe 同目录
      （冻结态下 yindun.APP_ROOT = exe 所在目录），主密钥仍由 DPAPI 保护在 %APPDATA%。

构建入口：python tools/build_portable.py
备选（安装包形态）：tools/yindun_agent.spec（onedir）+ tools/installer.nsi

注意（环境依赖，不进包）：Ollama 需用户自行安装，并至少准备一个支持工具调用的模型。
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules
import os

# ★ 路径基准必须锚定"仓库根目录"：PyInstaller 解析 spec 内的相对路径时，
#   基准是 spec 文件所在目录（tools/），不是当前工作目录。
_ROOT = os.path.normpath(os.path.join(SPECPATH, ".."))
_ENTRY = os.path.join(_ROOT, "run.py")

datas = []
binaries = []
hiddenimports = []

# ── 第三方库：整包收集（含数据文件与子模块），避免动态导入被裁掉 ──
for pkg in [
    "chromadb",
    "langchain",
    "langchain_core",
    "langchain_text_splitters",
    "langchain_ollama",
    "langchain_openai",
    "langchain_chroma",
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
    except Exception as exc:  # 可选依赖缺失时不让整个打包失败
        print(f"[spec] 跳过可选依赖 {pkg}: {exc}")
        continue
    datas += d
    binaries += b
    hiddenimports += h

# OCR（扫描件 PDF）在 Python >= 3.13 下不可用，存在才收
try:
    d, b, h = collect_all("rapidocr_onnxruntime")
    datas += d
    binaries += b
    hiddenimports += h
except Exception as exc:
    print(f"[spec] 跳过 OCR 依赖 rapidocr_onnxruntime: {exc}")

# ── 产品代码与随包文档 ──
hiddenimports += collect_submodules("yindun")
datas += [
    (os.path.join(_ROOT, "docs", "使用说明.pdf"), "."),   # 随包附使用说明
    (os.path.join(_ROOT, "assets", "demo_contract.txt"), "assets"),
    (os.path.join(_ROOT, "assets", "知识库演示文档"), os.path.join("assets", "知识库演示文档")),
]

a = Analysis(
    [_ENTRY],
    pathex=[_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "torch", "tensorflow", "IPython", "jupyter", "matplotlib", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

# onefile：binaries/datas 全部塞进 EXE（不生成 COLLECT 目录）
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="YindunPortable",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
