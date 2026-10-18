# -*- coding: utf-8 -*-
"""隐盾控制面 · 免安装单文件 exe 构建入口（企业端）

用法：
    python tools/build_control.py

产物：
    dist/YindunControl.exe → 自动重命名为 dist/隐盾控制端_<版本>_便携版.exe
    （版本号动态取自 yindun/__init__.py 的 __display_version__）

说明：
- 与客户端构建同样使用 PyInstaller 的 **Python API**（`PyInstaller.__main__.run`）而非
  subprocess：既符合本仓库的静态门禁（见 AGENTS.md），也让构建过程在同一进程里可读可控。
- 控制面零第三方依赖（http.server + sqlite3 + hmac），产物只有十几 MB；
  客户端那些重依赖在 spec 里被整体排除，因此**不会**把 130MB 的客户端体积带过来。
- 启动形态：**双击 exe 直接就是一个原生控制台窗口**（无控制台、无黑命令行）——
  窗口自带拖动与最小化/最大化/关闭，管理令牌通过 fragment 自动带进页面、页面立刻从地址栏抹掉。
  启动信息（监听地址、数据目录、令牌文件）写进同目录 `logs/yindun.log`；
  启动失败（端口被占等）会弹系统对话框说明原因与日志路径，不会"双击了没反应"。
- 只起服务（装在服务器上）：`隐盾控制端_<版本>_便携版.exe --no-window --port 8787`，
  此时同样没有终端输出，观察手段是日志文件与 HTTP 接口。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools/packaging/ → 仓库根
sys.path.insert(0, ROOT)
import yindun as _pkg  # noqa: E402  # 只为取 __display_version__（模块级只有路径/代理设置）

SPEC = os.path.join("tools", "packaging", "yindun_control.spec")
DIST = os.path.join(ROOT, "dist")
RAW_EXE = os.path.join(DIST, "YindunControl.exe")
FINAL_NAME = f"隐盾控制端_{_pkg.__display_version__}_便携版.exe"
FINAL_EXE = os.path.join(DIST, FINAL_NAME)


def main() -> int:
    os.chdir(ROOT)
    if not os.path.isfile(SPEC):
        print(f"❌ 找不到打包配置：{SPEC}")
        return 1

    print("=" * 78)
    print("隐盾控制面 · 免安装单文件 exe 构建（PyInstaller onefile）")
    print("=" * 78)
    print(f"仓库根目录：{ROOT}")
    print("提示：控制面零第三方依赖，构建通常 1~3 分钟即可完成。\n")

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
    except SystemExit as exc:
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
            # 旧 exe 正在运行（控制台窗口开着）→ 文件被锁。不抛栈：说清新产物在哪、怎么落名。
            print("\n" + "=" * 78)
            print("⚠ 新产物已构建完成，但**未能改名**（旧 exe 正在运行，文件被锁）：")
            print(f"   新产物：{RAW_EXE}")
            print(f"   目标名：dist/{FINAL_NAME}")
            print("   关掉正在运行的控制端窗口后，重新执行本脚本即可落名；")
            print("   或者手工把上面那个文件改名为目标名（内容已是最新，无需重新构建）。")
            print("=" * 78)
            return 1
    os.replace(RAW_EXE, FINAL_EXE)

    size_mb = os.path.getsize(FINAL_EXE) / 1024 / 1024
    print("\n" + "=" * 78)
    print(f"✅ 构建完成：{FINAL_EXE}（{size_mb:.1f} MB）")
    print("   启动：双击 exe 即打开控制台窗口（无黑命令行、自动登录）；")
    print("         启动信息与排障线索见 exe 同目录 logs/yindun.log")
    print("         只起服务（服务器场景）：--no-window --port 8787；")
    print("         远端接入用的管理令牌见 control_data/admin_token.txt")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
