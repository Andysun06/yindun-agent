# -*- coding: utf-8 -*-
"""隐盾 · 界面自动化回归（V3.6.1）

为什么要有它（开发记忆 §7 B-4，"当前最大的工程债"）：界面改动一直靠"源码契约 grep +
手工点浏览器"验证——改一次要点十几轮，而且逻辑回归（渲染后的真实 DOM 行为）根本测不到。
本套件用 Playwright 驱动真实 Chromium 打开 `index.html#mock`（界面自带的后端沙盘），
把十二条关键交互钉死；不需要 Ollama、不需要起后端，两分钟内跑完。

覆盖（每条都是"用户真会做的一步"）：
  1. 四档权限：分段器选中态 / 提示文案 / 矩阵渲染与内核 `MODE_POLICY` **逐格一致** / 落库调用
  2. 每对话权限胶囊：弹出、选档、胶囊文案与 `set_session_permission` 调用
  3. 思考强度四档：下拉四项、选中后顶栏文案与 `save_settings` 调用
  4. 插件市场：搜索过滤、启停开关（含确认语义按来源区分）→ 调用 `plugin_set_enabled`
  5. 插件专属设置栏目：启用带设置的插件后栏目出现、停用后消失
  6. 插件专属设置页渲染：laya 三级接入表单（本机/局域网/正则）+ 保存调用
  7. 设置页六组页面渲染（模型/隐私与权限/知识库/插件/企业控制面/窗口与外观）
  7b. 模型接入页（V3.6.8 重做）：左列按供应商聚合 / **背景高亮只表示"正在查看"**（同一时刻恰 1 个）/
      「使用中」是绿点标记（不是背景）/ 表单三块（Base URL / API 格式 / API Key）/ 模型列表行
      （显示名 + 模型 ID + 标签 + 编辑删除）/ 点模型名即切换 / API Key「查看」调桥
  7c. 密钥语义：留空＝不改动（沿用已存）+ 测试连接捎上条目名
  7d. 添加供应商 → 添加模型（写入时同一供应商归到同一 provider，带上 api_format / tags）
  7e. 可读写范围（sandbox_dir）：设置项显示 / 选择目录 / 清除回默认 / 态势条同一来源 /
      四档矩阵写明"越界按档位判定并写审计"
  8. 工作流**界面入口已移除**：DOM 里没有按钮/面板/页签，源码里没有 `workflow_*` / `wf-*` 残留
  9. 闪发模式：输入与主输入同步、Enter 发送、Shift+Enter 换行（不发送）
 10. 明暗切换 / 右栏折叠 → 窗口按钮归位（topbar ↔ panel ↔ head 三态）
 11. 附件「＋」：真的触发 pick_files 并有可读反馈；桥缺失时提示「桥接未就绪」
 12. Markdown 表格单元格：粗体 / 行内代码 / <br> 换行 / 链接 / HTML 注入防护
 13. 反馈时序（真机事故固化）：发送**立刻**出现等待态；status / intermediate_result 到达后
     是"同一条气泡原地升级"（工具行 / 流式正文），不是并存两条；finished 收到空串时显示
     如实兜底文案且**不留空气泡**（已有流式正文时正文优先）；没有流式气泡时 trace 也有
     可见落点；全程无 JS 运行时错误
 14. 安全工具：隐私健康体检 / 模型行为画像的结构化渲染与失败路径
 15. 设置页内容限宽居中（1920 / 1000 两档）
 16. 模型选择器：两级弹层（供应商 → 模型）、**没有搜索框**、键盘 ↑↓/Enter、点外部关闭、
     「管理模型」跳设置页
 17. 思考强度：下拉只留档位名（解释收进 title）、触发器紧凑、语义不变（仍落库同一档）
 18. 上下文容量浮层：进度条与分类占比**逐项等于后端给的值**、底部两行来自预算表、
     不出现"缓存命中率"这类我们没有的指标
 19. 窗口按钮：正好四个（置顶/最小化/最大化/关闭）、点最大化调桥并切图标、置顶高亮与
     `window_state()` 一致、折叠态下最大化按钮禁用且服务端拒绝
 20. 暗色滚动条：corner/track 透明、button 不显示（白块根因），宽度仍是 10px
 21. 脱浏览器的服务层断言：密钥「查看」返回本机明文并写 `access_control` 审计（不含密钥本身）、
     `context_usage()` 的数字与预算表一致
 22. 模型可用性诚实化（K）：配置里的模型不在清单里时胶囊显示「请选择模型」（不宣称不存在的模型）、
     本机档空态原样显示 `local_note` + 两个动作、手动添加本机模型走 `<ollama_host>/v1` + `ollama` 密钥、
     加完能立刻切换；没有任何可选模型时选择器给可行动提示

用法：python tests/test_ui_paths.py        （退出码 0 = 全部通过）
依赖：pip install playwright && python -m playwright install chromium
"""
import json
import os
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WEB_DIR = ROOT / "yindun" / "app" / "web"

from yindun.core.permission import MODE_POLICY  # noqa: E402

failures = []
LIMIT_SECONDS = 210


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):        # 别把静态请求刷满屏幕
        pass


def start_static_server() -> tuple:
    handler = partial(_QuietHandler, directory=str(WEB_DIR))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def main() -> int:
    started = time.time()
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:                                            # noqa: BLE001
        print(f"❌ 未安装 playwright（pip install playwright && python -m playwright install chromium）：{exc}")
        return 1

    httpd, port = start_static_server()
    url = f"http://127.0.0.1:{port}/index.html#mock"
    print(f"界面沙盘：{url}")

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:                                        # noqa: BLE001
            print(f"❌ Chromium 起不来（先跑 python -m playwright install chromium）：{exc}")
            httpd.shutdown()
            return 1
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_function(
            "() => { const v = document.querySelector('#brand-version');"
            " return v && v.textContent.trim() && v.textContent.trim() !== '—'; }", timeout=20000)
        # 记录所有 mock 接口调用（用来断言"界面真的调了那个接口、参数对不对"）
        page.evaluate("""() => {
            window.__calls = [];
            const api = window.__YINDUN_MOCK__.api;
            for (const key of Object.keys(api)) {
                if (typeof api[key] !== 'function') continue;
                const fn = api[key];
                api[key] = (...args) => { window.__calls.push([key, args]); return fn(...args); };
            }
        }""")

        def calls(name: str) -> list:
            return page.evaluate("(n) => (window.__calls || []).filter(c => c[0] === n)", name)

        # ── 1) 四档权限：分段器 / 提示 / 矩阵与内核一致 / 落库 ──
        print("\n" + "=" * 78)
        print("【1】AI 权限四档（设置页）")
        print("=" * 78)
        page.click("#btn-settings")          # 设置是独立视图，先打开它
        page.wait_for_selector('.setnav__item[data-set="safety"]', state="visible", timeout=8000)
        check("点「设置」进入设置视图", page.evaluate(
            "() => document.querySelector('.app').classList.contains('is-settings-open')"))
        page.click('.setnav__item[data-set="safety"]')
        for mode in ("plan", "ask", "auto", "full"):
            page.click(f'#perm-modes .permseg__btn[data-mode="{mode}"]')
            active = page.get_attribute(f'#perm-modes .permseg__btn[data-mode="{mode}"]', "class") or ""
            aria = page.get_attribute(f'#perm-modes .permseg__btn[data-mode="{mode}"]', "aria-checked")
            check(f"切到「{mode}」后该档高亮且 aria-checked=true",
                  "is-active" in active and aria == "true", f"class={active} aria={aria}")
        hint = (page.inner_text("#perm-hint") or "").strip()
        check("切换档位后提示文案非空", len(hint) > 4, hint)
        saved = [c for c in calls("save_settings")
                 if c[1] and isinstance(c[1][0], dict) and "permission" in c[1][0]]
        check("界面把档位落库（save_settings({permission: …})）", len(saved) >= 1, str(saved[:2]))
        # 矩阵与内核逐格比对：UI 只是渲染，判定口径必须在 core/permission.py
        matrix = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#perm-matrix tbody tr')];
            return rows.map(tr => [tr.getAttribute('data-op'),
                                   [...tr.querySelectorAll('td')].map(td => td.getAttribute('data-decision'))]);
        }""")
        check("矩阵渲染出 4 行（读/写/删/执行）", len(matrix) == 4, str([r[0] for r in matrix]))
        expected_order = ["plan", "ask", "auto", "full"]
        mismatched = []
        for op, decisions in matrix:
            want = [MODE_POLICY[m][op] for m in expected_order]
            if decisions != want:
                mismatched.append(f"{op}: UI={decisions} 内核={want}")
        check("权限矩阵与内核 MODE_POLICY 逐格一致（界面不许自成一派）", not mismatched, str(mismatched))

        # ── 2) 每对话权限胶囊 ──
        print("\n" + "=" * 78)
        print("【2】每对话权限胶囊（顶栏）")
        print("=" * 78)
        # 胶囊/思考/闪发都在对话视图的顶栏——设置是独立视图，先返回（否则元素不可见）
        page.click("#set-back")
        page.wait_for_timeout(200)
        check("点返回退出设置视图", not page.evaluate(
            "() => document.querySelector('.app').classList.contains('is-settings-open')"))
        page.click("#perm-trigger")
        check("点胶囊后弹出选项层", page.is_visible("#perm-pop"))
        options = page.eval_on_selector_all("#perm-pop [data-mode]", "els => els.map(e => e.dataset.mode)")
        check("弹层含四个档位 + 跟随默认", set(options) >= {"plan", "ask", "auto", "full", ""}, str(options))
        page.click('#perm-pop [data-mode="plan"]')
        page.wait_for_timeout(120)
        value = (page.inner_text("#perm-value") or "").strip()
        mode_attr = page.get_attribute("#perm-pill", "data-mode")
        check("选「计划模式」后胶囊文案与状态更新", value == "计划模式" and mode_attr == "plan",
              f"value={value} data-mode={mode_attr}")
        session_calls = calls("set_session_permission")
        check("调用了 set_session_permission('plan')",
              any(c[1][0] == "plan" for c in session_calls), str(session_calls[:2]))

        # ── 3) 思考强度四档 ──
        print("\n" + "=" * 78)
        print("【3】思考强度下拉（四档）")
        print("=" * 78)
        page.click("#think-trigger")
        check("点开后出现下拉选项", page.is_visible("#think-pop"))
        levels = page.eval_on_selector_all("#think-pop [data-level]", "els => els.map(e => e.dataset.level)")
        check("四档齐全（低/中/高/最高）", levels == ["low", "mid", "high", "max"], str(levels))
        page.click('#think-pop [data-level="max"]')
        page.wait_for_timeout(120)
        check("选「最高」后顶栏文案更新", (page.inner_text("#think-value") or "").strip() == "最高",
              page.inner_text("#think-value"))
        # 思考强度存的是**推理轮次**（thinking_depth），不是顶栏那个"快速/思考"开关——两者别混
        depth_saved = [c for c in calls("save_settings")
                       if c[1] and isinstance(c[1][0], dict) and "thinking_depth" in c[1][0]]
        check("推理轮次落库（save_settings({thinking_depth: 10})）",
              depth_saved and depth_saved[-1][1][0]["thinking_depth"] == 10, str(depth_saved[-2:]))
        page.click('#mode-switch .seg__btn[data-mode="深度思考"]')
        page.wait_for_timeout(150)
        think_mode_saved = [c for c in calls("save_settings")
                            if c[1] and isinstance(c[1][0], dict) and "think_mode" in c[1][0]]
        check("顶栏「思考」开关落库（save_settings({think_mode: 深度思考})）",
              think_mode_saved and think_mode_saved[-1][1][0]["think_mode"] == "深度思考",
              str(think_mode_saved[-2:]))

        # ── 4) 插件市场：搜索 + 启停 ──
        print("\n" + "=" * 78)
        print("【4】插件市场（搜索 / 启停）")
        print("=" * 78)
        page.click("#btn-settings")
        page.wait_for_selector('.setnav__item[data-set="plugin"]', state="visible", timeout=8000)
        page.click('.setnav__item[data-set="plugin"]')
        page.wait_for_selector(".pcard")
        total_cards = page.eval_on_selector_all(".pcard", "els => els.length")
        check("插件卡片全部渲染（6 个内置插件）", total_cards >= 6, str(total_cards))
        page.fill("#plugin-search", "laya")
        page.wait_for_timeout(150)
        filtered = page.eval_on_selector_all(".pcard", "els => els.map(e => e.dataset.card)")
        check("搜索「laya」只剩相关插件", filtered == ["laya_risk"], str(filtered))
        page.fill("#plugin-search", "")
        page.wait_for_timeout(150)
        check("清空搜索后恢复全部",
              page.eval_on_selector_all(".pcard", "els => els.length") == total_cards)
        # 开关的原生 input 是 `display: none`（由 .switch__track 画样式），所以点可见的轨道
        page.click('.pcard[data-card="laya_risk"] .switch__track')
        page.wait_for_timeout(300)
        enabled_calls = [c for c in calls("plugin_set_enabled") if c[1][0] == "laya_risk"]
        check("切换开关调用 plugin_set_enabled('laya_risk', true)",
              enabled_calls and enabled_calls[-1][1][1] is True, str(enabled_calls[-2:]))
        checked = page.evaluate(
            "() => document.querySelector('.pcard[data-card=\"laya_risk\"] .switch input').checked")
        check("开关状态跟着后端返回更新（checked=true）", checked is True, str(checked))

        # ── 5) 插件专属设置栏目出现/消失 ──
        print("\n" + "=" * 78)
        print("【5】插件专属设置栏目（启用才出现）")
        print("=" * 78)
        page.wait_for_timeout(250)
        nav_visible = page.is_visible("#setnav-plugins")
        check("启用 laya 后设置栏出现插件分组", nav_visible)
        nav_items = page.eval_on_selector_all("#setnav-plugins .setnav__item",
                                             "els => els.map(e => e.textContent.trim())")
        check("分组里有「laya 风险分级」设置入口", any("laya" in t.lower() for t in nav_items), str(nav_items))
        # ★ 再测"没有任何插件带设置时，整栏必须隐藏"（V3.6.5 修的真实缺陷，用户截图实测）：
        #   `.setnav__group` 声明了 display:flex，盖掉了 UA 的 [hidden]{display:none} →
        #   空栏目照样显示一个孤零零的「插件」标题。
        #   做法：把**所有带设置的插件**都停掉（mock 里默认启用的是 custom_dict，带 1 个配置项），
        #   然后要求整栏彻底不可见、且没有残留条目；再复原状态供后续组使用。
        def _group_state():
            return page.evaluate(
                "() => { const g = document.querySelector('#setnav-plugins'); if (!g) return [false, 0];"
                " const st = getComputedStyle(g);"
                " const visible = st.display !== 'none' && g.getBoundingClientRect().height > 0;"
                " return [visible, g.querySelectorAll('.setnav__item').length]; }")

        # 把"所有带设置的插件"都停掉（此前列里 mock 默认启用的是 custom_dict，另外我刚启用了 laya），
        # 只剩确定没有设置项的插件在启用状态 → 栏目必须完全消失。
        page.click('.pcard[data-card="custom_dict"] .switch__track')
        page.wait_for_timeout(300)
        page.click('.pcard[data-card="laya_risk"] .switch__track')
        page.wait_for_timeout(400)
        _visible, _items = _group_state()
        check("停掉所有带设置的插件后，插件专属设置栏彻底隐藏（不留空标题、不残留条目）",
              not _visible and _items == 0, f"visible={_visible} items={_items}")
        # 复原：custom_dict 回到 mock 默认的启用态；laya 重新启用，供第 6 组（专属设置页）使用
        page.click('.pcard[data-card="custom_dict"] .switch__track')
        page.wait_for_timeout(300)
        page.click('.pcard[data-card="laya_risk"] .switch__track')
        page.wait_for_timeout(400)

        # ── 6) 插件专属设置页渲染（laya 三级接入）──
        print("\n" + "=" * 78)
        print("【6】laya 三级接入设置页")
        print("=" * 78)
        page.click('#setnav-plugins .setnav__item[data-plugin="laya_risk"]')
        page.wait_for_timeout(300)
        # 三级接入：左侧三档、右侧按选中档渲染详情（数据边界分档是它的产品语义）
        tiers = page.eval_on_selector_all("#laya-list [data-t]", "els => els.map(e => e.dataset.t)")
        check("三档接入列表渲染（本机/局域网/正则）", tiers == ["local", "lan", "regex"], str(tiers))
        page_text = page.inner_text("#plugin-settings-page")
        for keyword in ("本机", "局域网", "正则", "数据不出本机", "不参与任何放行判定"):
            check(f"页面含「{keyword}」（口径写清楚）", keyword in page_text)
        page.click('#laya-list [data-t="lan"]')
        page.wait_for_timeout(200)
        check("切到「局域网」后出现端点输入框", page.is_visible("#laya-endpoint"))
        check("切到「局域网」后出现模型名输入框", page.is_visible("#laya-model"))
        lan_ops = page.eval_on_selector_all("[data-laya]", "els => els.map(e => e.dataset.laya)")
        check("局域网档有测试/试打分/保存三个动作",
              {"probe-lan", "try-lan", "save-lan"} <= set(lan_ops), str(lan_ops))
        page.click('#laya-list [data-t="regex"]')
        page.wait_for_timeout(200)
        regex_ops = page.eval_on_selector_all("[data-laya]", "els => els.map(e => e.dataset.laya)")
        check("正则档可读取/保存规则文件",
              {"load-rules", "save-rules"} <= set(regex_ops), str(regex_ops))
        check("正则档有规则编辑框", page.is_visible("#laya-rules"))
        page.click('#laya-list [data-t="local"]')
        page.wait_for_timeout(200)
        check("本机档有「重新检测」", page.is_visible('[data-laya="probe-local"]'))
        page.click('[data-laya="probe-local"]')
        page.wait_for_timeout(250)
        check("点「重新检测」调用 laya_probe", bool(calls("laya_probe")), str(calls("laya_probe")[:1]))

        # ── 7) 设置页六组渲染 ──
        print("\n" + "=" * 78)
        print("【7】设置页六组页面渲染")
        print("=" * 78)
        for page_name, label in (("model", "模型接入"), ("safety", "隐私与权限"), ("kb", "本地知识库"),
                                 ("plugin", "插件"), ("control", "企业控制面"), ("appearance", "窗口与外观")):
            page.click(f'.setnav__item[data-set="{page_name}"]')
            page.wait_for_timeout(120)
            text = page.inner_text(f'.setpage[data-set="{page_name}"]') if page.query_selector(
                f'.setpage[data-set="{page_name}"]') else ""
            check(f"「{label}」页面渲染且内容非空", len(text.strip()) > 20, f"{len(text.strip())} 字")
        # 外观页控件是"开关 + 按钮"，文字本来就短——按控件断言，而不是按字数
        # （开关的原生 input 是 display:none，由 .switch__track 画样式，所以查轨道可见性）
        check("外观页有置顶开关与主题按钮",
              page.is_visible("#sw-topmost + .switch__track") and page.is_visible("#btn-theme-2"))

        # ── 7b) 模型接入页（V3.6.8 按 ZCode「模型设置」重做）──
        print("\n" + "=" * 78)
        print("【7b】模型接入页：供应商聚合 / 唯一背景高亮 / 使用中绿点 / 模型列表")
        print("=" * 78)
        page.click('.setnav__item[data-set="model"]')
        page.wait_for_timeout(400)
        # 记录 save_settings 的入参：切换是真的落库了，还是只改了界面
        page.evaluate("""() => {
          window.__saved = [];
          const m = window.__YINDUN_MOCK__.api, orig = m.save_settings;
          m.save_settings = (patch) => { window.__saved.push(patch); return orig(patch); };
        }""")

        def token(name: str) -> str:
            """把设计 token 换算成实际颜色（用于断言"用的是品牌绿"而不是背景块）。"""
            return page.evaluate("""(n) => {
                const d = document.createElement('div');
                d.style.color = `var(${n})`;
                document.body.appendChild(d);
                const c = getComputedStyle(d).color; d.remove(); return c; }""", name)

        providers = page.evaluate("""() => [...document.querySelectorAll('#prov-list .prov__item')].map((b) => ({
            tier: b.dataset.t, prov: b.dataset.prov, builtin: b.dataset.builtin,
            active: b.classList.contains('is-active') }))""")
        check("左列按**供应商**聚合（本机 Ollama / 内网地址 / 云端域名，而不是一串模型名）",
              [(p["tier"], p["prov"]) for p in providers] ==
              [("local", "本机 Ollama"), ("lan", "10.0.0.8:8000"), ("wan", "open.bigmodel.cn")],
              str(providers))
        check("本机档仍列出 Ollama 已安装模型（摊开的子项可一键切换）",
              page.eval_on_selector_all("#prov-list .prov__sub", "els => els.map(e => e.dataset.use)") ==
              ["qwen2.5:7b-instruct", "qwen3.5:4b"], "")
        check("★ 背景高亮只表示「正在查看的那一项」（同一时刻恰好 1 个 is-active）",
              page.eval_on_selector_all("#prov-list .prov__item.is-active", "els => els.length") == 1,
              str([p["active"] for p in providers]))
        flags = page.evaluate("""() => [...document.querySelectorAll('#prov-list [data-cur-flag]')]
            .filter((e) => !e.hidden).map((e) => ({ cur: e.dataset.curFlag, cls: e.className }))""")
        check("「使用中」是独立标记（绿点），与「正在查看」的高亮互不共用",
              bool(flags) and all("usage-flag" in f["cls"] for f in flags)
              and all("prov__item" not in f["cls"] for f in flags), str(flags))
        dot_color = page.evaluate("""() => {
            const d = document.querySelector('#prov-list .usage-flag--dot');
            return d ? getComputedStyle(d).backgroundColor : ''; }""")
        check("「使用中」用品牌绿圆点（--accent-500），不是背景色块",
              dot_color == token("--accent-500"), f"{dot_color} vs {token('--accent-500')}")
        active_bg = page.evaluate("""() => getComputedStyle(document.querySelector('#prov-list .prov__item.is-active')).backgroundColor""")
        check("「正在查看」用的才是背景（与绿点标记可同时区分开）",
              active_bg not in ("rgba(0, 0, 0, 0)", "transparent"), active_bg)

        # 右侧表单三块 + 模型列表（选局域网供应商）
        page.click('#prov-list .prov__item[data-t="lan"]')
        page.wait_for_timeout(350)
        check("局域网供应商的右侧表单含 Base URL / API 格式 / API Key 三块与模型列表",
              page.is_visible("#p-url") and page.is_visible("#p-format")
              and page.is_visible("#p-key") and page.query_selector("#p-models .mrow") is not None, "")
        check("同一时刻只有一个背景高亮（切到局域网后仍是 1 个）",
              page.eval_on_selector_all("#prov-list .prov__item.is-active", "els => els.length") == 1, "")
        fmt = page.eval_on_selector_all("#p-format option", "els => els.map(e => [e.value, e.disabled, e.textContent])")
        check("API 格式下拉：只有 OpenAI 兼容 Chat Completions 可用，其它置灰并注明「暂不支持」",
              fmt[0][0] == "chat_completions" and fmt[0][1] is False
              and all(f[1] is True and "暂不支持" in f[2] for f in fmt[1:]), str(fmt))
        row = page.evaluate("""() => {
            const r = document.querySelector('#p-models .mrow'); if (!r) return null;
            return { name: r.dataset.mname,
                     mid: (r.querySelector('.mrow__mid') || {}).textContent || '',
                     tags: [...r.querySelectorAll('.mtag')].map(e => e.textContent),
                     ops: [...r.querySelectorAll('.mrow__ops .mbtn')].map(e => e.textContent) }; }""")
        check("模型列表每行 = 显示名 + 模型 ID + 标签 + 编辑/删除",
              row and row["name"] == "公司内网Qwen" and row["mid"] == "qwen2.5:14b"
              and row["tags"] == ["中文", "长文"] and row["ops"] == ["编辑", "删除"], str(row))
        check("「＋ 添加模型」在列表底部（同一供应商加模型）",
              page.is_visible('#prov-detail [data-addmodel]'), "")
        # 点模型名即切换（单选式"使用中"）
        page.click('#p-models .mrow[data-mname="公司内网Qwen"] .mrow__use')
        page.wait_for_timeout(500)
        saved = page.evaluate("() => window.__saved.slice()")
        check("点模型名即切换使用（落库 patch.model）",
              bool(saved) and saved[-1].get("model") == "公司内网Qwen", str(saved[-1:]))
        check("切换后「使用中」绿点移到新模型（页面当场反映）",
              page.evaluate("""() => [...document.querySelectorAll('#prov-list [data-cur-flag]')]
                  .filter(e => !e.hidden).map(e => e.dataset.curFlag)""") == ["公司内网Qwen"], "")
        check("★ 同一时刻只有一个「使用中」（单选式；不是 ZCode 那种多开关）",
              page.eval_on_selector_all("#p-models .usage-flag:not([hidden])", "els => els.length") == 1, "")
        check("输入区触发器变成「供应商 / 模型」",
              page.inner_text("#model-value").strip() == "10.0.0.8:8000 / 公司内网Qwen",
              page.inner_text("#model-value"))

        # ── 7c) 密钥：看（显式查看 + 审计）/ 留空＝不改动 / 探测回落到条目名 ──
        print("\n" + "=" * 78)
        print("【7c】API Key：显式「查看」/ 留空＝不改动 / 测试连接捎上条目名")
        print("=" * 78)
        page.click('#prov-list .prov__item[data-t="wan"]')
        page.wait_for_timeout(300)
        check("已存密钥时输入框不回显（占位写明「留空则沿用已存密钥」）",
              "已配置" in (page.get_attribute("#p-key", "placeholder") or ""),
              repr(page.get_attribute("#p-key", "placeholder")))
        page.evaluate("""() => {
          window.__keys = [];
          const m = window.__YINDUN_MOCK__.api, orig = m.custom_model_key;
          m.custom_model_key = (...args) => { window.__keys.push(args); return orig(...args); };
        }""")
        page.click("#p-key-eye")
        page.wait_for_timeout(350)
        keys = page.evaluate("() => window.__keys")
        check("点「查看」调用桥 custom_model_key(供应商名)——显式动作，不是自动回显",
              bool(keys) and keys[-1][0] == "open.bigmodel.cn", str(keys[-1:]))
        check("查看后输入框里是本机已存的明文（仅本机窗口可见）",
              page.input_value("#p-key").startswith("sk-") and page.inner_text("#p-key-eye") == "隐藏",
              f"{page.input_value('#p-key')[:8]!r} / {page.inner_text('#p-key-eye')!r}")
        page.click("#p-key-eye")
        page.wait_for_timeout(250)
        check("再点一次 = 隐藏并清空（回到「留空＝不改动」）",
              page.input_value("#p-key") == "" and page.inner_text("#p-key-eye") == "查看", "")
        page.evaluate("""() => {
          window.__probes = [];
          const m = window.__YINDUN_MOCK__.api, orig = m.probe_model_endpoint;
          m.probe_model_endpoint = (...args) => { window.__probes.push(args); return orig(...args); };
        }""")
        page.click('#prov-detail [data-probe="detail"]')
        page.wait_for_timeout(450)
        probes = page.evaluate("() => window.__probes")
        check("「测试连接」把条目名一并交给后端（后端据此用已存密钥，而不是空密钥）",
              bool(probes) and probes[-1][2] == "云端GLM" and str(probes[-1][1]) == "",
              str(probes[-1] if probes else None)[:160])
        page.click("#prov-detail [data-save]")
        page.wait_for_timeout(700)
        toast_text = page.eval_on_selector_all("#toast-root .toast", "els => els.map(e => e.textContent).join(' | ')")
        check("留空保存 → 如实提示「密钥沿用已存」（不是把密钥清掉）",
              "密钥沿用已存" in toast_text, repr(toast_text[:80]))
        check("保存后该供应商仍在（保存真的生效，不是静默失败）",
              page.query_selector('#prov-list .prov__item[data-prov="open.bigmodel.cn"]') is not None, "")

        # ── 7d) 添加供应商 → 添加模型（同一供应商归到同一 provider）──
        print("\n" + "=" * 78)
        print("【7d】添加供应商 / 添加模型（provider / api_format / tags 落库）")
        print("=" * 78)
        page.click('#prov-list .prov__add[data-addt="wan"]')
        page.wait_for_timeout(300)
        check("「＋ 添加供应商」= 空表单 + 必填的「第一个模型」（供应商不能凭空存在）",
              page.input_value("#p-provider") == "" and page.is_visible("#pm-panel")
              and page.input_value("#pm-model") == "", "")
        page.fill("#p-provider", "回归-云端供应商")
        page.fill("#p-url", "https://api.example.invalid/v1")
        page.fill("#pm-model", "demo-model-a")
        page.fill("#pm-tags", "中文，长文")
        page.click("#prov-detail [data-save]")
        page.wait_for_timeout(800)
        add_calls = calls("add_custom_model")
        check("保存供应商 → add_custom_model 带上 provider / api_format / tags",
              bool(add_calls) and add_calls[-1][1][5] == "回归-云端供应商"
              and add_calls[-1][1][6] == "chat_completions"
              and list(add_calls[-1][1][7]) == ["中文", "长文"], str(add_calls[-1][1]))
        check("新供应商出现在左列（公网档）",
              page.query_selector('#prov-list .prov__item[data-prov="回归-云端供应商"]') is not None, "")
        page.click("#prov-detail [data-addmodel]")
        page.wait_for_timeout(250)
        check("点「＋ 添加模型」展开编辑面板", page.is_visible("#pm-panel") and page.is_visible("#pm-model"), "")
        page.fill("#pm-model", "demo-model-b")
        page.fill("#pm-name", "回归模型B")
        page.click("#prov-detail [data-msave]")
        page.wait_for_timeout(800)
        add_calls = calls("add_custom_model")
        check("新模型归到同一 provider（不是各挂各的）",
              bool(add_calls) and add_calls[-1][1][0] == "回归模型B"
              and add_calls[-1][1][5] == "回归-云端供应商", str(add_calls[-1][1]))
        check("该供应商下现在有两个模型（列表真的多了）",
              page.eval_on_selector_all("#p-models .mrow", "els => els.length") == 2, "")

        # ── 7e) 可读写范围（沙箱目录）：显示 / 选择 / 清除 / 态势条同步 ──
        print("\n" + "=" * 78)
        print("【7e】可读写范围（sandbox_dir）：设置项 + 态势条同一来源")
        print("=" * 78)
        page.click('.setnav__item[data-set="safety"]')
        page.wait_for_timeout(300)
        check("隐私与权限页里有「可读写范围」的路径、选择目录与清除三个控件",
              page.is_visible("#sandbox-path") and page.is_visible("#btn-pick-sandbox")
              and page.is_visible("#btn-clear-sandbox"), "")
        check("未自定义时显示后端给的默认范围（与态势条同源）",
              page.inner_text("#sandbox-path").strip() == "E:\\yindun-agent"
              and "默认" in page.inner_text("#sandbox-note"), page.inner_text("#sandbox-path"))
        safety_text = page.inner_text('.setpage[data-set="safety"]')
        check("说明文案写清「越界」按档位判定 + 写入「审计」（防被改成空话）",
              "越界" in safety_text and "审计" in safety_text
              and "系统目录与凭据类路径" in safety_text, "")
        check("四档对照表下方写明「沙箱外操作按同一档位判定」",
              "可读写范围（沙箱目录）内" in safety_text and "越界" in safety_text, "")
        page.click("#btn-pick-sandbox")
        page.wait_for_timeout(500)
        sandbox_saved = [c for c in calls("save_settings")
                         if c[1] and isinstance(c[1][0], dict) and "sandbox_dir" in c[1][0]]
        check("「选择目录…」走原生目录通道并落库 sandbox_dir",
              bool(calls("pick_directory")) and sandbox_saved
              and sandbox_saved[-1][1][0]["sandbox_dir"] == "E:\\yd-demo-sandbox",
              str(sandbox_saved[-1:]))
        check("选完立刻显示新路径（并按「已自定义」口径说明）",
              page.inner_text("#sandbox-path").strip() == "E:\\yd-demo-sandbox"
              and "已自定义" in page.inner_text("#sandbox-note"), page.inner_text("#sandbox-note"))
        page.click("#set-back")
        page.wait_for_timeout(300)
        check("★ 态势条「沙箱」跟着设置实时变（不再是环境变量那个旧值）",
              page.eval_on_selector("#posture-sandbox span", "e => e.textContent.trim()") == "E:\\yd-demo-sandbox",
              page.eval_on_selector("#posture-sandbox span", "e => e.textContent"))
        page.click("#btn-settings")
        page.wait_for_timeout(300)
        page.click('.setnav__item[data-set="safety"]')
        page.wait_for_timeout(250)
        page.click("#btn-clear-sandbox")
        page.wait_for_timeout(500)
        sandbox_saved = [c for c in calls("save_settings")
                         if c[1] and isinstance(c[1][0], dict) and "sandbox_dir" in c[1][0]]
        check("「清除（回到默认）」写空串并回到默认显示",
              sandbox_saved[-1][1][0]["sandbox_dir"] == ""
              and page.inner_text("#sandbox-path").strip() == "E:\\yindun-agent"
              and "默认" in page.inner_text("#sandbox-note"),
              str(sandbox_saved[-1:]))
        check("清除后也有如实提示（不是静默）",
              "默认" in page.eval_on_selector_all("#toast-root .toast", "els => els.map(e => e.textContent).join(' | ')"), "")

        # ── 8) 工作流界面入口已移除（引擎保留）──
        print("\n" + "=" * 78)
        print("【8】工作流界面层已移除（DOM + 源码双断言，防止悄悄复活）")
        print("=" * 78)
        page.click("#set-back")
        page.wait_for_timeout(250)
        check("DOM 里不存在工作流的按钮 / 面板 / 页签 / 模板容器",
              page.query_selector("#btn-workflow") is None
              and page.query_selector("#pane-workflow") is None
              and page.query_selector('.ptab[data-pane="workflow"]') is None
              and page.query_selector("#wf-templates") is None, "")
        # 源码级断言（grep 语义）：界面文件里不许再出现工作流面板与桥调用的残留。
        # 为什么是这几个词而不是裸 "workflow"：插件的 workflow_template 钩子是**内核保留能力**
        # （引擎没删），会被裸词误伤；这里只钉"界面面板 + 桥方法"。
        sources = {name: (WEB_DIR / name).read_text(encoding="utf-8")
                   for name in ("index.html", "app.js", "dev-mock.js")}
        banned = ("wf-templates", "btn-workflow", "btn-wf-", "wf-format", "wf-run",
                  "pane-workflow", "workflow_templates", "workflow_start", "workflow_status",
                  "workflow_execute", "workflow_approve", "workflow_export")
        leftovers = {n: [t for t in banned if t in s] for n, s in sources.items()}
        check("index.html / app.js / dev-mock.js 里没有任何工作流界面残留引用",
              not any(leftovers.values()), str(leftovers))
        check("app.js 里写明「界面入口已移除、引擎保留」（以后不会被当成漏删又接回来）",
              "引擎保留" in sources["app.js"], "")

        # ── 9) 闪发模式输入同步 ──
        print("\n" + "=" * 78)
        print("【9】闪发模式（输入同步 / Enter / Shift+Enter）")
        print("=" * 78)
        page.fill("#input", "主输入框的内容")
        page.click("#btn-mini")
        page.wait_for_timeout(300)
        check("进入闪发模式（浮条可见）", page.is_visible("#mini-input"))
        mini_value = page.input_value("#mini-input")
        check("浮条输入框带上了主输入框内容", mini_value == "主输入框的内容", mini_value)
        page.fill("#mini-input", "浮条新内容")
        page.wait_for_timeout(120)
        # 折叠态下打字不会实时回写主输入框；**展开时**必须把浮条里的草稿带回去（旧实现直接丢字）
        page.click("#btn-mini-expand")
        page.wait_for_timeout(250)
        main_value = page.input_value("#input")
        check("展开时浮条内容带回主输入框（不丢字）", main_value == "浮条新内容", main_value)
        page.click("#btn-mini")
        page.wait_for_timeout(250)
        page.fill("#mini-input", "浮条新内容")
        # Shift+Enter 必须是换行而不是发送（历史缺陷：会被当成发送）
        before_send_count = len(calls("send"))
        page.click("#mini-input")
        page.keyboard.press("Shift+Enter")
        page.wait_for_timeout(200)
        check("Shift+Enter 不触发发送", len(calls("send")) == before_send_count,
              f"{before_send_count} → {len(calls('send'))}")
        page.keyboard.press("Enter")
        page.wait_for_timeout(250)
        check("Enter 触发发送", len(calls("send")) > before_send_count,
              f"{before_send_count} → {len(calls('send'))}")

        # ── 10) 明暗切换 + 右栏折叠与窗口按钮归位 ──
        print("\n" + "=" * 78)
        print("【10】明暗切换 / 右栏折叠 / 窗口按钮归位")
        print("=" * 78)
        theme_before = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
        # ★ 明暗切换要有过渡动画（负责人反馈"太闪了"）：装个计数器，证明真的走了
        #   View Transitions 这条路径，而不是"啪一下"直接换主题。
        page.evaluate("() => { window.__vtCount = 0;"
                      " const orig = document.startViewTransition;"
                      " if (typeof orig === 'function') {"
                      "   document.startViewTransition = function (...a) {"
                      "     window.__vtCount += 1; return orig.apply(this, a); }; } }")
        page.click("#btn-theme")
        page.wait_for_timeout(500)
        theme_after = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
        check("切主题后 data-theme 改变", theme_before != theme_after, f"{theme_before} → {theme_after}")
        _vt = page.evaluate("() => window.__vtCount || 0")
        check("切主题走了页级过渡动画（startViewTransition 被调用，不再是一下闪过去）",
              _vt >= 1, str(_vt))
        # 无障碍：系统开了"减少动态效果"时**不应**做过渡（直接切）
        page.emulate_media(reduced_motion="reduce")
        page.evaluate("() => { window.__vtCount = 0; }")
        page.click("#btn-theme")
        page.wait_for_timeout(300)
        _vt_reduced = page.evaluate("() => window.__vtCount || 0")
        check("开了「减少动态效果」时不做过渡动画（尊重系统设置）", _vt_reduced == 0, str(_vt_reduced))
        page.emulate_media(reduced_motion="no-preference")
        page.click("#btn-toggle-right")
        page.wait_for_timeout(250)
        collapsed = page.evaluate("() => document.querySelector('.app').classList.contains('is-right-collapsed')")
        check("右栏能折叠", bool(collapsed))
        where = page.evaluate("""() => {
            const box = document.querySelector('#win-ctrls');
            if (!box) return 'none';
            const app = document.querySelector('.app');
            if (app.classList.contains('is-settings-open')) return 'head';
            return box.classList.contains('win-ctrls--panel') ? 'panel'
                 : (box.classList.contains('win-ctrls--head') ? 'head' : 'topbar');
        }""")
        check("折叠右栏时窗口按钮回到顶栏", where == "topbar", where)
        page.click("#btn-toggle-right")
        page.wait_for_timeout(250)
        check("再点一次可展开", not page.evaluate(
            "() => document.querySelector('.app').classList.contains('is-right-collapsed')"))

        # ── 11) 附件「＋」：真的触发 pick_files，且有可读反馈 ──
        print("\n" + "=" * 78)
        print("【11】附件「＋」按钮（正常 / 桥缺失两条路径）")
        print("=" * 78)
        chips_before = page.eval_on_selector_all("#chips .chip", "els => els.map(e => e.textContent)")
        picks_before = len(calls("pick_files"))
        page.click("#btn-attach")
        page.wait_for_timeout(500)
        check("点「＋」真的调用 pick_files（不是没收事件的死按钮）",
              len(calls("pick_files")) > picks_before,
              f"{picks_before} → {len(calls('pick_files'))}")
        chips_after = page.eval_on_selector_all("#chips .chip", "els => els.map(e => e.textContent)")
        check("点「＋」后附件条多出一项（界面真的生效）",
              len(chips_after) == len(chips_before) + 1,
              f"{len(chips_before)} → {len(chips_after)}")
        toast_text = page.eval_on_selector_all(
            "#toast-root .toast", "els => els.map(e => e.textContent).join(' | ')")
        check("点「＋」给出可读提示（已挂载 N 个附件）",
              "已挂载 1 个附件" in toast_text, repr(toast_text))
        # 桥缺失：必须走 call() 的「桥接未就绪」提示，而不是静默无反应
        page.evaluate("() => document.querySelectorAll('#toast-root .toast').forEach(e => e.remove())")
        page.evaluate("() => { window.__savedPickFiles = window.__YINDUN_MOCK__.api.pick_files;"
                      " delete window.__YINDUN_MOCK__.api.pick_files; }")
        page.click("#btn-attach")
        page.wait_for_timeout(350)
        toast_text = page.eval_on_selector_all(
            "#toast-root .toast", "els => els.map(e => e.textContent).join(' | ')")
        check("桥里没有 pick_files 时提示「桥接未就绪」（不再静默）",
              "桥接未就绪：pick_files" in toast_text, repr(toast_text))
        page.evaluate("() => { window.__YINDUN_MOCK__.api.pick_files = window.__savedPickFiles; }")

        # ── 12) Markdown 表格单元格：行内标记 / <br> / 注入防护 ──
        print("\n" + "=" * 78)
        print("【12】Markdown 表格单元格（粗体 / 行内代码 / <br> / 链接 / 注入防护）")
        print("=" * 78)
        # 恶意样例按仓库约定分段拼接：保持为纯字符串常量，不写成完整可执行载荷
        evil_img = "<img src=x on" + "error=alert(" + "1)>"
        evil_b = "<b>" + "不该生效" + "</b>"
        evil_br = "<br onmouse" + "over=alert(" + "1)>"
        evil_link = "[点我](java" + "script:alert(" + "1))"
        md_table = "\n".join([
            "| 列A | 列B | 列C | 列D | 列E | 列F | 列G | 列H | 列I | 列J |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            "| **代码分析** | `grep` 片段 | grep ) <br> 文件内容读取 | - 甲<br/>- 乙 "
            "| [官网](https://example.com/a?x=1&y=2) "
            f"| {evil_img} {evil_b} | x {evil_br} y | {evil_link} | 1. 第一<br>2. 第二 "
            "| `<br>` 应该原样显示 |",
        ])
        page.evaluate("(t) => window.yindun.onEvent('intermediate_result', t)", md_table)
        page.wait_for_timeout(300)
        cells = page.evaluate("""() => {
          const t = [...document.querySelectorAll('#chat .entry__body table')].pop();
          if (!t) return null;
          return [...t.querySelectorAll('tbody td')].map((td) => ({
            text: td.textContent, html: td.innerHTML,
            strong: [...td.querySelectorAll('strong')].map((e) => e.textContent),
            code: [...td.querySelectorAll('code')].map((e) => e.textContent),
            brs: td.querySelectorAll('br').length,
            imgs: td.querySelectorAll('img').length,
            bs: td.querySelectorAll('b').length,
            links: [...td.querySelectorAll('a')].map((a) => ({
              text: a.textContent, href: a.getAttribute('href'), rel: a.getAttribute('rel') })),
          }));
        }""")
        check("表格样例行渲染出 10 个单元格", isinstance(cells, list) and len(cells) == 10,
              str(len(cells) if isinstance(cells, list) else cells))
        if isinstance(cells, list) and len(cells) == 10:
            c = cells
            check("单元格内 **粗体** 渲染为 <strong>（不再出现字面 **）",
                  c[0]["strong"] == ["代码分析"] and "**" not in c[0]["text"],
                  f"{c[0]['strong']} / {c[0]['text']!r}")
            check("单元格内 `行内代码` 渲染为 <code>", c[1]["code"] == ["grep"], str(c[1]["code"]))
            check("单元格内 <br> 变成真正换行（DOM 有 br、文本无字面 <br>）",
                  c[2]["brs"] == 1 and "<br>" not in c[2]["text"]
                  and "grep" in c[2]["text"] and "文件内容读取" in c[2]["text"],
                  f"brs={c[2]['brs']} text={c[2]['text']!r}")
            check("单元格内 <br/> 同样换行，且无序列表标记变圆点",
                  c[3]["brs"] == 1 and "• 甲" in c[3]["text"] and "• 乙" in c[3]["text"],
                  f"brs={c[3]['brs']} text={c[3]['text']!r}")
            links = c[4]["links"]
            check("单元格内链接渲染为安全链接（http(s) + noopener）",
                  len(links) == 1 and links[0]["href"] == "https://example.com/a?x=1&y=2"
                  and "noopener" in (links[0]["rel"] or ""), str(links))
            check("单元格里的 img 不被当成 HTML 执行（DOM 无 img 元素）",
                  c[5]["imgs"] == 0, f"img={c[5]['imgs']}")
            check("单元格里的 <b> 不被当成 HTML 渲染（DOM 无 b 元素）",
                  c[5]["bs"] == 0, f"b={c[5]['bs']}")
            check("恶意标签以转义后的原文可见（文本里能看到原样内容）",
                  evil_img in c[5]["text"] and evil_b in c[5]["text"], repr(c[5]["text"]))
            check("带属性的 <br on…> 不放行（不产生 br 元素）",
                  c[6]["brs"] == 0 and evil_br in c[6]["text"],
                  f"brs={c[6]['brs']} text={c[6]['text']!r}")
            check("javascript: 链接不放行（不产生 a 元素）",
                  c[7]["links"] == [] and "javascript:" in c[7]["text"],
                  f"links={c[7]['links']} text={c[7]['text']!r}")
            check("有序列表在同一单元格内保持可读的多行（数字标记不受损）",
                  c[8]["brs"] == 1 and "1. 第一" in c[8]["text"] and "2. 第二" in c[8]["text"],
                  f"brs={c[8]['brs']} text={c[8]['text']!r}")
            check("行内代码里的 <br> 原样显示（不被换行替换拆开）",
                  c[9]["code"] == ["<br>"] and c[9]["brs"] == 0 and "<br>" in c[9]["text"],
                  f"code={c[9]['code']} brs={c[9]['brs']} text={c[9]['text']!r}")

        # ── 13) 等待态 / 空回复兜底 / trace 落点（真机事故固化）──
        # 事故（负责人实测）：问「我的桌面有几张图片？」→ 助手气泡是**空的**、整段生成期没有
        # 任何"思考中"提示，重启后那一轮像没发生过。后端已修（直答快路径不再吞工具调用、
        # 终止文本保证非空），这里把前端的三条反馈缺口钉死：发送即等待态、等待态被"升级"
        # 而不是并存两条、空终稿有如实兜底且不留空气泡、trace 没有流式气泡时也有落点。
        # 全部用 #mock 沙盘 + 直接派发事件构造时序（沙盘的 send 是 noop，不会自己发事件）。
        print("\n" + "=" * 78)
        print("【13】等待态（发送即出现）/ 空回复兜底 / trace 落点")
        print("=" * 78)
        errors_before = len(errors)
        # 先给 §9 触发的那一轮补终止事件并回到空闲（沙盘不会自己发终止事件）
        page.evaluate("() => window.yindun.onEvent('finished', '上一轮收口示例回复。')")
        page.evaluate("() => window.yindun.onEvent('state', {busy: false})")
        page.wait_for_timeout(150)
        entering = page.evaluate("""() => ({
            waitings: document.querySelectorAll('#chat .waiting').length,
            inputDisabled: document.getElementById('input').disabled })""")
        check("收口后回到空闲（无残留等待指示、输入框可用）",
              entering["waitings"] == 0 and not entering["inputDisabled"], str(entering))

        # 13a) 发送即等待态：不喂任何后端事件，等待指示必须已经在那儿
        page.fill("#input", "我的桌面有几张图片？")
        users_before = page.eval_on_selector_all("#chat .row--user", "els => els.length")
        entries_before = page.eval_on_selector_all("#chat .entry", "els => els.length")
        page.click("#btn-send")
        wait = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#chat .entry')];
            const row = rows[rows.length - 1];
            const w = row ? row.querySelector('.waiting') : null;
            return {
                count: rows.length,
                waiting: !!w,
                dots: w ? w.querySelectorAll('.typing i').length : 0,
                text: row ? row.textContent.trim() : '',
                thinking: !!(row && row.querySelector('.entry__body.is-thinking')),
                users: document.querySelectorAll('#chat .row--user').length,
                inputDisabled: document.getElementById('input').disabled,
            };
        }""")
        check("发送后**立刻**出现等待指示（不等后端第一条事件）",
              wait["waiting"] and wait["dots"] == 3, str(wait))
        check("等待指示含「正在思考…」文字（忙态在对话区真的看得见）",
              "正在思考" in wait["text"], repr(wait["text"])[:80])
        check("等待态是用户消息之后新开的一条助手条目",
              wait["count"] == entries_before + 1 and wait["users"] == users_before + 1,
              f"{entries_before}→{wait['count']} / {users_before}→{wait['users']}")
        check("等待期间输入框进入忙态（与发送按钮的停止态一致）", wait["inputDisabled"] is True)

        # 13b) 升级：status → 工具行、intermediate_result → 流式正文，都落在同一条气泡里
        page.evaluate("() => window.yindun.onEvent('status', '[快速回答] 直接回答问题...')")
        page.wait_for_timeout(120)
        up1 = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#chat .entry')];
            const row = rows[rows.length - 1];
            return { count: rows.length, waiting: !!row.querySelector('.waiting'),
                     tools: [...row.querySelectorAll('.tool-line__text')].map(e => e.textContent) };
        }""")
        check("收到 status → 原地升级出工具行（不是并存第二条气泡）",
              up1["count"] == wait["count"] and up1["tools"] == ["[快速回答] 直接回答问题..."], str(up1))
        check("升级后等待指示被摘掉（不会「正在思考…」与工具行同时挂着）", not up1["waiting"], str(up1))
        page.evaluate("() => window.yindun.onEvent('intermediate_result', '桌面上一共 **3** 张图片。')")
        page.wait_for_timeout(120)
        up2 = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#chat .entry')];
            const row = rows[rows.length - 1];
            return { count: rows.length, waiting: !!row.querySelector('.waiting'),
                     thinking: !!row.querySelector('.entry__body.is-thinking'),
                     body: row.querySelector('.entry__body').textContent.trim(),
                     strong: [...row.querySelectorAll('.entry__body strong')].map(e => e.textContent),
                     tools: row.querySelectorAll('.tool-line').length };
        }""")
        check("收到 intermediate_result → 同一条气泡里出流式正文（工具行仍留在它里面）",
              up2["count"] == wait["count"] and "桌面上一共" in up2["body"]
              and up2["strong"] == ["3"] and up2["tools"] == 1, str(up2))
        check("流式正文到达后不再显示等待/思考标记（不一边出字一边转圈）",
              not up2["waiting"] and not up2["thinking"], str(up2))

        # 13c-1) 空终稿但已有流式正文：正文优先，绝不把已收到的内容抹掉
        page.evaluate("() => window.yindun.onEvent('finished', '')")
        page.wait_for_timeout(150)
        fin1 = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#chat .entry')];
            const row = rows[rows.length - 1];
            return { count: rows.length, body: row.querySelector('.entry__body').textContent.trim(),
                     tag: (row.querySelector('.bubble__tag') || {}).textContent || '' };
        }""")
        check("空终稿不抹掉已流式的正文（内容还在，只补「无内容」状态标记）",
              "桌面上一共" in fin1["body"] and fin1["tag"] == "无内容", str(fin1))
        check("空终稿原地收口（条目数量不变，不再画一条）", fin1["count"] == wait["count"], str(fin1["count"]))

        # 13c-2) 整条气泡都没有内容时的空终稿（事故第一现场）：兜底文案，绝不留空气泡
        page.evaluate("() => window.yindun.onEvent('state', {busy: false})")
        page.wait_for_timeout(120)
        page.fill("#input", "再试一次：我的桌面有几张图片？")
        page.click("#btn-send")
        page.wait_for_timeout(120)
        page.evaluate("() => window.yindun.onEvent('finished', '')")
        page.wait_for_timeout(150)
        fin2 = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#chat .entry')];
            const row = rows[rows.length - 1];
            const body = row.querySelector('.entry__body');
            return { count: rows.length, body: body.textContent.trim(),
                     tag: (row.querySelector('.bubble__tag') || {}).textContent || '',
                     emptyReply: body.classList.contains('is-empty-reply'),
                     waiting: !!row.querySelector('.waiting') };
        }""")
        check("finished 收到空串 → 显示如实兜底文案（不是空气泡）",
              "没有给出内容" in fin2["body"] and "再问一次" in fin2["body"], repr(fin2["body"])[:70])
        check("兜底气泡带「无内容」标签与空回复样式（观感上区别于正常回答）",
              fin2["tag"] == "无内容" and fin2["emptyReply"], str(fin2))
        check("兜底文案在界面上真实可见（可见性取自布局，不是只看 DOM）",
              page.is_visible("#chat .entry__body.is-empty-reply"), str(fin2))
        check("空终稿不留等待指示（不会与兜底文案并存）", not fin2["waiting"], str(fin2))

        # 13d) trace 落点：没有流式气泡时也必须看得见（旧实现直接丢帧）
        page.evaluate("""() => window.yindun.onEvent('trace', {
            seq: 7, kind: 'tool', name: '列出本地文件', detail: '沙箱内 只读扫描', status: 'ok', ms: 210 })""")
        page.wait_for_timeout(150)
        tr = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#chat .entry')];
            const row = rows[rows.length - 1];
            const t = row.querySelector('.entry__trace .trace');
            return { count: rows.length, hasTrace: !!t,
                     text: t ? t.textContent.trim() : '', waiting: !!row.querySelector('.waiting') };
        }""")
        check("没有流式气泡时 trace 也有可见落点（就地现建条目，不再丢帧）",
              tr["hasTrace"] and "列出本地文件" in tr["text"], str(tr))
        check("trace 落点是新的一条条目（不覆盖上一条兜底气泡）",
              tr["count"] == fin2["count"] + 1, f"{fin2['count']} → {tr['count']}")
        check("trace 落地时不再显示等待指示（轨迹本身就是活动证据）", not tr["waiting"], str(tr))

        # 13e) 组员审查报告点名的场景：**每轮最早的那条隐私网关轨迹不能被丢掉**
        # （他们的取证：审计里有 privacy_sensitive，对话流里却完全没有轨迹——因为旧实现的
        #  sendText 不建气泡、trace 只在"已有流式气泡"时才渲染，于是每轮最早的事件必然丢帧。）
        # 现在 sendText 之前就建了等待气泡，所以"网关判定"应当看得见。
        # 先回到空闲（上面几步故意留着忙态，忙时输入框是禁用的）——这正是真实一轮的起点。
        page.evaluate("() => window.yindun.onEvent('state', {busy: false})")
        page.wait_for_timeout(150)
        page.fill("#input", "我的手机号是 13800138000，帮我记一下")
        page.press("#input", "Enter")
        page.wait_for_timeout(150)
        page.evaluate("""() => window.yindun.onEvent('trace', {
            seq: 1, kind: 'judge', name: '隐私隔离网关', status: 'ok', ms: 12,
            detail: '识别到敏感实体，已在本机替换为占位符' })""")
        page.wait_for_timeout(200)
        gw = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#chat .entry')];
            const row = rows[rows.length - 1];
            const t = row.querySelector('.entry__trace');
            return { hasTrace: !!t, text: t ? t.textContent.replace(/\\s+/g, ' ').trim() : '',
                     hasBody: !!row.querySelector('.entry__body'), rows: rows.length };
        }""")
        check("★ 发送后立刻出现的隐私网关轨迹看得见（报告里「网关作用找不到」的回归位）",
              gw["hasTrace"] and "隐私隔离网关" in gw["text"], str(gw))
        check("网关轨迹落在本轮新建的条目里（既没丢帧，也没塞进上一轮）",
              gw["hasBody"], str(gw))

        # 收口：回到空闲 + 画布上没有任何"内容为空"的条目。
        # 注意这条 state 到达时上一条 trace 气泡还开着（本时序故意漏掉终止事件）——
        # 它必须按空回复口径收口，而不是永远转圈或把引用留给下一轮。
        page.evaluate("() => window.yindun.onEvent('state', {busy: false})")
        page.wait_for_timeout(150)
        ending = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#chat .entry')];
            const last = rows[rows.length - 1];
            return { waitings: document.querySelectorAll('#chat .waiting').length,
                     inputDisabled: document.getElementById('input').disabled,
                     lastBody: last ? last.querySelector('.entry__body').textContent.trim() : '',
                     lastTag: last && last.querySelector('.bubble__tag')
                              ? last.querySelector('.bubble__tag').textContent : '',
                     air: rows.filter(r => !r.textContent.trim()).length };
        }""")
        check("时序跑完后没有遗留的等待指示、输入框恢复可用",
              ending["waitings"] == 0 and not ending["inputDisabled"], str(ending))
        check("终止事件丢失时，空闲状态把还开着的气泡收口成兜底文案（不跨轮悬挂）",
              "没有给出内容" in ending["lastBody"] and ending["lastTag"] == "无内容", str(ending))
        check("画布上不存在任何「内容为空」的助手条目（空气泡清点）", ending["air"] == 0, str(ending))
        check("等待态/空回复/trace 时序全程无 JS 运行时错误", len(errors) == errors_before,
              "；".join(errors[errors_before:][:3]))

        # ── 14) 审计页安全工具：结构化 UI（不再贴 ==== 纯文本）──
        print("\n" + "=" * 78)
        print("【14】安全工具结构化渲染（隐私健康体检 / 模型行为画像）")
        print("=" * 78)
        page.click("#btn-audit")
        page.wait_for_selector("#pane-audit.is-active", timeout=5000)
        check("审计页出现两个安全工具按钮（显式点击才运行）",
              page.is_visible("#btn-health") and page.is_visible("#btn-behavior"))

        # 14a) 进行中可见状态：mock 延迟 350ms，点下去立刻能看到 loading
        page.evaluate("""() => {
          window.__origHealth = window.__YINDUN_MOCK__.api.health_scan;
          window.__YINDUN_MOCK__.api.health_scan = (...a) =>
            new Promise((resolve) => setTimeout(() => resolve(window.__origHealth(...a)), 350));
        }""")
        page.click("#btn-health")
        page.wait_for_timeout(80)
        loading = page.evaluate("""() => {
          const el = document.querySelector('#tool-result .tool-loading');
          return { visible: !!el, text: el ? el.textContent : '',
                   disabled: document.getElementById('btn-health').disabled };
        }""")
        check("体检运行中有可见的进行中状态（不是点完毫无反馈）",
              loading["visible"] and "正在扫描" in loading["text"], str(loading))
        check("运行中按钮暂时禁用（避免重复触发）", loading["disabled"], str(loading))
        page.wait_for_selector("#tool-result .health-group", timeout=6000)

        # 14b) 体检：指标 + 严重程度分组 + 分级着色 + 空态/原文
        health = page.evaluate("""() => {
          const token = (name) => {
            const d = document.createElement('div');
            d.style.color = `var(${name})`;
            document.body.appendChild(d);
            const c = getComputedStyle(d).color;
            d.remove();
            return c;
          };
          const groups = [...document.querySelectorAll('#tool-result .health-group')];
          return {
            metrics: [...document.querySelectorAll('#tool-result .tool-metric')].map((m) => ({
              num: (m.querySelector('.tool-metric__num') || {}).textContent || '',
              label: (m.querySelector('.tool-metric__label') || {}).textContent || '',
              cls: m.className,
              color: getComputedStyle(m.querySelector('.tool-metric__num')).color,
            })),
            severities: groups.map((g) => g.dataset.severity),
            badges: groups.map((g) => ({
              sev: g.dataset.severity,
              cls: (g.querySelector('.sev-badge') || {}).className || '',
              color: getComputedStyle(g.querySelector('.sev-badge')).color,
            })),
            cats: [...new Set([...document.querySelectorAll('#tool-result .health-finding__cat')]
              .map((e) => e.textContent))],
            matches: [...document.querySelectorAll('#tool-result .health-finding__matches code')]
              .map((e) => e.textContent),
            lines: [...document.querySelectorAll('#tool-result .health-finding__line')]
              .map((e) => e.textContent),
            rawOpen: !!(document.querySelector('#tool-result .tool-raw') || {}).open,
            rawHasSeparator: !!document.querySelector('#tool-result .tool-raw pre') &&
              document.querySelector('#tool-result .tool-raw pre').textContent.includes('======'),
            danger: token('--danger-500'), warn: token('--warn-500'),
          };
        }""")
        check("体检渲染出 3 个指标（扫描文件 / 发现问题 / 风险评分）",
              len(health["metrics"]) == 3 and
              all(k in "".join(m["label"] for m in health["metrics"])
                  for k in ("扫描文件", "发现问题", "风险评分")),
              str(health["metrics"]))
        check("指标数字来自结构化字段（121 扫描 / 4 问题 / 43 分）",
              [m["num"] for m in health["metrics"]] == ["121", "4", "43"],
              str([m["num"] for m in health["metrics"]]))
        check("风险评分按中风险着色（43 分 → warn 色）",
              "tool-metric--warn" in health["metrics"][2]["cls"] and
              health["metrics"][2]["color"] == health["warn"],
              f"{health['metrics'][2]['cls']} / {health['metrics'][2]['color']} vs {health['warn']}")
        check("发现按严重程度分组，顺序为 绝密 → 机密 → 内部",
              health["severities"] == ["绝密", "机密", "内部"], str(health["severities"]))
        sev_colors = {b["sev"]: b["color"] for b in health["badges"]}
        check("绝密/机密用红色、内部用琥珀色（按分级着色）",
              sev_colors.get("绝密") == health["danger"] and
              sev_colors.get("机密") == health["danger"] and
              sev_colors.get("内部") == health["warn"],
              str(health["badges"]))
        check("每条发现带类别中文标签（敏感文件 / 敏感内容 / 权限异常）",
              set(health["cats"]) == {"敏感文件", "敏感内容", "权限异常"}, str(health["cats"]))
        check("匹配样例与行号可见（不是只报个文件名）",
              "138****5678" in health["matches"] and any("第 12 行" in x for x in health["lines"]),
              str(health["matches"]) + " / " + str(health["lines"]))
        check("输出区默认不再出现 ====== 报告分隔线",
              "======" not in page.inner_text("#tool-result"),
              repr(page.inner_text("#tool-result")[:80]))
        check("原始报告默认折叠，但里面保留后端原文（留证/排障）",
              health["rawOpen"] is False and health["rawHasSeparator"] and
              not page.is_visible("#tool-result .tool-raw pre"), str(health))
        page.click("#tool-result .tool-raw summary")
        page.wait_for_timeout(120)
        check("展开「查看原始报告」后能看到原文",
              page.is_visible("#tool-result .tool-raw pre") and
              "======" in page.inner_text("#tool-result .tool-raw pre"))
        page.click("#tool-result .tool-raw summary")
        page.wait_for_timeout(100)

        # 14c) 画像：指标 + 工具横条 + 操作分布 + 敏感路径 + 异常着色
        page.evaluate("() => { window.__origBehavior = window.__YINDUN_MOCK__.api.behavior_profile; }")
        page.click("#btn-behavior")
        page.wait_for_selector("#tool-result .anomaly-list", timeout=5000)
        behavior = page.evaluate("""() => {
          const token = (name) => {
            const d = document.createElement('div');
            d.style.color = `var(${name})`;
            document.body.appendChild(d);
            const c = getComputedStyle(d).color;
            d.remove();
            return c;
          };
          return {
            metrics: [...document.querySelectorAll('#tool-result .tool-metric')].map((m) => ({
              num: (m.querySelector('.tool-metric__num') || {}).textContent || '',
              cls: m.className,
            })),
            hbars: [...document.querySelectorAll('#tool-result .hbar')].map((b) => ({
              name: (b.querySelector('.hbar__name') || {}).textContent || '',
              width: (b.querySelector('.hbar__track > i') || {}).style ?
                (b.querySelector('.hbar__track > i') || {}).style.width : '',
              num: (b.querySelector('.hbar__num') || {}).textContent || '',
            })),
            ops: [...document.querySelectorAll('#tool-result .tool-op')].map((e) => e.textContent),
            paths: [...document.querySelectorAll('#tool-result .tool-paths li')].map((e) => e.textContent),
            anomalies: [...document.querySelectorAll('#tool-result .anomaly')].map((a) => ({
              sev: a.dataset.severity,
              cls: (a.querySelector('.sev-badge') || {}).className || '',
              color: getComputedStyle(a.querySelector('.sev-badge')).color,
              label: (a.querySelector('.anomaly__head b') || {}).textContent || '',
            })),
            rawOpen: !!(document.querySelector('#tool-result .tool-raw') || {}).open,
            metricsCount: (document.querySelector('#tool-result .tool-metrics') || {}).dataset
              ? document.querySelector('#tool-result .tool-metrics').dataset.count : '',
            metricsGrid: getComputedStyle(document.querySelector('#tool-result .tool-metrics'))
              .gridTemplateColumns,
            metricsCols: getComputedStyle(document.querySelector('#tool-result .tool-metrics'))
              .gridTemplateColumns.trim().split(/[ ]+/).length,
            targetText: (document.querySelector('#tool-result .tool-target') || {}).textContent || '',
            anomalyTimes: [...document.querySelectorAll('#tool-result .anomaly__time')]
              .map((e) => e.textContent),
            anomalyTitles: [...document.querySelectorAll('#tool-result .anomaly__time')]
              .map((e) => e.getAttribute('title') || ''),
            danger: token('--danger-500'), warn: token('--warn-500'), muted: token('--text-3'),
          };
        }""")
        check("画像渲染出 4 个指标（工具调用 / 唯一路径 / 路径跳跃 / 风险评分）",
              [m["num"] for m in behavior["metrics"]] == ["37", "14", "7", "62"],
              str([m["num"] for m in behavior["metrics"]]))
        check("风险评分 62 按高风险着色（critical 色）",
              "tool-metric--critical" in behavior["metrics"][3]["cls"], str(behavior["metrics"][3]))
        check("工具调用分布用横条呈现（5 项，按次数降序）",
              len(behavior["hbars"]) == 5 and behavior["hbars"][0]["name"] == "读取文件" and
              behavior["hbars"][0]["width"] == "100%" and "18" in behavior["hbars"][0]["num"],
              str(behavior["hbars"][:2]))
        check("操作类型分布：只列真有次数的档（读取 26 / 写入 6 / 删除 2）",
              len(behavior["ops"]) == 3 and "26读取" in behavior["ops"] and
              "6写入" in behavior["ops"] and "2删除" in behavior["ops"],
              str(behavior["ops"]))
        check("★ 0 值档不渲染灰盒（mock 的 execute=0 不出现在结果里）",
              not any("执行" in o for o in behavior["ops"]), str(behavior["ops"]))
        check("★ 四张指标卡走 2×2 等宽网格（窄栏里不排成 3+1 孤格）",
              behavior["metricsCount"] == "4" and behavior["metricsCols"] == 2
              and len(set(behavior["metricsGrid"].split())) == 1,
              str(behavior["metricsGrid"]))
        _target = behavior["targetText"].strip()
        check("★ 时间戳压到 MM-DD HH:MM（不再出现带 T 的 ISO 串、年份或微秒）",
              _target.startswith("qwen") and "T" not in _target and "2026-" not in _target
              and "09:12:00" not in _target and "10:24:08" not in _target
              and "09:12" in _target and _target.endswith("10-16 10:24"),
              repr(_target))
        check("异常时间同样用短格式（MM-DD HH:MM）且完整时间放 title 备查",
              behavior["anomalyTimes"] and all(len(t) == 11 and t[5] == " " for t in behavior["anomalyTimes"])
              and all("2026-10-16T" in ti for ti in behavior["anomalyTitles"]),
              str(list(zip(behavior["anomalyTimes"], behavior["anomalyTitles"]))) + " || "
              + behavior.get("boxHead", "?"))
        check("被访问的敏感路径逐条列出（2 个）",
              len(behavior["paths"]) >= 2 and
              any("id_rsa" in p for p in behavior["paths"]), str(behavior["paths"]))
        check("异常按 severity 排序并着色（high=红 / medium=琥珀 / low=灰）",
              [a["sev"] for a in behavior["anomalies"]] == ["high", "medium", "low"] and
              behavior["anomalies"][0]["color"] == behavior["danger"] and
              behavior["anomalies"][1]["color"] == behavior["warn"] and
              behavior["anomalies"][2]["color"] == behavior["muted"],
              str(behavior["anomalies"]))
        check("异常类型给中文标签（敏感路径访问 / 路径跳跃 / 非工作时间）",
              [a["label"] for a in behavior["anomalies"]] ==
              ["敏感路径访问", "路径跳跃", "非工作时间"], str(behavior["anomalies"]))
        check("画像输出区不再出现 ====== 报告分隔线",
              "======" not in page.inner_text("#tool-result"),
              repr(page.inner_text("#tool-result")[:80]))
        check("画像的原始报告默认折叠", behavior["rawOpen"] is False, str(behavior["rawOpen"]))

        # 14d) 无发现：正面空态（而不是"0 项"一行）
        page.evaluate("""() => {
          window.__YINDUN_MOCK__.api.health_scan = () => Promise.resolve({
            ok: true, target: 'E:/demo/clean',
            summary: '======\\n隐盾隐私健康体检报告\\n✓ 未发现隐私风险\\n======',
            json: JSON.stringify({
              scan_root: 'E:/demo/clean', scan_time: '2026-10-16 10:30:00',
              total_files: 9, scanned_files: 9, findings_count: 0,
              risk_score: 0, findings: [],
            }),
          });
        }""")
        page.click("#btn-health")
        page.wait_for_selector("#tool-result .tool-empty", timeout=5000)
        empty = page.evaluate("""() => {
          const el = document.querySelector('#tool-result .tool-empty');
          return {
            text: el ? el.textContent : '',
            visible: !!el && el.getBoundingClientRect().height > 0,
            groups: document.querySelectorAll('#tool-result .health-group').length,
            nums: [...document.querySelectorAll('#tool-result .tool-metric__num')].map((e) => e.textContent),
            okMetric: !!document.querySelector('#tool-result .tool-metric--ok'),
          };
        }""")
        check("无发现时给正面空态（未发现隐私风险 + 扫描依据，一行内说清）",
              empty["visible"] and "未发现隐私风险" in empty["text"] and
              "扫描 9 个文件" in empty["text"] and len(empty["text"]) < 70, str(empty))
        check("空态下不渲染空的分组容器", empty["groups"] == 0, str(empty["groups"]))
        check("无发现时问题数与风险评分如实为 0，并用安全色",
              empty["nums"] == ["9", "0", "0"] and empty["okMetric"], str(empty))

        # 14e) 失败：原样显示后端 error，按钮可重试
        page.evaluate("""() => {
          window.__YINDUN_MOCK__.api.behavior_profile = () => Promise.resolve({
            ok: false, error: 'RuntimeError: 审计日志不可读（回归样例）',
          });
        }""")
        page.click("#btn-behavior")
        page.wait_for_selector("#tool-result .tool-fail", timeout=5000)
        fail = page.evaluate("""() => ({
          text: (document.querySelector('#tool-result .tool-fail') || {}).textContent || '',
          disabled: document.getElementById('btn-behavior').disabled,
        })""")
        check("失败时原样显示后端 error（不吞错、不改写）",
              "执行失败" in fail["text"] and
              "RuntimeError: 审计日志不可读（回归样例）" in fail["text"], str(fail))
        check("失败后按钮恢复可用（可以重试）", not fail["disabled"], str(fail))

        # 14f) 无异常：如实说明"未发现异常"
        base = page.evaluate("async () => await window.__origBehavior()")
        base["anomalies"] = []
        page.evaluate("""(payload) => {
          window.__YINDUN_MOCK__.api.behavior_profile = () => Promise.resolve(payload);
        }""", base)
        page.click("#btn-behavior")
        page.wait_for_selector("#tool-result .tool-empty", timeout=5000)
        check("无异常时如实说明「未发现异常行为」（不是留空或假装成功）",
              "未发现异常行为" in page.inner_text("#tool-result .tool-empty"),
              repr(page.inner_text("#tool-result .tool-empty")))
        # 还原 mock，避免影响后续用例
        page.evaluate("""() => {
          window.__YINDUN_MOCK__.api.behavior_profile = window.__origBehavior;
          window.__YINDUN_MOCK__.api.health_scan = window.__origHealth;
        }""")

        # ── 15) 设置页：内容限宽居中（1920 / 1000 两侧留白相等而且不横向溢出）──
        print("\n" + "=" * 78)
        print("【15】设置页内容居中（宽窗口不拉满 / 窄窗口不横向滚动）")
        print("=" * 78)
        page.click("#btn-settings")
        page.wait_for_selector("#settings-view:not([hidden])", timeout=5000)
        page.click('.setnav__item[data-set="appearance"]')
        page.wait_for_timeout(200)

        def settings_gaps(width: int) -> dict:
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(350)
            return page.evaluate("""() => {
              const main = document.querySelector('.setmain');
              const body = document.querySelector('.setmain__body');
              const head = document.querySelector('.setmain__head');
              const mr = main.getBoundingClientRect();
              const br = body.getBoundingClientRect();
              const hr = head.getBoundingClientRect();
              return {
                vw: window.innerWidth,
                left: br.left - mr.left,
                right: mr.right - br.right,
                headLeft: hr.left - mr.left,
                headRight: mr.right - hr.right,
                bodyWidth: br.width,
                rectWidth: mr.width,
                clientWidth: main.clientWidth,
                docOverflow: document.documentElement.scrollWidth - window.innerWidth,
                mainOverflow: main.scrollWidth - main.clientWidth,
                bodyOverflow: body.scrollWidth - body.clientWidth,
              };
            }""")

        g1920 = settings_gaps(1920)
        check("1920：设置内容左右留白相等（误差 ≤2px）",
              abs(g1920["left"] - g1920["right"]) <= 2, str(g1920))
        check("1920：内容限宽居中而不是拉满（≤1000px 且两侧确有留白）",
              g1920["bodyWidth"] <= 1000.5 and g1920["left"] > 100, str(g1920))
        check("1920：设置标题与内容列左右边缘对齐",
              abs(g1920["headLeft"] - g1920["left"]) <= 2 and
              abs(g1920["headRight"] - g1920["right"]) <= 2, str(g1920))
        check("1920：无横向滚动（文档 / 滚动容器 / 内容列都没有溢出）",
              g1920["docOverflow"] <= 0 and g1920["mainOverflow"] <= 0 and
              g1920["bodyOverflow"] <= 0, str(g1920))

        g1000 = settings_gaps(1000)
        check("1000：设置内容左右留白相等（误差 ≤2px）",
              abs(g1000["left"] - g1000["right"]) <= 2, str(g1000))
        # clientWidth 扣掉了两侧的滚动条槽（scrollbar-gutter），所以"占满可用区"
        # 要拿主区的 border-box 宽度来算，否则会误判成内容没铺满。
        check("1000：窄窗口回落到满宽（内容 + 两侧留白正好占满可用区）",
              abs((g1000["left"] + g1000["bodyWidth"] + g1000["right"]) -
                  g1000["rectWidth"]) <= 2 and
              g1000["bodyWidth"] >= g1000["clientWidth"] - 1, str(g1000))
        check("1000：无横向滚动（文档 / 滚动容器 / 内容列都没有溢出）",
              g1000["docOverflow"] <= 0 and g1000["mainOverflow"] <= 0 and
              g1000["bodyOverflow"] <= 0, str(g1000))
        page.set_viewport_size({"width": 1440, "height": 900})
        page.wait_for_timeout(200)

        # ── 16) 模型选择器：两级弹层 + 无搜索框（负责人要求照 ZCode）──
        print("\n" + "=" * 78)
        print("【16】模型选择器：两级（供应商 → 模型）/ 无搜索框 / 管理模型")
        print("=" * 78)
        # §15 停在设置视图里（设置会隐藏顶栏与输入区），先回到对话视图
        page.click("#set-back")
        page.wait_for_timeout(300)
        check("回到对话视图（后续交互都在输入区一带）",
              not page.evaluate("() => document.querySelector('.app').classList.contains('is-settings-open')"), "")
        page.click("#model-trigger")
        page.wait_for_timeout(300)
        check("弹层里**没有**搜索框（D：照 ZCode 的形态）",
              page.query_selector("#model-pop input") is None
              and page.query_selector("#model-search") is None, "")
        src_index = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        src_app = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        check("源码里也不再引用 model-search / model-list（整条老路径退场）",
              "model-search" not in src_index and "model-search" not in src_app
              and "model-list" not in src_index, "")
        prov_opts = page.eval_on_selector_all(".mpick__prov", "els => els.map(e => e.dataset.pkey)")
        check("一级列出供应商，并按三档分组（保留数据边界说明）",
              len(prov_opts) >= 3
              and page.eval_on_selector_all(".mpick__gt", "els => els.length") >= 2, str(prov_opts))
        check("默认展开当前模型所在供应商（一级有 is-open）",
              page.eval_on_selector_all(".mpick__prov.is-open", "els => els.length") == 1, "")
        check("二级里当前模型打勾（一眼看出在用哪个）",
              page.eval_on_selector_all("#mpick-models .model-opt.is-cur .model-opt__tick",
                                        "els => els.length") == 1, "")
        page.click('.mpick__prov[data-pkey="wan::open.bigmodel.cn::0"]')
        page.wait_for_timeout(250)
        check("点一级供应商 → 弹层保持打开（重绘不会把它自己关掉）",
              page.is_visible("#model-pop"), "")
        sel_models = page.eval_on_selector_all(
            "#mpick-models [data-model]", "els => els.map(e => ({n: e.dataset.model, tags: [...e.querySelectorAll('.mtag')].map(t => t.textContent)}))")
        check("二级列出该供应商的模型，带标签",
              any(m["n"] == "云端GLM" for m in sel_models)
              and any(m["tags"] == ["低成本"] for m in sel_models), str(sel_models))
        page.click('#mpick-models [data-model="云端GLM"]')
        page.wait_for_timeout(450)
        check("选模型 → 弹层关闭 + 触发器文案变成「供应商 / 模型」",
              not page.is_visible("#model-pop")
              and page.inner_text("#model-value").strip() == "open.bigmodel.cn / 云端GLM",
              page.inner_text("#model-value"))
        # 键盘：↑↓ 移动、Enter 进二级/选中、Esc 关闭
        page.click("#model-trigger")
        page.wait_for_timeout(250)
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        page.wait_for_timeout(250)
        check("键盘：↓ 移动 + Enter（供应商列 → 进入模型列，且有高亮项）",
              page.eval_on_selector_all("#mpick-models .is-hl", "els => els.length") == 1, "")
        page.keyboard.press("Enter")
        page.wait_for_timeout(450)
        check("键盘 Enter 在模型列选中 → 弹层关闭", not page.is_visible("#model-pop"), "")
        page.click("#model-trigger")
        page.wait_for_timeout(250)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        check("键盘 Esc 关闭弹层", not page.is_visible("#model-pop"), "")
        page.click("#model-trigger")
        page.wait_for_timeout(250)
        page.click("#posture")                      # 点弹层外部
        page.wait_for_timeout(250)
        check("点弹层外部关闭", not page.is_visible("#model-pop"), "")
        page.click("#model-trigger")
        page.wait_for_timeout(250)
        page.click("#mpick-manage")
        page.wait_for_timeout(600)
        check("底部「管理模型」跳到设置页「模型接入」（原手输模型名那条路的正式落点）",
              page.evaluate("() => document.querySelector('.app').classList.contains('is-settings-open')")
              and page.evaluate("""() => { const p = document.querySelector('.setpage[data-set="model"]');
                                          return !!p && p.classList.contains('is-active'); }"""), "")
        page.click("#set-back")
        page.wait_for_timeout(250)

        # ── 17) 思考强度：紧凑形态（解释进 title）──
        print("\n" + "=" * 78)
        print("【17】思考强度：下拉只留档位名（解释收进 title）")
        print("=" * 78)
        page.click("#think-trigger")
        page.wait_for_timeout(250)
        opts = page.eval_on_selector_all(
            "#think-pop .think__opt", "els => els.map(e => ({ t: e.innerText.trim(), title: e.title }))")
        names = [o["t"].replace("✓", "").strip() for o in opts]
        check("下拉只有档位名（低/中/高/最高），不再铺一行解释",
              names == ["低", "中", "高", "最高"], str(names))
        check("解释性文字收进 title（悬停才看，不是删掉不告诉）",
              all(len(o["title"]) >= 8 for o in opts), str(opts))
        check("触发器紧凑：只有档位词 + 小图标（含箭头），没有长说明",
              len((page.inner_text("#think-trigger") or "").strip()) <= 4
              and page.eval_on_selector_all("#think-trigger svg", "els => els.length") >= 2,
              repr(page.inner_text("#think-trigger")))
        check("触发器的 title 带着当前档说明（细看时口径还在）",
              len(page.get_attribute("#think-trigger", "title") or "") >= 10,
              repr(page.get_attribute("#think-trigger", "title")))
        page.click('#think-pop [data-level="low"]')
        page.wait_for_timeout(300)
        depth_saved = [c for c in calls("save_settings")
                       if c[1] and isinstance(c[1][0], dict) and "thinking_depth" in c[1][0]]
        check("语义不变：点「低」仍落库同一档（thinking_depth=2）",
              depth_saved and depth_saved[-1][1][0]["thinking_depth"] == 2, str(depth_saved[-1:]))
        page.click("#think-trigger")                    # 点选后下拉自动收起，重新打开
        page.wait_for_timeout(200)
        page.click('#think-pop [data-level="mid"]')     # 还原成日常档，供后续用例

        # ── 18) 上下文容量浮层（数字全部来自后端）──
        print("\n" + "=" * 78)
        print("【18】上下文容量浮层（进度条 / 分类占比 / 底部预算口径）")
        print("=" * 78)
        # 统计条只在右栏收起时可见（既有布局规则），点开浮层前先收起右栏
        if not page.evaluate("() => document.querySelector('.app').classList.contains('is-right-collapsed')"):
            page.click("#btn-toggle-right")
            page.wait_for_timeout(300)
        page.click("#stat-tokens")
        page.wait_for_timeout(450)
        check("点顶栏「上下文 x/y」打开浮层", page.is_visible("#ctx-pop"), "")
        payload = page.evaluate("async () => await window.__YINDUN_MOCK__.api.context_usage()")
        head = page.inner_text(".ctx__head").replace("\n", " ")
        check("标题行给出「已用 / 上限」与百分比",
              "已用" in head and f'{payload["percent"]}%' in head and "16.4k" in head, head)
        check("进度条宽度 = 后端百分比（前端不自己算）",
              page.eval_on_selector("#ctx-fill", "e => e.style.width") == f'{payload["percent"]}%',
              page.eval_on_selector("#ctx-fill", "e => e.style.width"))
        rows = page.eval_on_selector_all("#ctx-list .ctx__item", """els => els.map(e => ({
            key: e.dataset.key, label: e.querySelector('.ctx__label').textContent,
            pct: e.querySelector('.ctx__pct').textContent }))""")
        want = [{"key": p["key"], "label": p["label"], "pct": f'{p["percent"]}%'} for p in payload["parts"]]
        check("分类占比**逐项**与后端给的一致（用沙盘数据核对）", rows == want, f"{rows} vs {want}")
        foot = page.inner_text("#ctx-foot")
        check("底部两行来自预算表（历史压缩阈值 / 附件正文预算）",
              all(line in foot for line in payload["footer"]["lines"]), repr(foot))
        check("底部不出现我们没有的指标（如「缓存命中率」）",
              "缓存" not in foot, repr(foot))
        check("原来那段长 tooltip 已移除（信息进浮层）",
              "超过" not in (page.get_attribute("#stat-tokens", "title") or ""),
              repr(page.get_attribute("#stat-tokens", "title")))
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        check("浮层可 Esc 关闭", not page.is_visible("#ctx-pop"), "")
        page.click("#stat-tokens")
        page.wait_for_timeout(250)
        # 点浮层外部关闭：用窗口左上角（侧栏品牌区，无任何点击行为）——
        # 浮层盖住了下方内容，点别处会被它自己接住，测试就测不出"点外部关闭"了
        page.mouse.click(8, 8)
        page.wait_for_timeout(250)
        check("浮层可点外部关闭", not page.is_visible("#ctx-pop"), "")

        # ── 19) 窗口按钮：置顶 / 最小化 / 最大化 / 关闭 ──
        print("\n" + "=" * 78)
        print("【19】窗口四按钮（状态以 window_state() 为准）")
        print("=" * 78)
        btns = page.eval_on_selector_all(
            ".win-ctrls .win-btn", "els => els.map(e => [e.dataset.win, e.title])")
        check("正好四个按钮，顺序为 置顶 / 最小化 / 最大化 / 关闭",
              [b[0] for b in btns] == ["toggle_top", "minimize", "toggle_maximize", "close"], str(btns))
        check("每个按钮都有 data-win 与 title", all(b[0] and b[1] for b in btns), str(btns))
        page.click("#btn-max")
        page.wait_for_timeout(400)
        max_calls = [c for c in calls("window_action") if c[1][0] == "toggle_maximize"]
        check("点最大化调用桥 window_action('toggle_maximize')", bool(max_calls), str(max_calls[-1:]))
        check("图标切到「还原」态（body.is-maximized + 还原图标可见 + title 说清）",
              page.evaluate("() => document.body.classList.contains('is-maximized')")
              and page.is_visible(".win-btn__ico-restore")
              and (page.get_attribute("#btn-max", "title") or "") == "还原窗口", "")
        page.click("#btn-max")
        page.wait_for_timeout(400)
        check("再点回到「最大化」态（图标与 title 都回来）",
              not page.evaluate("() => document.body.classList.contains('is-maximized')")
              and (page.get_attribute("#btn-max", "title") or "") == "最大化"
              and page.is_visible(".win-btn__ico-max"), "")
        before_pin = page.evaluate("async () => await window.__YINDUN_MOCK__.api.window_state()")
        page.click("#btn-pin")
        page.wait_for_timeout(400)
        after_pin = page.evaluate("async () => await window.__YINDUN_MOCK__.api.window_state()")
        pin_active = page.evaluate("() => document.getElementById('btn-pin').classList.contains('is-active')")
        check("点置顶后按钮高亮与 window_state().topmost 一致（不是本地翻转记账）",
              after_pin["topmost"] != before_pin["topmost"] and pin_active == after_pin["topmost"],
              f'{before_pin} → {after_pin} active={pin_active}')
        check("外观页的置顶开关同步（窗内外无二口径）",
              page.evaluate("() => document.getElementById('sw-topmost').checked") == after_pin["topmost"], "")
        page.click("#btn-pin")                      # 复原，避免影响后续用例
        page.wait_for_timeout(350)
        # 折叠态：最大化按钮必须禁用，服务端也拒绝（别卡在"半个状态"）
        page.click("#btn-mini")
        page.wait_for_timeout(450)
        max_state = page.evaluate("""() => { const b = document.getElementById('btn-max');
            return { disabled: b.disabled, title: b.title }; }""")
        check("折叠态下最大化按钮禁用且如实说明原因（不静默）",
              max_state["disabled"] is True and "先退出闪发模式" in max_state["title"], str(max_state))
        check("折叠态直接调桥也会被拒绝（返回 false，不是假装成功）",
              page.evaluate("async () => await window.__YINDUN_MOCK__.api.window_action('toggle_maximize')") is False, "")
        page.click("#btn-mini-expand")
        page.wait_for_timeout(400)
        check("展开后最大化按钮恢复可用",
              page.evaluate("() => !document.getElementById('btn-max').disabled"), "")

        # ── 20) 暗色滚动条：不留白色小方块 ──
        print("\n" + "=" * 78)
        print("【20】滚动条 corner / track / button（暗色白块根因）")
        print("=" * 78)
        for theme, label in (("dark", "暗色"), ("light", "浅色")):
            page.evaluate("(t) => { document.documentElement.dataset.theme = t; }", theme)
            page.wait_for_timeout(150)
            styles = page.evaluate("""() => {
                const el = document.querySelector('#chat');
                return {
                  corner: getComputedStyle(el, '::-webkit-scrollbar-corner').backgroundColor,
                  track: getComputedStyle(el, '::-webkit-scrollbar-track').backgroundColor,
                  button: getComputedStyle(el, '::-webkit-scrollbar-button').display,
                  width: getComputedStyle(el, '::-webkit-scrollbar').width,
                }; }""")
            check(f"{label}下滚动条 corner 与 track 都是透明的（白块的根因）",
                  styles["corner"] == "rgba(0, 0, 0, 0)" and styles["track"] == "rgba(0, 0, 0, 0)", str(styles))
            check(f"{label}下滚动条箭头按钮不显示（Windows 原生底色）",
                  styles["button"] == "none", str(styles))
            check(f"{label}下滚动条宽度仍是既有的 10px（没被顺手改掉）",
                  styles["width"] == "10px", str(styles))

        # ── 22) 模型可用性诚实化（K：便携版在同学机器上宣称了一个不存在的模型）──
        # 用另一张页面打开 #mock-noollama：本机没有任何模型、也没有自定义条目，
        # 配置里却写着 qwen3.5:4b —— 正是负责人实测的那台机器。
        print("\n" + "=" * 78)
        print("【22】模型可用性诚实化 + 本机档空态 + 手动添加本机模型")
        print("=" * 78)
        page2 = browser.new_page(viewport={"width": 1440, "height": 900})
        errors2 = []
        page2.on("pageerror", lambda err: errors2.append(str(err)))
        page2.goto(url + "-noollama", wait_until="domcontentloaded")
        page2.wait_for_function(
            "() => { const v = document.querySelector('#model-value');"
            " return v && v.textContent.trim() && v.textContent.trim() !== '—'"
            " && v.textContent.indexOf('检查模型') < 0; }", timeout=20000)
        trigger = (page2.inner_text("#model-value") or "").strip()
        check("★ 配置里的模型不在可用清单里 → 胶囊显示「请选择模型」（不宣称不存在的模型）",
              trigger == "请选择模型" and "qwen3.5:4b" not in trigger, repr(trigger))
        cat = page2.evaluate("async () => await window.__YINDUN_MOCK__.api.model_catalog()")
        check("沙盘契约：current_available=false 且三档全空（这就是同学那台机器的状态）",
              cat["current_available"] is False
              and not any(cat["tiers"][t] for t in ("local", "lan", "wan")), str(cat["current"]))
        page2.click("#model-trigger")
        page2.wait_for_timeout(350)
        hint = page2.inner_text("#model-hint") or ""
        check("可选模型为空时，选择器给的是可行动提示（手动添加 / ollama pull）",
              ("手动添加" in hint) or ("ollama pull" in hint), repr(hint[:120]))
        page2.keyboard.press("Escape")
        page2.click("#btn-settings")
        page2.wait_for_timeout(800)
        note_ui = (page2.inner_text("#local-note") or "").strip()
        check("★ 本机档把后端给的 local_note **原样**显示（说清为什么空）",
              note_ui == str(cat["local_note"]).strip() and "连不上本机 Ollama 服务" in note_ui,
              repr(note_ui[:80]))
        check("空态旁边给了两个动作：重新检测 + 手动添加本机模型",
              page2.is_visible('#prov-detail [data-rescan]')
              and page2.is_visible("#prov-detail [data-addlocal]"), "")
        page2.evaluate("""() => {
          window.__adds = [];
          const m = window.__YINDUN_MOCK__.api, orig = m.add_custom_model;
          m.add_custom_model = (...args) => { window.__adds.push(args); return orig(...args); };
        }""")
        page2.click("#prov-detail [data-addlocal]")
        page2.wait_for_timeout(250)
        page2.fill("#la-model", "qwen3.5:27b")
        page2.click("#prov-detail [data-lasave]")
        page2.wait_for_timeout(900)
        adds = page2.evaluate("() => window.__adds")
        check("★「＋ 手动添加本机模型」按 Ollama 通路落库：tier=local / base_url 以 /v1 结尾 / key=ollama",
              bool(adds) and adds[-1][4] == "local"
              and str(adds[-1][2]).endswith("/v1") and adds[-1][3] == "ollama",
              str(adds[-1] if adds else None))
        check("添加后本机档里出现该模型（不是「加完还是空的」）",
              page2.eval_on_selector_all("#p-models .mrow", "els => els.map(e => e.dataset.mname)")
              == ["qwen3.5:27b"], str(page2.eval_on_selector_all("#p-models .mrow", "els => els.map(e => e.dataset.mname)")))
        page2.click('#p-models .mrow[data-mname="qwen3.5:27b"] .mrow__use')
        page2.wait_for_timeout(600)
        page2.click("#set-back")
        page2.wait_for_timeout(300)
        trigger2 = (page2.inner_text("#model-value") or "").strip()
        check("添加后点一下即切换，胶囊显示「供应商 / 模型」",
              trigger2.endswith("/ qwen3.5:27b") and "手动" in trigger2, repr(trigger2))
        check("K 场景全程无 JS 运行时错误", not errors2, "；".join(errors2[:3]))
        page2.close()

        check("全程无 JS 运行时错误", not errors, "；".join(errors[:3]))
        browser.close()

    httpd.shutdown()

    # ── 21) 服务层（浏览器沙盘之外的真实路径）：密钥查看写审计 / 上下文口径 ──
    # 这两条没法在沙盘里验：审计写在内核（HMAC 链），沙盘的假桥不产生审计。
    # 所以直接用真实 AgentService + 临时配置跑一次——与 tests/test_agent_service.py 同一做法
    # （临时目录里的配置，不碰用户的 global_config.json）。
    print("\n" + "=" * 78)
    print("【21】服务层：custom_model_key 写审计 / context_usage 与预算表一致")
    print("=" * 78)
    import tempfile                                                     # noqa: E402
    from yindun.app.agent_service import AgentService                   # noqa: E402
    from yindun.app.session_store import SessionStore                   # noqa: E402
    from yindun.app.settings_store import SettingsStore                 # noqa: E402
    from yindun.core.audit_log import AuditLog                          # noqa: E402

    tmp = Path(tempfile.mkdtemp(prefix="yd_ui_key_"))
    fake_key = "sk-" + "ui-regression-not-a-real-key"       # 分段拼：不把"像密钥的字面量"写进仓库
    store = SettingsStore(path=tmp / "cfg.json")
    store.load()
    store.set("custom_models", {"回归供应商": {
        "model_id": "demo-model", "base_url": "https://api.example.invalid/v1",
        "api_key": fake_key, "tier": "wan"}})
    store.save()
    svc = AgentService(settings=store, sessions=SessionStore(path=tmp / "sessions.json"))
    audit_before = AuditLog().get_entries() or []
    key_res = svc.custom_model_key("回归供应商")
    fresh = (AuditLog().get_entries() or [])[len(audit_before):]
    check("custom_model_key 返回本机已存的明文密钥（仅本机窗口通路）",
          bool(key_res.get("ok")) and key_res.get("key") == fake_key, str(key_res)[:40])
    viewed = [e for e in fresh if "查看" in str(getattr(e, "message", ""))]
    check("每次查看都写审计（access_control / warning）",
          bool(viewed) and str(getattr(viewed[-1], "event_type", "")) == "access_control"
          and str(getattr(viewed[-1], "severity", "")) == "warning",
          str(viewed[-1:])[:200])
    check("审计只记「看了哪个供应商」，不写密钥本身",
          all(fake_key not in json.dumps(getattr(e, "details", {}) or {}, ensure_ascii=False)
              and fake_key not in str(getattr(e, "message", "")) for e in fresh), "")
    usage = svc.context_usage()
    from yindun.core.context_budget import (ATTACHMENT_TOTAL_CHARS,         # noqa: E402
                                            MODEL_WINDOW_TOKENS)
    check("context_usage 的窗口上限与预算表一致",
          usage.get("limit_tokens") == MODEL_WINDOW_TOKENS, str(usage.get("limit_tokens")))
    check("context_usage 的底部两行来自预算表（附件预算写的就是内核那个数）",
          any(str(ATTACHMENT_TOTAL_CHARS) in line for line in usage["footer"]["lines"]),
          str(usage["footer"]))
    check("context_usage 的每一项都带真实 token 与占比（没有占位/编造）",
          all(isinstance(p.get("tokens"), int) and isinstance(p.get("percent"), (int, float))
              and p.get("label") for p in usage["parts"]), str(usage["parts"]))

    elapsed = time.time() - started
    print("\n" + "=" * 78)
    print(f"耗时 {elapsed:.1f} 秒（上限 {LIMIT_SECONDS} 秒）")
    check("在时限内跑完（无 Ollama 依赖）", elapsed < LIMIT_SECONDS, f"{elapsed:.1f}s")
    if failures:
        print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
        return 1
    print("✅ 界面关键路径全部通过（权限 / 胶囊 / 思考紧凑形态 / 插件 / 模型接入重做 / 模型选择器两级 / "
          "上下文浮层 / 窗口四按钮 / 可读写范围 / 模型可用性诚实化 / 闪发 / 主题与布局 / 附件＋ / "
          "Markdown 表格 / 等待态与空回复兜底 / 工作流界面已移除 / 暗色滚动条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
