# -*- mode: python ; coding: utf-8 -*-
"""
隐盾控制面 · 免安装单文件（onefile）PyInstaller 配置

目标：产出一个 exe，双击即启动企业控制面（集中管理服务 + 集中控制台窗口），
      数据写在 exe 同目录的 control_data/（冻结态下 yindun.APP_ROOT = exe 所在目录），
      管理令牌明文只落在 control_data/admin_token.txt（数据库内只存哈希）。

构建入口：python tools/build_control.py

★ 与客户端包的关键差异（这也是它的优势）：
  控制面业务**只用 Python 标准库**（http.server + sqlite3 + hmac），不依赖 LangChain/Chroma/torch，
  因此把客户端那些重依赖全部排除；唯一保留的第三方依赖是 **pywebview**（桌面窗口宿主），
  产物仍只有几十 MB，可以单独拷到管理机上跑，不需要装 Ollama、也不需要装客户端。

注意（环境依赖，不进包）：无。控制面自身不需要任何网络依赖；对外提供服务前请自行加 TLS 反代。
"""
from PyInstaller.utils.hooks import collect_submodules
import os

# ★ 路径基准必须锚定"仓库根目录"：PyInstaller 解析 spec 内相对路径时，
#   基准是 spec 文件所在目录（tools/），不是当前工作目录。
_ROOT = os.path.normpath(os.path.join(SPECPATH, "..", ".."))
_ICON_NAME = "yindun-control.ico"   # tools/packaging/ → 仓库根
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
    # 控制面业务本身零第三方依赖（http.server + sqlite3 + hmac），客户端/Torch 那些重依赖
    # 整体排除，产物因此很小。**唯一保留的第三方依赖是 pywebview**：控制端要"双击就有一个
    # 可操作的窗口"，它负责开原生窗口承载控制台（没有它时自动退回系统浏览器）。
    # 端点侧上报器（agent_reporter）**不在**这里：它属于客户端，跑在员工机上。
    excludes=["PySide6", "shiboken6", "tkinter", "torch", "tensorflow", "transformers",
              "tokenizers", "safetensors", "hf_xet", "sentence_transformers", "accelerate",
              "scipy", "chromadb", "langchain", "langchain_core", "langchain_ollama",
              "langchain_openai", "langchain_chroma", "langchain_text_splitters",
              "onnxruntime", "rapidocr_onnxruntime", "pymupdf", "pdfplumber", "PyPDF2",
              "docx", "openpyxl", "PIL", "huggingface_hub",
              "IPython", "jupyter", "matplotlib", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

# onefile + **无控制台**（用户反馈："不希望服务端还有个命令行"）：
# 双击即一个原生控制台窗口，不再先弹一个黑命令行。代价是启动信息（监听地址/数据目录/令牌）
# 没有终端可显示，因此统一改走 `desktop.say()`（无控制台时落日志）与 `desktop.notify()`
# （启动失败弹系统对话框，写清原因与日志路径）——**不做"双击没反应"的静默失败**。
# 图标缺失时给一句人话（PyInstaller 自己抛的是裸路径异常）
_ICON_FILE = os.path.join(_ROOT, "assets", "icons", _ICON_NAME)
if not os.path.exists(_ICON_FILE):
    print("[spec] ⚠ 找不到定制图标 %s——先跑 python tools/packaging/make_icons.py"
          "（不传 icon= 会退回 PyInstaller 默认图标，产物看着像半成品）" % _ICON_FILE)
_ICON_PATH = _ICON_FILE if os.path.exists(_ICON_FILE) else None

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="YindunControl",
    # ★ 定制图标（V3.6.10，负责人要求）：不传 icon= 时 PyInstaller 会用**它自带的默认图标**，
    #   和产品毫无关系、看着就像半成品。图标由 tools/packaging/make_icons.py 生成
    #   （盾牌+对勾；控制端用管理蓝以便一眼区分两个程序）。
    icon=_ICON_PATH or str(_ROOT / "assets" / "icons" / "yindun-control.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
