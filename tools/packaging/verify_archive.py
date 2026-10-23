# -*- coding: utf-8 -*-
"""隐盾 · exe 归档校验（"跑的就是打包进去的那份"）

为什么需要它：源码改了、exe 还是旧构建，是本项目最常见的假绿灯来源——
V3.5.9/V3.6.0 两次都是靠"exe 内文件与源码逐字节比对"才当场抓出来。
以前这段逻辑只写在 `tests/test_plugins_live.py` §8 里、且只覆盖插件目录；
现在抽成一份工具，本地与 CI 共用（CI 里改前端/插件却没重建 exe 会直接报红）。

校验四件事（都基于 PyInstaller 归档，不解包到磁盘）：
  1. **内置插件**文件与源码逐字节一致（含 manifest/词表等 .json）；
  2. **前端资源**（`yindun/app/web/*`、控制台 `yindun/control/web/*`）与源码逐字节一致——
     界面改动最容易忘记重建 exe，这一项专治它；
  3. **关键模块符号**在归档的 PYZ 里存在（用字节码里的名字/常量判断，不解包执行）——
     例如工作流的真实 handler、知识库编号无关还原、启动预热、权限四档；
  4. **版本号**：归档里的 `yindun` 模块带有当前 `__display_version__`（防"exe 是旧版本"）。

用法：
    python tools/packaging/verify_archive.py            # 校验客户端 exe（默认）
    python tools/packaging/verify_archive.py --control  # 顺带校验控制端 exe
    python tools/packaging/verify_archive.py --all      # 两者都校验
退出码 0 = 全部一致；非 0 = 有不一致（逐条打印）。

也可当模块用（测试里不启子进程，直接函数调用）：
    from verify_archive import verify_client, verify_control
    problems = verify_client(exe_path, source_root)
"""
from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path
from typing import Dict, List, Sequence, Set, Tuple

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

DATA_SUFFIXES = {".py", ".json", ".js", ".css", ".html", ".svg", ".txt", ".md"}

# 关键模块 → 必须能在归档里找到的符号（新增能力时把符号加进来，这条链就自动跟着守）
CLIENT_SYMBOLS: Dict[str, Sequence[str]] = {
    "yindun.core.workflow": ["_h_fetch_data", "_h_clean_data", "_h_analyze_data",
                             "_h_generate_chart", "_h_human_review", "_default_executor",
                             "has_execution_handler", "wf_data_pipeline", "workflow_outputs"],
    "yindun.core.privacy_engine": ["restore_kb_placeholders"],
    "yindun.core.permission": ["permission_mode", "base_decision", "stricter",
                              # 默认可读写范围（V3.6.8）＝程序目录 + 桌面/文档/下载：
                              # 这两个符号缺任何一个，说明包里还是"只有程序目录"的旧范围
                              "sandbox_roots", "sandbox_scope_text"],
    "yindun.app.agent_service": ["_warmup_llm", "impl_notice", "set_session_permission",
                                 # 密钥「留空＝沿用已存」（V3.6.7）：这两个符号缺任何一个，
                                  # 说明打进包里的还是"空串覆盖密钥"的旧逻辑
                                 "_stored_api_key", "kept_key"],
    "yindun.app.laya_service": ["probe_lan", "probe_local"],
    # 公网模型接入（V3.6.4）：地址归一化与多候选探测必须真的在包里
    "yindun.app.llm_factory": ["normalize_base_url", "probe_candidates", "probe_endpoint"],
    "yindun.plugins.host": ["module_attr", "preload"],
    # 工作线程的工具面（V3.6.8）：文件管理族与"路径是文件→取父目录"的收敛必须在包里，
    # 否则用户拿到的是"能看见工具名、实际调不通"的旧行为
    "yindun.worker.agent_worker": ["_as_directory_hint", "_range_notice", "_verify_tool_outcome",
                                   # 用途红线 / 知识库范围 / 长回答续写（V3.6.13）
                                   "_looks_like_kb_question", "_hit_length_limit",
                                   "_guard_length_limit",
                                   # 明确删除指令在任何 LLM 之前由宿主执行（V3.6.21 热修）
                                   "_forced_delete_target", "_run_forced_delete"],
    # 请求用途红线（V3.6.13）：这个模块缺了，包里的智能体就会把"卖个人信息"当普通请求规划
    "yindun.core.usage_policy": ["assess_request", "_has_pii", "_has_profit", "_has_exfil"],
    "yindun.utils.logger": ["setup_logging"],
    # ★ 插件是**按文件路径动态加载**的（plugins/host.py 的 spec_from_file_location），
    #   PyInstaller 的静态分析进不去那些 plugin.py —— 插件独有的 import 只能靠"碰巧被别的模块带进来"。
    #   一旦漏了，源码跑得好好的、exe 里插件自检报 ModuleNotFoundError（组员审查报告里的真实案例）。
    #   这四条就是插件与解析器的独有依赖：它们必须在包里，缺了就在这里报红。
    "html.parser": ["HTMLParser"],
    "email.policy": ["EmailPolicy"],
    "pptx": ["Presentation"],
    "xlrd": ["open_workbook"],
    # 附件解析能力（V3.6.8）：PPTX 分支 / 旧版 .xls 分支 / 图片 OCR / 失败判定必须真的在包里。
    # 这些符号缺任何一个，都说明打进包的是"图片与 pptx 读不出内容、且失败被当成正文"的旧解析器。
    # 文件管理族（V3.6.8）：五个工具完全缺失 → 用户一句"帮我批量改后缀"就做不了
    "yindun.core.file_tools": ["move_local_file", "copy_local_file",
                               "rename_local_file", "batch_rename_local_files",
                               "make_local_dir", "_plan_transfer"],
    "yindun.utils.document_parser": ["_html_to_text", "_HtmlTextExtractor",   # HTML 去标签（V3.6.8）
                                     "PptxParser", "is_parse_failure", "_extract_xls",
                                     "_extract_image", "_ocr_engine"],
}

CONTROL_SYMBOLS: Dict[str, Sequence[str]] = {
    "yindun.control.server": ["display_version", "render_static"],
    "yindun.control.models": ["_TS_RE"],
    "yindun.control.agent_reporter": ["_iso"],
    "yindun.control.store": ["ensure_admin_token"],
    "yindun.control.policy": ["merge_tighten", "is_tightening_only"],
}


# ── 归档读取 ──────────────────────────────────────────────
def _load_archives(exe: Path):
    """打开 PyInstaller onefile：返回 (CArchiveReader, ZlibArchiveReader)。"""
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    reader = CArchiveReader(str(exe))
    pyz = next((n for n in reader.toc if n.lower().endswith(".pyz")), "")
    if not pyz:
        raise RuntimeError(f"归档里找不到 PYZ：{exe}")
    tmp_dir = Path(tempfile.mkdtemp(prefix="yindun_verify_"))
    tmp = tmp_dir / "PYZ.pyz"
    # 落点受控：系统临时目录（本工具自己创建），仅用于把 PYZ 交给 PyInstaller 的读取器
    tmp.write_bytes(reader.extract(pyz))
    return reader, ZlibArchiveReader(str(tmp))


def _carchive_files(reader) -> Dict[str, str]:
    return {n.replace("\\", "/"): n for n in reader.toc}


def _harvest(code) -> Tuple[Set[str], str]:
    """把一个模块的字节码里出现的所有名字与字符串常量收出来（不解包执行）。"""
    names: Set[str] = set()
    texts: List[str] = []
    stack = [code]
    while stack:
        cur = stack.pop()
        names.add(getattr(cur, "co_name", ""))
        names.update(getattr(cur, "co_names", ()))
        names.update(getattr(cur, "co_varnames", ()))
        for item in getattr(cur, "co_consts", ()):
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, types.CodeType):
                stack.append(item)
    return names, "\n".join(texts)


def _plugin_import_modules(src_root: Path) -> Set[str]:
    """扫描内置插件源码，收集它们 import 的**非本仓库**模块名（去重后的顶层名）。

    为什么需要它（组员审查报告的真实案例）：插件是**按文件路径动态加载**的
    （`plugins/host.py` 里 `spec_from_file_location`），PyInstaller 的静态分析进不去
    `plugin.py`，插件独有的 import（例如 `doc_extra` 用的 `html.parser`）只能"碰巧"被别的
    模块带进包。构建脚本会主动收集（见 spec），这里**独立复核一遍**——两个实现各自扫源码，
    一个坏了另一个还能报出来。

    只收集"第三方/标准库"模块：跳过相对导入与本仓库自己的 `yindun*`（它们由
    `collect_submodules("yindun")` 负责）。
    """
    import ast

    tolerated = {"ImportError", "ModuleNotFoundError", "Exception"}

    def _optional_imports(tree) -> Set[int]:
        """被 try/except（捕 ImportError / Exception）包住的 import 节点：**可选依赖**。

        判据取自源码结构而不是一份白名单：插件把"本机没装也能降级"的依赖写成
        `try: from laya import Router / except Exception: 回退`，这类 import 缺了不算缺陷
        （laya 就是这种：装了用模型分级，没装回退正则）。白名单会随插件增删漂移，
        结构判据不会。
        """
        marked: Set[int] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            # ★ 只认异常**类型**（`h.type`）：`except Exception as exc:` 里的 `h.name` 是绑定名 "exc"，
            #   早先写成 `h.name or h.type.id` 会把它当成捕获类型 → 所有"忘了带类型"的处理器
            #   都会被误判成"没捕获 ImportError"，可选依赖因此漏检（实测：laya 没被识别成可选）。
            catches = {h.type.id for h in node.handlers
                       if isinstance(getattr(h, "type", None), ast.Name)}
            if not (catches & tolerated):
                continue
            for child in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                marked.add(id(child))
        return marked

    found: Set[str] = set()
    plugin_root = src_root / "yindun" / "plugins" / "builtin"
    for path in sorted(plugin_root.glob("*/*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        optional = _optional_imports(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:                  # 相对导入：插件自己在包里
                    continue
                names = [node.module or ""]
            else:
                continue
            if id(node) in optional:
                continue
            for name in names:
                top = (name or "").split(".")[0]
                if not top or top.startswith("yindun") or top == "__future__":
                    continue
                found.add(top)
                found.add(name)                 # 子模块也留一份（PyInstaller 会按需收集）
    return found


def _module_symbols(zr, module: str) -> Tuple[Set[str], str]:
    entry = next((n for n in zr.toc if n == module), None)
    if entry is None:
        return set(), ""
    code = zr.extract(entry)
    if isinstance(code, (bytes, bytearray)):
        text = code.decode("utf-8", "replace")
        return set(), text
    return _harvest(code)


# ── 校验项 ────────────────────────────────────────────────
def _check_data_dir(reader, src_dir: Path, prefix: str, skip_dirs: Sequence[str] = ()) -> List[str]:
    """`src_dir` 下的文本类文件必须与归档内 `prefix/...` 逐字节一致。

    默认跳过 `.mimosa` 与 `__pycache__`：这两个是运行时产物（安全门禁的 hook 状态、
    Python 字节码缓存），本来就不该进包，源码树里出现它们不算"归档缺文件"。
    """
    problems: List[str] = []
    files = _carchive_files(reader)
    skip = tuple(skip_dirs) + (".mimosa", "__pycache__")
    compared = 0
    for path in sorted(src_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in DATA_SUFFIXES:
            continue
        if any(part in skip for part in path.parts):
            continue
        key = prefix + "/" + path.relative_to(src_dir).as_posix()
        entry = files.get(key)
        if entry is None:
            problems.append(f"归档缺少文件：{key}")
            continue
        blob = reader.extract(entry)
        text = blob.decode("utf-8", "replace") if isinstance(blob, (bytes, bytearray)) else str(blob)
        if text.replace("\r\n", "\n") != path.read_text(encoding="utf-8").replace("\r\n", "\n"):
            problems.append(f"与源码不一致（源码比 exe 新？）：{key}")
        compared += 1
    if compared == 0:
        problems.append(f"没有比到任何文件：{prefix}（路径写错了？）")
    return problems


def _check_symbols(zr, symbols: Dict[str, Sequence[str]], label: str) -> List[str]:
    problems: List[str] = []
    for module, wanted in symbols.items():
        names, text = _module_symbols(zr, module)
        if not names and not text:
            problems.append(f"{label}：归档里找不到模块 {module}")
            continue
        missing = [w for w in wanted if w not in names and w not in text]
        if missing:
            problems.append(f"{label}：{module} 缺少符号 {missing}（exe 是旧构建？）")
    return problems


def _check_version(zr, version: str) -> List[str]:
    names, text = _module_symbols(zr, "yindun")
    if not names and not text:
        return ["归档里找不到 yindun 包模块"]
    if version not in text:
        return [f"归档里的版本号不是 {version}（exe 为旧版本构建？）"]
    return []


def _exe_path(kind: str, version: str) -> Path:
    name = ("隐盾安全智能体_" if kind == "client" else "隐盾控制端_") + f"{version}_便携版.exe"
    return _SRC / "dist" / name


def _stdlib_zip_modules(reader) -> Set[str]:
    """`base_library.zip` 里的模块名（PyInstaller 6 把纯 Python 标准库放这儿，不在 PYZ 里）。

    不查这里就会把 `re` 这种"明明在包里、只是不落 PYZ"的模块报成缺失（实测踩过一次）。
    """
    names: Set[str] = set()
    entry = next((n for n in reader.toc if n.lower().endswith("base_library.zip")), "")
    if not entry:
        return names
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(reader.extract(entry))) as zf:
            for item in zf.namelist():
                head = item.split("/")[0]
                names.add(head[:-4] if head.endswith(".pyc") else head)
                names.add(item[:-4].replace("/", ".") if item.endswith(".pyc") else "")
    except Exception:
        pass
    names.discard("")
    return names


# 客户端 exe 必须带的**能力模块**（缺一个就少一类附件能力，且往往只在真机才暴露）
CLIENT_REQUIRED_MODULES = (
    "winrt._winrt_windows_media_ocr",      # Windows 内置 OCR（图片/扫描件）
    "winrt.windows.media.ocr",
    "winrt.windows.graphics.imaging",
)


def _check_required_modules(reader, zr, names) -> List[str]:
    """这些模块必须真的在包里（按三处查找：PYZ / base_library.zip / 内建）。

    为什么单独列一份：它们是**扩展模块**（.pyd），`_check_symbols` 那套"读字节码找符号"用不上，
    只能查存在性。缺了的表现是"附件里图片/扫描件静默读不出字"，而构建日志一切正常——
    所以必须在归档校验里钉住。
    """
    import sys as _sys
    builtin_names = set(_sys.builtin_module_names)
    pyz_names = set(getattr(zr, "toc", {}) or {})
    stdlib_names = _stdlib_zip_modules(reader)

    def _present(name: str) -> bool:
        top = name.split(".")[0]
        return (name in builtin_names or top in builtin_names
                or name in pyz_names or top in pyz_names
                or name in stdlib_names or top in stdlib_names)

    missing = [n for n in names if not _present(n)]
    if not missing:
        return []
    return [f"客户端 exe：缺少能力模块 {missing}（图片/扫描件 OCR 会静默失效；"
            f"检查 spec 里 winrt 的 collect_submodules）"]


def _check_plugin_deps(reader, zr, source_root: Path) -> List[str]:
    """插件的 import 必须真的进了包（组员审查报告里的 `html.parser` 那一类）。

    判据：按插件源码扫出来的模块名，逐个在**三个地方**找——PYZ 模块表、`base_library.zip`
    （PyInstaller 6 的标准库落点）、解释器内建模块表。三处都没有才算"没进包"。
    这条能拦住"源码跑得好好的、exe 里插件自检报 ModuleNotFoundError"的整类问题。
    """
    modules = _plugin_import_modules(source_root)
    if not modules:
        return []
    import sys as _sys
    builtin_names = set(_sys.builtin_module_names)
    pyz_names = set(getattr(zr, "toc", {}) or {})
    stdlib_names = _stdlib_zip_modules(reader)

    def _present(name: str) -> bool:
        top = name.split(".")[0]
        return (name in builtin_names or top in builtin_names
                or name in pyz_names or top in pyz_names
                or name in stdlib_names or top in stdlib_names)

    missing = sorted(m for m in modules if not _present(m))
    if not missing:
        return []
    return [f"客户端 exe：插件依赖模块没进包 {missing}（源码能跑、exe 里会 ModuleNotFoundError；"
            f"检查 tools/packaging/yindun_agent_portable.spec 的 hiddenimports 收集）"]


def _icon_frames(ico: Path) -> Dict[tuple, "object"]:
    """把 .ico 里各尺寸帧读成 RGBA 图（{(宽,高): Image}）。"""
    from PIL import Image
    frames: Dict[tuple, object] = {}
    with Image.open(ico) as image:
        for size in sorted(set(image.info.get("sizes", [])), reverse=True):
            one = Image.open(ico)
            one.size = size
            frames[(size[0], size[1])] = one.convert("RGBA")
    return frames


def _sample_points(width: int, height: int) -> list:
    """取样点：都取在**盾牌主体内部**（避开顶部尖角与中间的对勾），颜色才稳定可比。

    第一版把点取在 (宽/2, 1) —— 那正好是盾牌顶点之外的透明区，于是永远比不中（假阴性）。
    """
    return [(width // 2, height // 3), (width // 4, height // 2),
            (width // 2, height - max(2, height // 5))]


def _check_icon(exe: Path, source_root: Path, kind: str = "client") -> List[str]:
    """exe 内嵌的图标是不是我们的**定制图标**（而不是 PyInstaller 自带的默认图标）。

    为什么要在归档校验里查这件事（V3.6.10，负责人要求「别用 python 默认的图标」）：
    源码里写了 `icon=` 不等于产物里真的有——spec 回退、图标文件被挪走都会**静默**退回默认图标，
    而「图标不对」没人会在发版前专门点开看。所以这里读 PE 资源来判。

    判据（结构感知，不是在全文件里搜字节——3 字节探针在 156MB 里必然偶然命中）：
      ① 用 pefile 取出 RT_ICON 各帧；**两种编码都要认**——实测 PyInstaller 把各尺寸帧
         统一存成 **PNG**（其他工具可能存 DIB/BMP），第一版只解析 DIB，于是把明明带着
         定制图标的产物判成了「没有」（假阴性，这次真踩到了）；
      ② 按尺寸找到 .ico 里对应的帧，在盾牌主体内部取几个点比 RGB（容差 ±8）与 alpha（±32）；
      ③ 任一帧对得上即通过。
    """
    problems: List[str] = []
    # 由调用方指定 kind：产物文件名是**中文**（隐盾控制端_…），按文件名猜"control"必然猜错
    ico = source_root / "assets" / "icons" / (
        "yindun-control.ico" if kind == "control" else "yindun-client.ico")
    if not ico.exists():
        return [f"定制图标文件缺失：{ico}（先跑 python tools/packaging/make_icons.py）"]
    try:
        import io as _io

        import pefile
        from PIL import Image
    except Exception:
        return []                     # 工具缺了就跳过（不误报；requirements 里有这两个）
    try:
        ours = _icon_frames(ico)

        pe = pefile.PE(str(exe), fast_load=True)
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        blobs = []
        for entry in getattr(pe, "DIRECTORY_ENTRY_RESOURCE", []).entries:
            if entry.id != pefile.RESOURCE_TYPE["RT_ICON"]:
                continue
            for level in entry.directory.entries:
                for leaf in level.directory.entries:
                    blobs.append(pe.get_data(leaf.data.struct.OffsetToData,
                                             leaf.data.struct.Size))
        pe.close()

        for blob in blobs:
            frame = None
            try:
                if blob[:8] == b"\x89PNG\r\n\x1a\n":                 # 实测 PyInstaller 走这条
                    frame = Image.open(_io.BytesIO(blob)).convert("RGBA")
                elif len(blob) >= 40:                                # 兼容 DIB/BMP 形态
                    width = int.from_bytes(blob[4:8], "little", signed=True)
                    height = int.from_bytes(blob[8:12], "little", signed=True) // 2
                    bits = int.from_bytes(blob[14:16], "little")
                    if bits in (24, 32) and (width, height) in ours:
                        px = bits // 8
                        stride = ((width * px + 3) // 4) * 4
                        offset = 4 + int.from_bytes(blob[0:4], "little")
                        frame = Image.new("RGBA", (width, height))
                        for y in range(height):
                            row = offset + (height - 1 - y) * stride
                            for x in range(width):
                                pos = row + x * px
                                alpha = blob[pos + 3] if px == 4 else 255
                                frame.putpixel((x, y),
                                               (blob[pos + 2], blob[pos + 1], blob[pos], alpha))
            except Exception:
                continue
            if frame is None:
                continue
            expected = ours.get(frame.size)
            if expected is None:
                continue
            matched = True
            for x, y in _sample_points(*frame.size):
                got, want = frame.getpixel((x, y)), expected.getpixel((x, y))
                if max(abs(got[i] - want[i]) for i in range(3)) > 8 or abs(got[3] - want[3]) > 32:
                    matched = False
                    break
            if matched:
                return problems

        problems.append(
            f"{exe.name}：PE 资源里找不到定制图标（{ico.name}）的对应像素 → "
            f"很可能退回了 PyInstaller 默认图标（检查 spec 的 icon= 与 assets/icons/*.ico）")
        return problems
    except Exception as exc:                                        # noqa: BLE001
        return [f"{exe.name}：图标校验失败（{type(exc).__name__}: {exc}）"]


def verify_client(exe: Path = None, source_root: Path = None) -> List[str]:
    """校验客户端 exe；返回问题列表（空 = 全部一致）。"""
    source_root = Path(source_root or _SRC)
    from yindun import __display_version__
    exe = Path(exe or _exe_path("client", __display_version__))
    if not exe.exists():
        return [f"找不到客户端 exe：{exe}（先跑 tools/packaging/build_portable.py）"]
    reader, zr = _load_archives(exe)
    problems: List[str] = []
    problems += _check_data_dir(reader, source_root / "yindun" / "plugins" / "builtin",
                               "yindun/plugins/builtin")
    problems += _check_data_dir(reader, source_root / "yindun" / "app" / "web",
                               "yindun/app/web")
    problems += _check_symbols(zr, CLIENT_SYMBOLS, "客户端 exe")
    problems += _check_required_modules(reader, zr, CLIENT_REQUIRED_MODULES)
    problems += _check_plugin_deps(reader, zr, source_root)
    problems += _check_version(zr, __display_version__)
    problems += _check_icon(exe, source_root, "client")
    return problems


def verify_control(exe: Path = None, source_root: Path = None) -> List[str]:
    """校验控制端 exe；返回问题列表（空 = 全部一致）。"""
    source_root = Path(source_root or _SRC)
    from yindun import __display_version__
    exe = Path(exe or _exe_path("control", __display_version__))
    if not exe.exists():
        return [f"找不到控制端 exe：{exe}（先跑 tools/packaging/build_control.py）"]
    reader, zr = _load_archives(exe)
    problems: List[str] = []
    problems += _check_data_dir(reader, source_root / "yindun" / "control" / "web",
                               "yindun/control/web")
    problems += _check_symbols(zr, CONTROL_SYMBOLS, "控制端 exe")
    problems += _check_version(zr, __display_version__)
    problems += _check_icon(exe, source_root, "control")
    # 控制台页面必须留占位符（版本由服务端注入；写死会在版本一致性套件里报红）
    files = _carchive_files(reader)
    entry = files.get("yindun/control/web/index.html")
    if entry is None:
        problems.append("控制端 exe 里没有控制台页面 index.html")
    else:
        blob = reader.extract(entry)
        text = blob.decode("utf-8", "replace") if isinstance(blob, (bytes, bytearray)) else str(blob)
        if "{{VERSION}}" not in text:
            problems.append("控制台页面里没有 {{VERSION}} 占位符（版本注入链路断了）")
    return problems


def _main(argv: Sequence[str]) -> int:
    from yindun import __display_version__
    want_control = "--control" in argv or "--all" in argv
    want_client = "--all" in argv or not want_control or "--client" in argv
    failed = False
    if want_client:
        print(f"== 客户端 exe（{__display_version__}）==")
        problems = verify_client()
        for line in problems:
            print("  ✗", line)
        print("  ✓ 插件 / 前端资源 / 关键符号 / 版本号 与源码一致" if not problems else "")
        failed = failed or bool(problems)
    if want_control:
        print(f"== 控制端 exe（{__display_version__}）==")
        problems = verify_control()
        for line in problems:
            print("  ✗", line)
        print("  ✓ 控制台资源 / 关键符号 / 版本号 与源码一致" if not problems else "")
        failed = failed or bool(problems)
    if failed:
        print("\n结论：归档与源码不一致——请重新打包对应 exe 后再交付。")
        return 1
    print("\n结论：exe 内资产 = 源码（跑的就是打包进去的那份）。")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
