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
    "yindun.core.permission": ["permission_mode", "base_decision", "stricter"],
    "yindun.app.agent_service": ["_warmup_llm", "impl_notice", "set_session_permission",
                                 # 密钥「留空＝沿用已存」（V3.6.7）：这两个符号缺任何一个，
                                  # 说明打进包里的还是"空串覆盖密钥"的旧逻辑
                                 "_stored_api_key", "kept_key"],
    "yindun.app.laya_service": ["probe_lan", "probe_local"],
    # 公网模型接入（V3.6.4）：地址归一化与多候选探测必须真的在包里
    "yindun.app.llm_factory": ["normalize_base_url", "probe_candidates", "probe_endpoint"],
    "yindun.plugins.host": ["module_attr", "preload"],
    "yindun.utils.logger": ["setup_logging"],
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
    problems += _check_version(zr, __display_version__)
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
