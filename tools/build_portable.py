# -*- coding: utf-8 -*-
"""隐盾 · 免安装单文件 exe 构建入口

用法：
    python tools/build_portable.py

产物：
    dist/YindunPortable.exe → 自动重命名为 dist/隐盾安全智能体_V3.4.0_便携版.exe

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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join("tools", "yindun_agent_portable.spec")
DIST = os.path.join(ROOT, "dist")
RAW_EXE = os.path.join(DIST, "YindunPortable.exe")
FINAL_NAME = "隐盾安全智能体_V3.4.0_便携版.exe"
FINAL_EXE = os.path.join(DIST, FINAL_NAME)


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
        os.remove(FINAL_EXE)
    os.replace(RAW_EXE, FINAL_EXE)

    size_mb = os.path.getsize(FINAL_EXE) / 1024 / 1024
    print("\n" + "=" * 78)
    print(f"✅ 构建完成：dist/{FINAL_NAME}（{size_mb:.0f} MB）")
    print("=" * 78)
    print("使用前提（务必告知使用者）：")
    print("  1) 需另行安装 Ollama，并至少拉取一个支持工具调用的模型，例如：")
    print("       ollama pull qwen2.5:7b")
    print("     （知识库功能还需要：ollama pull nomic-embed-text）")
    print("  2) exe 放在可写目录（桌面/便携盘均可）：配置、会话、审计、向量库")
    print("     会写在 exe 同目录下；主密钥由 Windows DPAPI 按当前用户保护。")
    print("  3) 免安装、免脚本：双击即用；卸载 = 删除 exe 与其同目录生成的数据文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
