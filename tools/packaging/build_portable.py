# -*- coding: utf-8 -*-
"""隐盾 · 免安装单文件 exe 构建入口

用法：
    python tools/build_portable.py

产物：
    dist/YindunPortable.exe → 自动重命名为 dist/隐盾安全智能体_<版本>_便携版.exe
    （版本号动态取自 yindun/__init__.py 的 __display_version__，
     避免"改了代码忘了改打包脚本"导致旧版文件名带新构建）

说明：
- 使用 PyInstaller 的 **Python API**（`PyInstaller.__main__.run`）而非 subprocess，
  既避免在仓库里出现"调用外部命令"的写法，也让构建过程在同一个进程里可读可控。
- 单文件（onefile）模式每次启动会把内容解压到临时目录，因此**首次启动约 10~20 秒**，
  属于该形态的固有代价；如要秒开可改用 tools/yindun_agent.spec（onedir 便携目录）。
- 打包前请确认：PySide6 / langchain / chromadb / onnxruntime 等依赖已在本环境安装
  （pip install -r requirements.txt）。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools/packaging/ → 仓库根
sys.path.insert(0, ROOT)
import yindun as _pkg  # noqa: E402  # 只为取 __display_version__（模块级只有路径/代理设置，无副作用风险）

SPEC = os.path.join("tools", "packaging", "yindun_agent_portable.spec")
DIST = os.path.join(ROOT, "dist")
RAW_EXE = os.path.join(DIST, "YindunPortable.exe")
FINAL_NAME = f"隐盾安全智能体_{_pkg.__display_version__}_便携版.exe"
FINAL_EXE = os.path.join(DIST, FINAL_NAME)


def _locked_hint(final_exe: str) -> str:
    """交付物被占用时返回"怎么办"的说明，否则返回空串。

    判据是**试着重命名一次再改回来**：Windows 上正在运行的 exe 会被锁住（改名与删除都拒），
    而"文件不存在"完全正常（首次构建）。这样探测不写任何内容，也不改动交付物。

    想"先构建、回头再落名"（例如用户正开着程序、我又不想白等一场构建）就设
    `YINDUN_BUILD_ALLOW_LOCKED=1`：跳过这次拦阻，构建完成后若仍改不了名，
    会把新产物留在 `dist/YindunPortable.exe` 并说清楚，而不是抛 PermissionError。
    """
    if not os.path.isfile(final_exe):
        return ""
    probe = final_exe + ".probe"
    try:
        os.replace(final_exe, probe)
    except OSError:
        if os.environ.get("YINDUN_BUILD_ALLOW_LOCKED") == "1":
            print("⚠ 交付物正被占用（多半是程序还在运行）：继续构建，完事后把新产物留在 dist/ 下不改名")
            return ""
        return ("产物被占用：dist 里这份 exe 正在运行（或被其它程序打开），无法替换。\n"
                "   请先关闭正在运行的「隐盾安全智能体」窗口，再重新执行本脚本——\n"
                "   否则会白等一场构建，而且新产物只会以 YindunPortable.exe 的名字留在 dist 里。\n"
                "   （确实要先构建、回头再落名：设 YINDUN_BUILD_ALLOW_LOCKED=1）")
    try:
        os.replace(probe, final_exe)
    except OSError:
        print(f"⚠ 探测用改名未能改回，交付物现在是：{probe}（请手工改回）")
    return ""


def main() -> int:
    os.chdir(ROOT)
    if not os.path.isfile(SPEC):
        print(f"❌ 找不到打包配置：{SPEC}")
        return 1

    print("=" * 78)
    print("隐盾 · 免安装单文件 exe 构建（PyInstaller onefile）")
    print("=" * 78)
    print(f"仓库根目录：{ROOT}")
    print("提示：首次构建约 5~15 分钟；产物为单文件 exe，双击即用。\n")

    try:
        import PyInstaller.__main__ as pyi
    except ImportError:
        print("❌ 未安装 PyInstaller。请先执行：pip install -r requirements.txt")
        return 1

    # ★ 构建前先确认交付物文件**可以被替换**（V3.6.7 实测踩到）：如果用户正跑着 dist 里的
    #   exe，Windows 会锁住该文件——旧写法要等 PyInstaller 跑完七分钟才在 os.remove 处抛
    #   PermissionError，既白等一场，又留下一个没换名的半成品（dist/YindunPortable.exe）。
    #   现在提前一步探一下：探不通就当场说清楚怎么办，别浪费七分钟。
    blocked = _locked_hint(FINAL_EXE)
    if blocked:
        print(f"\n❌ {blocked}")
        return 1

    try:
        pyi.run([
            SPEC,
            "--noconfirm",
            "--clean",
            "--distpath", "dist",
            "--workpath", "build",
        ])
    except SystemExit as exc:  # PyInstaller 出错时会 SystemExit
        code = exc.code if isinstance(exc.code, int) else 1
        if code:
            print(f"\n❌ 构建失败（PyInstaller 退出码 {code}）")
            return code

    if not os.path.isfile(RAW_EXE):
        print(f"\n❌ 未找到构建产物：{RAW_EXE}")
        return 1

    if os.path.isfile(FINAL_EXE):
        try:
            os.remove(FINAL_EXE)
        except PermissionError:
            # 走到这里说明是"允许占用继续构建"那条路：不抛栈，说清楚新产物在哪、怎么落名
            print("\n" + "=" * 78)
            print(f"⚠ 新产物已构建完成，但**未能改名**（旧 exe 正在运行，文件被锁）：")
            print(f"   新产物：{RAW_EXE}")
            print(f"   目标名：dist/{FINAL_NAME}")
            print("   关掉正在运行的「隐盾安全智能体」窗口后，重新执行本脚本即可落名；")
            print("   或者手工把上面那个文件改名为目标名（内容已是最新，无需重新构建）。")
            print("=" * 78)
            return 1
    os.replace(RAW_EXE, FINAL_EXE)

    size_mb = os.path.getsize(FINAL_EXE) / 1024 / 1024
    print("\n" + "=" * 78)
    print(f"✅ 构建完成：dist/{FINAL_NAME}（{size_mb:.0f} MB）")
    print("=" * 78)
    print("使用前提（务必告知使用者）：")
    print("  1) 需另行安装 Ollama，并至少拉取一个支持工具调用的模型，例如：")
    print("       ollama pull qwen3.5:4b")
    print("     （知识库功能还需要：ollama pull nomic-embed-text）")
    print("  2) exe 放在可写目录（桌面/便携盘均可）：配置、会话、审计、向量库")
    print("     会写在 exe 同目录下；主密钥由 Windows DPAPI 按当前用户保护。")
    print("  3) 免安装、免脚本：双击即用；卸载 = 删除 exe 与其同目录生成的数据文件。")
    print(f"  4) OCR（图片 / 扫描件 PDF 能否读出文字）：{_ocr_status()}")
    print("     附件支持格式见 使用说明；不支持或读不出的格式，界面会给出可行动的原因。")
    return 0


def _ocr_status() -> str:
    """本次构建产物是否含 OCR（图片与扫描件 PDF 识别能力的唯一前提）。

    ★ 为什么写在"使用前提"里：这里静默降级过一次——spec 装不到 OCR 包时只打一行日志，
    构建完谁也没注意，交付出去才发现"图片/扫描件读不到文字"。这行字把结论摆到人眼前。
    ★ 判据必须与 spec 一致（本轮修正）：**主路径是 Windows 内置 OCR（winrt 包）**，
    rapidocr 只是可选加成。旧实现只看 rapidocr，于是明明打了 winrt 也报"⚠ 不含 OCR"——
    对着交付的人撒谎（方向是悲观，但同样是撒谎）。两条件都报，且说清各自能力。
    """
    import importlib.util
    winrt_ok = importlib.util.find_spec("winrt") is not None
    rapid_label = ""
    for name, label in (("rapidocr_onnxruntime", "rapidocr-onnxruntime"), ("rapidocr", "rapidocr")):
        if importlib.util.find_spec(name) is not None:
            rapid_label = label
            break
    if winrt_ok and rapid_label:
        return (f"✅ 含 OCR（Windows 内置 OCR + {rapid_label}）→ 图片与扫描件 PDF 可提取文字")
    if winrt_ok:
        return ("✅ 含 OCR（Windows 内置 OCR：用系统已装语言包，不额外下载、约 4MB）"
                "→ 图片与扫描件 PDF 可提取文字")
    if rapid_label:
        return f"✅ 含 OCR（{rapid_label}）→ 图片与扫描件 PDF 可提取文字"
    return ("⚠ 不含 OCR → 图片附件与扫描件 PDF **读不到文字**（会如实提示“需要 OCR 组件”）；"
            "如需支持：pip install winrt-runtime winrt-Windows-Media-Ocr（约 4MB，推荐）"
            "或 pip install rapidocr（约 150MB）后重新构建")



if __name__ == "__main__":
    sys.exit(main())
