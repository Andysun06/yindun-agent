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
#   基准是 spec 文件所在目录（tools/packaging/），不是当前工作目录。
_ROOT = os.path.normpath(os.path.join(SPECPATH, "..", ".."))
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
    "pptx",       # PPTX 附件解析（python-pptx）：带 templates/*.pptx 等数据文件，必须整包收集
    "openpyxl",
    "xlrd",       # 旧版 .xls（OLE/BIFF）只能靠 xlrd；缺了它"支持 xls"就是源码级空话
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

# ── OCR（扫描件 PDF + 图片识别）──────────────────────────────
# ★ 这一段以前是"静默降级"：collect_all("rapidocr_onnxruntime") 包在 try 里，装不到只 print 一行，
#   构建日志一滚就没了——结果交付出去的 exe 根本读不了图片/扫描件，却没人知道。
#   现在两个包名都试（老包元数据要求 Python <3.13；rapidocr 2.x/3.x 支持 3.13，且模型随 wheel
#   离线自带，不需要联网下载），并**无论有没有都把结论打成横幅**
#   （看日志的人 = 打包的人 = 交付的人，这行字就是"本次产物能做什么"的凭据）。
_OCR_PKGS = ["rapidocr_onnxruntime", "rapidocr"]
_ocr_collected = []
for _pkg in _OCR_PKGS:
    try:
        d, b, h = collect_all(_pkg)
    except Exception as exc:
        print(f"[spec] OCR 引擎 {_pkg} 未收集：{exc}")
        continue
    datas += d
    binaries += b
    hiddenimports += h
    _ocr_collected.append(_pkg)

if _ocr_collected:
    print("*" * 78)
    print(f"* 本次产物【含 OCR】：{'、'.join(_ocr_collected)} → 扫描件 PDF 与图片可提取文字")
    print("*" * 78)
else:
    print("*" * 78)
    print("* 本次产物【不含 OCR】：★ 图片附件与扫描件 PDF 将**读不到文字**")
    print("*   （程序会如实提示「需要 OCR 组件」，不会假装读到了内容）")
    print("*   原因：rapidocr-onnxruntime 的包元数据要求 Python <3.13（3.13 上装不上）；")
    print("*         支持 3.13 的 rapidocr 本次未安装。两者的模型都随包离线自带，不需要联网下载。")
    print("*   要支持：pip install rapidocr 后重新构建（会一并收集 onnxruntime/opencv 等依赖）")
    print("*" * 78)

# ── 产品代码与随包文档 ──
hiddenimports += collect_submodules("yindun")

# ── 插件按【文件路径】加载 → 静态分析看不见它们的 import，必须显式兜住 ──
# 根因：`plugins/host.py` 用 spec_from_file_location 从 .py 文件加载插件，PyInstaller 的
# import 图里没有 plugin.py 这一环，插件独有的 import（例如 doc_extra 用的 `html.parser`）只能
# 靠"恰好被别的模块带进来"，属于碰运气。下面的做法是**扫描**内置插件的 import 并自动加入，
# 而不是手抄一份清单（手抄会随插件增删漂移）：
#   · 正则抓 `import x` / `from x import y`（含缩进内的函数内导入）；
#   · 只收仓库内插件真实出现的模块名，运行时缺失会当场报 ModuleNotFoundError，不会静默；
#   · 另有 _ALWAYS_HIDDEN：解析器/插件都会用、但同样可能只被动态路径触达的标准库与三方模块。
_ALWAYS_HIDDEN = [
    "html", "html.parser",        # doc_extra 扩展附件解析（.eml/.html/.mht）
    "email", "email.policy", "email.parser", "email.message",
    "pptx", "pptx.parts", "pptx.opc", "pptx.chart",     # PPTX 附件解析
    "xlrd", "xlrd.biffh", "xlrd.book", "xlrd.sheet",    # 旧版 .xls
    "openpyxl", "docx", "fitz", "pdfplumber",           # 其余附件解析路径
]
hiddenimports += _ALWAYS_HIDDEN


def _plugin_imports(plugin_root):
    """扫描内置插件源码里的 import 语句，返回模块名集合（正则够用，不做 AST）。"""
    import re as _re
    found = set()
    pattern = _re.compile(r"^\s*(?:from\s+([\w\.]+)\s+import|import\s+([\w\.]+))", _re.M)
    if not os.path.isdir(plugin_root):
        return found
    for root, dirs, files in os.walk(plugin_root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".mimosa")]
        for name in files:
            if not name.endswith(".py"):
                continue
            try:
                text = open(os.path.join(root, name), "r", encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for mod in pattern.findall(text):
                mod = mod[0] or mod[1]
                if mod and not mod.startswith("."):
                    found.add(mod)
    return found


_PLUGIN_MODULES = _plugin_imports(os.path.join(_ROOT, "yindun", "plugins", "builtin"))
hiddenimports += sorted(_PLUGIN_MODULES)
print(f"[spec] 内置插件 import 扫描：加入 {len(_PLUGIN_MODULES)} 个候选模块"
      f"（{', '.join(sorted(_PLUGIN_MODULES))[:160]}…）")


def _collect_dir(src, dest, skip_dirs=(".mimosa", "__pycache__"), skip_ext=(".pyc", ".pyo")):
    """把一个目录收进 datas，并**跳过不属于本产品的垃圾**。

    为什么不能用 Tree：Tree 产出 3 元组（name, path, typecode），只适合 COLLECT；
    Analysis 的 datas 要 2 元组，混进去会以 "too many values to unpack" 失败。
    为什么要跳过：目录整包拷贝会把 .mimosa（Mimosa 安全门禁的会话状态 JSON）与
    __pycache__ 一起打进 exe——前者是别的工具的运行时数据，出现在交付物里
    既浪费体积，也让"交付内容"变得不可解释。
    """
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


datas += [
    # ★ Web 前端资源必须打进包：冻结后前端由 file:// 从这里加载
    *_collect_dir(os.path.join(_ROOT, "yindun", "app", "web"), os.path.join("yindun", "app", "web")),
    # ★ 内置插件必须打进包：插件是按【文件路径】加载的（不是 import），PyInstaller 不会自动收集；
    #   漏了会表现为"设置页里一个插件都没有"，而且不会有任何报错
    *_collect_dir(os.path.join(_ROOT, "yindun", "plugins", "builtin"), os.path.join("yindun", "plugins", "builtin")),
    (os.path.join(_ROOT, "docs", "使用说明.pdf"), "."),   # 随包附使用说明
    (os.path.join(_ROOT, "docs", "插件开发.md"), "."),    # 随包附插件开发指南
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
    # ★ 默认界面是 Web（pywebview + WebView2），Qt 只在显式 `--qt` 时才需要；
    #   这里整体排除 PySide6，便携包因此明显瘦身（旧 Qt 界面请在源码环境运行 `python run.py --qt`）。
    # ★ 另排除"可选重依赖"：本机若因接入 laya（`pip install laya`）装上了 transformers/torch 等，
    #   PyInstaller 的 hook 会把它们收进包（实测体积 133MB → 220MB），而隐盾运行时并不 import 它们；
    #   exe 也不加载 laya（环境隔离），排除后体积回到 ~130MB。
    #   scipy 同理：它经 numpy.f2py（numpy>=2.0 未被 hook 排除）被带入，+65MB；全仓与全部依赖均无
    #   scipy 引用（chromadb/onnxruntime/pymupdf 均零引用），排除不影响功能。
    excludes=["PySide6", "shiboken6", "tkinter", "torch", "tensorflow",
              "transformers", "tokenizers", "safetensors", "hf_xet",
              "sentence_transformers", "accelerate", "scipy",
              "IPython", "jupyter", "matplotlib", "pytest"],
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
