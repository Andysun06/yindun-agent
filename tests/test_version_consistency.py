# -*- coding: utf-8 -*-
"""隐盾 · 版本号单点化一致性回归（V3.6.1）

为什么要有这套件：版本号曾散在 4 处（`yindun/__init__.py`、控制台徽标、前端预览 mock、
exe 文件名由脚本动态取），每次发布要手改 3 处——V3.5.1→V3.6.0 期间就出现过
"控制台徽标写 V3.5.9、客户端已是 V3.6.0"这类不一致，而且没人会发现。

现在唯一来源是 `yindun/__display_version__`：
  · 控制台 index.html 放 `{{VERSION}}` 占位符，由 `control.server.render_static()` 注入；
  · 客户端界面从 `bootstrap` 载荷的 `version` 读（`app.js` 写进 `#brand-version`）；
  · 本地预览 mock 不写产品版本（写死就会漂）；
  · 打包脚本的 exe 文件名由 `__display_version__` 拼。

本套件钉住这条链路 + 钉住"文档也要跟着 bump"（README 版本表、开发记忆头部）。

用法：python tests/test_version_consistency.py   （退出码 0 = 全部通过）
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yindun  # noqa: E402
from yindun.control.server import WEB_DIR, display_version, render_static  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


VERSION = yindun.__version__
DISPLAY = yindun.__display_version__

print("=" * 78)
print("【1】唯一来源：__version__ / __display_version__ 自洽")
print("=" * 78)
check("display 就是 version 加 V 前缀", DISPLAY == "V" + VERSION, f"{DISPLAY} / {VERSION}")
check("version 形如 x.y.z", bool(re.fullmatch(r"\d+\.\d+\.\d+", VERSION)), VERSION)

print("\n" + "=" * 78)
print("【2】控制台：占位符 + 服务端注入")
print("=" * 78)
index_html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
check("控制台页面用 {{VERSION}} 占位符（不写死版本）", "{{VERSION}}" in index_html)
check("控制台服务端能取到版本（来自 yindun.__display_version__）", display_version() == DISPLAY,
      display_version())
served = render_static(WEB_DIR / "index.html").decode("utf-8")
check("注入后的页面含当前版本", DISPLAY in served, served[:120])
check("注入后不再残留占位符", "{{VERSION}}" not in served)
for asset in ("app.js", "styles.css"):
    text = (WEB_DIR / asset).read_text(encoding="utf-8")
    # 允许出现"端点客户端版本"这类样例数据（如 3.4.6），但不能出现**产品版本**
    check(f"控制台 {asset} 不含写死的产品版本", DISPLAY not in text and VERSION not in text)

print("\n" + "=" * 78)
print("【3】客户端界面：版本来自 bootstrap 载荷")
print("=" * 78)
client_index = (ROOT / "yindun" / "app" / "web" / "index.html").read_text(encoding="utf-8")
client_js = (ROOT / "yindun" / "app" / "web" / "app.js").read_text(encoding="utf-8")
client_mock = (ROOT / "yindun" / "app" / "web" / "dev-mock.js").read_text(encoding="utf-8")
check("界面有版本位（#brand-version）", "brand-version" in client_index)
check("界面把 bootstrap 的 version 写进版本位",
      re.search(r'brand-version"\)\.textContent\s*=\s*\w+\.version', client_js) is not None)
check("本地预览 mock 不写产品版本（写死就会漂）",
      DISPLAY not in client_mock and VERSION not in client_mock)
check("客户端界面静态资源里没有写死的产品版本",
      DISPLAY not in client_index and DISPLAY not in client_js)

print("\n" + "=" * 78)
print("【4】打包脚本：exe 文件名由 __display_version__ 拼")
print("=" * 78)
for script, marker in (("tools/packaging/build_portable.py", "隐盾安全智能体"),
                       ("tools/packaging/build_control.py", "隐盾控制端")):
    text = (ROOT / script).read_text(encoding="utf-8")
    check(f"{script} 用 __display_version__ 生成文件名",
          "__display_version__" in text and marker in text)
    check(f"{script} 没有写死版本号", DISPLAY not in text)

print("\n" + "=" * 78)
print("【5】文档跟得上 bump（漏改会在这里报红）")
print("=" * 78)
readme = (ROOT / "README.md").read_text(encoding="utf-8")
dev_mem = (ROOT / "docs" / "dev" / "开发记忆.md").read_text(encoding="utf-8")
check("README 版本表里有当前版本", DISPLAY in readme, DISPLAY)
check("开发记忆头部写了当前版本", f"当前版本 **{DISPLAY}**" in dev_mem,
      [ln for ln in dev_mem.splitlines() if "当前版本" in ln][:1])

print("\n" + "=" * 78)
if failures:
    print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
    sys.exit(1)
print(f"✅ 版本号单点化一致：{DISPLAY}（界面/控制台/打包脚本/文档 全部跟得上）")
sys.exit(0)
