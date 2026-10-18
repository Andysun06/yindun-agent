# -*- coding: utf-8 -*-
"""隐盾 · 界面自动化回归（V3.6.1）

为什么要有它（开发记忆 §7 B-4，"当前最大的工程债"）：界面改动一直靠"源码契约 grep +
手工点浏览器"验证——改一次要点十几轮，而且逻辑回归（渲染后的真实 DOM 行为）根本测不到。
本套件用 Playwright 驱动真实 Chromium 打开 `index.html#mock`（界面自带的后端沙盘），
把十条关键交互钉死；不需要 Ollama、不需要起后端，两分钟内跑完。

覆盖（每条都是"用户真会做的一步"）：
  1. 四档权限：分段器选中态 / 提示文案 / 矩阵渲染与内核 `MODE_POLICY` **逐格一致** / 落库调用
  2. 每对话权限胶囊：弹出、选档、胶囊文案与 `set_session_permission` 调用
  3. 思考强度四档：下拉四项、选中后顶栏文案与 `save_settings` 调用
  4. 插件市场：搜索过滤、启停开关（含确认语义按来源区分）→ 调用 `plugin_set_enabled`
  5. 插件专属设置栏目：启用带设置的插件后栏目出现、停用后消失
  6. 插件专属设置页渲染：laya 三级接入表单（本机/局域网/正则）+ 保存调用
  7. 设置页六组页面渲染（模型/隐私与权限/知识库/插件/企业控制面/窗口与外观）
  8. 工作流面板：模板列表、启动实例、步骤渲染、"已审批，待执行"文案、实现口径提示
  9. 闪发模式：输入与主输入同步、Enter 发送、Shift+Enter 换行（不发送）
 10. 明暗切换 / 右栏折叠 → 窗口按钮归位（topbar ↔ panel ↔ head 三态）

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
LIMIT_SECONDS = 150


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

        # ── 8) 工作流面板 ──
        print("\n" + "=" * 78)
        print("【8】工作流面板")
        print("=" * 78)
        page.click("#set-back")
        page.wait_for_timeout(200)
        page.click("#btn-workflow")
        page.wait_for_selector("#wf-templates .wf-template")
        tpl_names = page.eval_on_selector_all("#wf-templates .wf-template__name",
                                             "els => els.map(e => e.textContent.trim())")
        check("模板列表渲染", len(tpl_names) >= 3, str(tpl_names))
        page.click("#wf-templates .wf-template")
        page.wait_for_timeout(300)
        check("点模板启动实例（调用 workflow_start）", bool(calls("workflow_start")))
        steps = page.eval_on_selector_all("#wf-steps .wf-step__name", "els => els.map(e => e.textContent.trim())")
        check("步骤列表渲染", len(steps) >= 3, str(steps))
        notice = page.inner_text("#wf-notice")
        check("提示写明实现口径（不再出现「演示步骤」字样）",
              "演示步骤" not in notice and ("真实执行器" in notice or "未实现" in notice), notice[:120])
        status_text = page.inner_text("#wf-steps")
        check("「已审批，待执行」状态文案生效", "已审批" in status_text, status_text[:200])
        check("未实现的步骤被标注（外部模板的工具有没有实现，界面要说清）",
              "未实现" in status_text, status_text[:200])

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

        check("全程无 JS 运行时错误", not errors, "；".join(errors[:3]))
        browser.close()

    httpd.shutdown()
    elapsed = time.time() - started
    print("\n" + "=" * 78)
    print(f"耗时 {elapsed:.1f} 秒（上限 {LIMIT_SECONDS} 秒）")
    check("在时限内跑完（无 Ollama 依赖）", elapsed < LIMIT_SECONDS, f"{elapsed:.1f}s")
    if failures:
        print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
        return 1
    print("✅ 界面十条关键路径全部通过（四档权限 / 胶囊 / 思考 / 插件市场与设置页 / 工作流 / 闪发 / 主题与布局）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
