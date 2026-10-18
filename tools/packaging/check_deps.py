# -*- coding: utf-8 -*-
"""隐盾 · 依赖锁定校验 / 生成（V3.6.1）

为什么需要它（开发记忆 §7 B-8）：`requirements.txt` 里全是 `>=`（如 `langchain-ollama>=0.1.0`），
今天能打出来的 exe，明天装到的可能是另一套版本、行为未必一致。`requirements.lock` 是
**当前已验证可用**的那套精确版本（全量套件 + 双 exe 都在这套上跑过）。

用法：
    python tools/packaging/check_deps.py            # 校验：装到的版本与锁定是否一致
    python tools/packaging/check_deps.py --write     # 重新生成 requirements.lock（升级依赖后）
    python tools/packaging/check_deps.py --quiet     # 只返回退出码（CI 用）

退出码 0 = 一致；非 0 = 有漂移（逐条打印"锁的是 X，装的是 Y"）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / "requirements.lock"
REQ_PATH = ROOT / "requirements.txt"

HEADER = """# 隐盾安全智能体 · 依赖锁定（打包用可复现快照）
#
# 为什么需要它：requirements.txt 里全是 `>=`（例如 langchain-ollama>=0.1.0），
# 今天能打出来的 exe，明天装到的可能是另一套版本、行为未必一致。
# 本文件是**当前已验证可用**的那套精确版本（全量套件与双 exe 都在这套上跑过）；
# 打包与 CI 可用 `pip install -r requirements.lock` 复现同一套环境。
#
# 生成：python tools/packaging/check_deps.py --write
# 校验：python tools/packaging/check_deps.py（版本漂了会报红）
# 升级：改 requirements.txt → 装新版本 → 跑全量套件 → --write 重生成 → 重建 exe
#
"""


def installed_versions() -> dict:
    """当前环境里已安装分发包的精确版本（名称统一小写、连字符归一）。"""
    from importlib import metadata
    out = {}
    for dist in metadata.distributions():
        name = (dist.metadata.get("Name") or "").strip()
        if name:
            out[name.lower().replace("_", "-")] = dist.version
    return out


def read_lock() -> dict:
    locked = {}
    if not LOCK_PATH.exists():
        return locked
    for line in LOCK_PATH.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if "==" in text:
            name, _, version = text.partition("==")
            locked[name.strip().lower().replace("_", "-")] = version.strip()
    return locked


def direct_requirements(skip_unmatched_markers: bool = True) -> list:
    """requirements.txt 里的直接依赖名（去掉注释与版本约束）。

    带环境标记（如 `; python_version < "3.13"`）的条目：默认**跳过当前 Python 装不上的**——
    否则 `rapidocr-onnxruntime`（最高 1.4.4 只支持 <3.13）会在 3.13 环境里被误判成"漏锁"。
    """
    names = []
    if not REQ_PATH.exists():
        return names
    for line in REQ_PATH.read_text(encoding="utf-8").splitlines():
        text = line.split("#", 1)[0].strip()
        if not text or text.startswith("-"):
            continue
        marker = ""
        if ";" in text:
            text, _, marker = text.partition(";")
            marker = marker.strip()
        text = text.strip()
        if skip_unmatched_markers and marker and not _marker_applies(marker):
            continue
        for sep in (">=", "==", "<=", "~=", ">", "<"):
            if sep in text:
                text = text.split(sep, 1)[0].strip()
                break
        if text:
            names.append(text)
    return names


def _marker_applies(marker: str) -> bool:
    """判断环境标记在当前解释器上是否成立（用 packaging 判，判不了就按成立处理）。"""
    try:
        from packaging.markers import Marker
        return bool(Marker(marker).evaluate())
    except Exception:
        return True


def write_lock() -> int:
    from importlib import metadata
    lines = []
    for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata.get("Name") or "").lower()):
        name = (dist.metadata.get("Name") or "").strip()
        if name:
            lines.append(f"{name}=={dist.version}")
    # 落点受控：仓库根目录的锁定文件（由本脚本生成，人工可审）
    LOCK_PATH.write_text(HEADER + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"✅ 已更新 {LOCK_PATH}（{len(lines)} 个分发包）")
    return 0


def check(quiet: bool = False) -> int:
    locked = read_lock()
    if not locked:
        print("❌ 没有 requirements.lock：先跑 python tools/packaging/check_deps.py --write")
        return 1
    installed = installed_versions()
    problems = []
    for name, want in sorted(locked.items()):
        got = installed.get(name)
        if got is None:
            problems.append(f"缺少：{name}（锁定 {want}，环境未安装）")
        elif got != want:
            problems.append(f"版本漂移：{name} 锁定 {want}，实装 {got}")
    missing_direct = [n for n in direct_requirements()
                      if n.lower().replace("_", "-") not in locked]
    for name in missing_direct:
        problems.append(f"直接依赖未进锁定：{name}（requirements.txt 有、lock 里没有）")

    if problems:
        print(f"❌ 依赖锁定不一致（{len(problems)} 项）：")
        for item in problems:
            print("   ·", item)
        print("\n处理：装齐锁定版本，或确认后跑 --write 重新生成并跑全量套件。")
        return 1
    if not quiet:
        print(f"✅ 依赖锁定一致：{len(locked)} 个分发包全部与 requirements.lock 吻合"
              f"（直接依赖 {len(direct_requirements())} 项都在锁定内）")
    return 0


def main(argv) -> int:
    if "--write" in argv:
        return write_lock()
    return check(quiet="--quiet" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
