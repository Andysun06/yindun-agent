# -*- mode: python ; coding: utf-8 -*-
"""
隐盾控制面 · 免安装单文件（onefile）PyInstaller 配置

目标：产出一个 exe，双击即启动企业控制面（集中管理服务 + 集中控制台），
      数据写在 exe 同目录的 control_data/（冻结态下 yindun.APP_ROOT = exe 所在目录），
      管理令牌明文只落在 control_data/admin_token.txt（数据库内只存哈希）。

构建入口：python tools/build_control.py

★ 与客户端包的关键差异（这也是它的优势）：
  控制面**只用 Python 标准库**（http.server + sqlite3 + hmac），不依赖 LangChain/Chroma/torch，
  因此这里把客户端那些重依赖全部排除 —— 产物是十几 MB 量级的独立小工具，
  可以单独拷到管理机上跑，不需要装 Ollama、也不需要装客户端。

注意（环境依赖，不进包）：无。控制面自身不需要任何网络依赖；对外提供服务前请自行加 TLS 反代。
"""
from PyInstaller.utils.hooks import collect_submodules
import os

# ★ 路径基准必须锚定"仓库根目录"：PyInstaller 解析 spec 内相对路径时，
#   基准是 spec 文件所在目录（tools/），不是当前工作目录。
_ROOT = os.path.normpath(os.path.join(SPECPATH, "..", ".."))   # tools/packaging/ → 仓库根
_ENTRY = os.path.join(_ROOT, "yindun", "control", "__main__.py")

def _collect_dir(src, dest, skip_dirs=(".mimosa", "__pycache__"), skip_ext=(".pyc", ".pyo")):
    """目录收进 datas 并跳过 .mimosa / __pycache__（Tree 只适合 COLLECT，datas 要 2 元组）。"""
    out = []
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in files:
            if name.endswith(skip_ext):
                continue
            full = os.path.join(root, name)
            parent = os.path.dirname(os.path.relpath(full, src))
            out.append((full, os.path.join(dest, parent) if parent else dest))
    return out


datas = [
    # ★ 控制台前端资源必须打进包：冻结后由 file:// 从包内加载（与客户端同理）
    *_collect_dir(os.path.join(_ROOT, "yindun", "control", "web"), os.path.join("yindun", "control", "web")),
    # 随包附交付文档，便于管理机现场查阅
    (os.path.join(_ROOT, "docs", "使用说明.pdf"), "."),
    (os.path.join(_ROOT, "docs", "隐盾安全智能体_技术报告.pdf"), "."),
]

hiddenimports = collect_submodules("yindun.control")

a = Analysis(
    [_ENTRY],
    pathex=[_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # 控制面零第三方依赖：把客户端与端点的重依赖整体排除，产物因此很小。
    # 端点侧上报器（agent_reporter）**不在**这里：它属于客户端，跑在员工机上。
    excludes=["PySide6", "shiboken6", "tkinter", "torch", "tensorflow", "transformers",
              "tokenizers", "safetensors", "hf_xet", "sentence_transformers", "accelerate",
              "scipy", "chromadb", "langchain", "langchain_core", "langchain_ollama",
              "langchain_openai", "langchain_chroma", "langchain_text_splitters",
              "onnxruntime", "rapidocr_onnxruntime", "pymupdf", "pdfplumber", "PyPDF2",
              "docx", "openpyxl", "PIL", "huggingface_hub", "pywebview",
              "IPython", "jupyter", "matplotlib", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

# onefile；console=True：控制面是"命令行启动、打印监听地址与令牌"的服务型工具，
# 需要把启动信息与访问日志展示给运维（关掉窗口即停服，符合"可见即可控"）。
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="YindunControl",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
