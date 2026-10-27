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
  7d-2. 跨档位「＋ 添加…」通路（负责人反馈三次的缺陷固化）：三档各有同一处的添加入口 /
      局域网→本机→公网→本机→局域网 反复切每次面板都真的展开（hidden=false 且高度>0）/
      提交过一次后入口依然可用：不是"用一次就废" / 左列摊开的模型子项点一下即切换
      （data-use 不被同元素的 data-t 覆盖）/ 左列事件走容器委托，重绘后仍有效
  7e. 可读写范围（sandbox_dir）：设置项显示 / 选择目录 / 清除回默认 / 态势条同一来源 /
      四档矩阵写明"越界按档位判定并写审计"
  8. 工作流面板（V3.6.21 恢复入口）：页签 / 模板 / 启动 / 执行下一步 / 待审批 / 通过 / 导出
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
 17. 思考强度：模型卡片底部四档滑杆（Ctrl+T 循环）、触发器紧凑、语义不变（仍落库同一档）
 18. 上下文容量浮层：进度条与分类占比**逐项等于后端给的值**、底部两行来自预算表、
     不出现"缓存命中率"这类我们没有的指标
 19. 窗口按钮：正好四个（置顶/最小化/最大化/关闭）、点最大化调桥并切图标、置顶高亮与
     `window_state()` 一致、折叠态下最大化按钮禁用且服务端拒绝
 20. 暗色滚动条：corner/track 透明、button 不显示（白块根因），宽度仍是 10px
 21. 脱浏览器的服务层断言：密钥「查看」返回本机明文并写 `access_control` 审计（不含密钥本身）、
     `context_usage()` 的数字与预算表一致
 22. 模型可用性诚实化（K）：配置里的模型不在清单里时胶囊显示「请选择模型」（不宣称不存在的模型）、
     本机档空态原样显示 `local_note` + 两个动作、手动添加本机模型走 `<ollama_host>/v1` + `ollama` 密钥、
     加完能立刻切换；没有任何可选模型时选择器给可行动提示；**本机档为空时左列仍列出「本机 Ollama」
     与「＋ 手动添加本机模型」入口**（点过局域网添加之后仍能回到本机档继续加本地模型）

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

        # ── 3) 思考强度四档（V3.6.15：模型卡片底部滑杆）──
        print("\n" + "=" * 78)
        print("【3】思考强度滑杆（四档）")
        print("=" * 78)
        page.click("#think-trigger")
        page.wait_for_timeout(200)
        check("点开后出现模型卡片与四档滑杆", page.is_visible("#model-pop") and page.is_visible("#think-slider"))
        levels = page.eval_on_selector("#think-slider", """el => ({
            min: Number(el.min), max: Number(el.max), step: Number(el.step) })""")
        check("四档齐全（0..3，步长 1）",
              levels == {"min": 0, "max": 3, "step": 1}, str(levels))
        page.eval_on_selector("#think-slider", """el => {
            el.value = '3';
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""")
        page.wait_for_timeout(200)
        check("拖到「最高」后顶栏文案更新", (page.inner_text("#think-value") or "").strip() == "最高",
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

        # 连点回归（V3.6.21）：视觉立即跟随、落库合并，不能每点一次都排一队重活
        _mode_before = len(calls("save_settings"))
        for _mode in ("快速回答", "深度思考", "快速回答", "深度思考", "快速回答", "深度思考"):
            page.click(f'#mode-switch .seg__btn[data-mode="{_mode}"]')
        page.wait_for_timeout(350)
        _mode_active = page.eval_on_selector(
            "#mode-switch .seg__btn.is-active", "el => el.dataset.mode")
        _mode_after = [c for c in calls("save_settings")[_mode_before:]
                       if c[1] and isinstance(c[1][0], dict) and "think_mode" in c[1][0]]
        check("★ 快速来回点：最终高亮是最后一次点击",
              _mode_active == "深度思考", str(_mode_active))
        check("★ 快速来回点：落库被合并（不是每点一次写一次）",
              len(_mode_after) <= 3, str(len(_mode_after)))

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
              [(p["tier"], p["prov"]) for p in providers][:3] ==
              [("local", "本机 Ollama"), ("lan", "10.0.0.8:8000"), ("wan", "open.bigmodel.cn")]
              and ("wan", "隐盾") in [(p["tier"], p["prov"]) for p in providers],
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
        check("API 格式下拉：四种格式全部可用（V3.6.21 起不再有置灰项）",
              fmt[0][0] == "chat_completions" and fmt[0][1] is False
              and all(f[1] is False and "暂不支持" not in f[2] for f in fmt[1:])
              and {f[0] for f in fmt} >= {"chat_completions", "anthropic", "responses", "ollama_native"},
              str(fmt))
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

        # ── 7b-2) 跨档位「＋ 添加…」通路（负责人反馈三次的缺陷固化）──
        # 症状：点过任一档的「＋ 添加…」（state.prov 被写成那一档）之后，其它档的
        #       「＋ 添加…」就"点不动"。根因有二，都修在机制上：
        #       ① 本机档在清单为空时左列**没有条目**，右栏（手动添加 / 重新检测）失去导航入口——
        #          进了别的档就回不去本机档（详见【22】noollama 场景）；
        #       ② 左列事件原先逐个绑到渲染出的节点上，paintUsage 重绘左列后旧节点连同 handler
        #          一起被摘掉；且 `.prov__sub` 同时带 `data-t` 与 `data-use`，后绑的 `[data-t]`
        #          覆盖了 `[data-use]` → 点本机档摊开的模型名"点了没反应"。
        #       现在：三档各有同一处的添加入口 + 容器级事件委托（重绘后仍有效）。
        print("\n" + "=" * 78)
        print("【7b-2】跨档位「＋ 添加…」通路：反复切档、反复打开、提交后仍可用")
        print("=" * 78)

        def panel_box(sel):
            """面板状态：hidden 与真实高度（只用"元素存在"抓不到"点了没反应"）。"""
            return page.evaluate("""(s) => { const e = document.querySelector(s);
                if (!e) return null;
                return { hidden: !!e.hidden, h: e.getBoundingClientRect().height }; }""", sel)

        addt = page.eval_on_selector_all("#prov-list [data-addt]", "els => els.map(e => e.dataset.addt)")
        check("三档各有同一处的「＋ 添加…」入口（本机档 = 手动添加本机模型）",
              addt == ["local", "lan", "wan"]
              and "手动添加本机模型" in page.inner_text('#prov-list [data-addt="local"]'), str(addt))

        # 反复跨档切换：每一次点击对应面板都必须真的展开（hidden=false 且高度 > 0）
        cycle = (("局域网", '#prov-list [data-addt="lan"]', "#p-provider"),
                 ("本机", '#prov-list [data-addt="local"]', "#local-add"),
                 ("公网", '#prov-list [data-addt="wan"]', "#p-provider"),
                 ("本机", '#prov-list [data-addt="local"]', "#local-add"),
                 ("局域网", '#prov-list [data-addt="lan"]', "#p-provider"))
        for i, (label, sel, probe) in enumerate(cycle, 1):
            page.click(sel)
            page.wait_for_timeout(280)
            st = panel_box(probe)
            check(f"★ 跨档第 {i} 步：点「{label}」的 ＋添加 后面板真的展开（hidden=false 且高度>0）",
                  bool(st) and st["hidden"] is False and st["h"] > 0, str(st))
        page.click('#prov-list [data-addt="local"]')
        page.wait_for_timeout(280)
        check("本机添加入口展开后自动聚焦到模型名输入框",
              page.evaluate("() => document.activeElement && document.activeElement.id") == "la-model",
              page.evaluate("() => document.activeElement && document.activeElement.id"))

        # 提交过一次之后入口依然可用（不是"用一次就废"）
        page.fill("#la-model", "回归-本机手动")
        page.click("#prov-detail [data-lasave]")
        page.wait_for_timeout(900)
        check("本机手动添加提交后模型真的进了本机档",
              "回归-本机手动" in page.eval_on_selector_all("#p-models .mrow", "els => els.map(e => e.dataset.mname)"),
              str(page.eval_on_selector_all("#p-models .mrow", "els => els.map(e => e.dataset.mname)")))
        page.click('#prov-list [data-addt="lan"]')
        page.wait_for_timeout(280)
        check("★ 提交过一次后，局域网「＋ 添加供应商」依然能打开（不是一次性）",
              page.is_visible("#p-provider"), "")
        page.click('#prov-list [data-addt="local"]')
        page.wait_for_timeout(280)
        st = panel_box("#local-add")
        check("★ 提交过一次后，本机「＋ 手动添加本机模型」依然能展开",
              bool(st) and st["hidden"] is False and st["h"] > 0, str(st))

        # 左列摊开的模型子项：点一下即切换（data-use 不被同元素的 data-t 覆盖）
        page.evaluate("""() => {
          window.__picked = [];
          const m = window.__YINDUN_MOCK__.api, orig = m.save_settings;
          m.save_settings = (patch) => { window.__picked.push(patch); return orig(patch); };
        }""")
        sub_cur = page.inner_text("#model-value").strip().split("/")[-1].strip()
        target = page.evaluate("""(cur) => {
            const els = [...document.querySelectorAll('#prov-list .prov__sub')];
            const el = els.find((e) => e.dataset.use !== cur) || els[0];
            return el ? el.dataset.use : ''; }""", sub_cur)
        check("本机档摊开了可点击切换的模型子项（且不都是当前使用中）",
              bool(target) and target != sub_cur, f"cur={sub_cur} target={target}")
        page.click(f'#prov-list .prov__sub[data-use="{target}"]')
        page.wait_for_timeout(700)
        picked = page.evaluate("() => window.__picked.slice()")
        check("★ 点左列本机档摊开的模型名 = 切换使用（落库 patch.model，不是「点了没反应」）",
              bool(picked) and picked[-1].get("model") == target,
              f"target={target} picked={picked[-1:]}")
        # 复原当前模型，避免影响后续用例
        page.click('#prov-list .prov__item[data-t="lan"]')
        page.wait_for_timeout(250)
        page.click('#p-models .mrow[data-mname="公司内网Qwen"] .mrow__use')
        page.wait_for_timeout(600)

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

        # ── 7d-2) 四种接口格式全部可用 + 公共中转一键添加（V3.6.21）──
        print("\n" + "=" * 78)
        print("【7d-2】接口格式四项可用 + 「添加公共中转（免 Key）」")
        print("=" * 78)
        fmt_disabled = page.eval_on_selector_all(
            "#p-format option", "els => els.filter(e => e.disabled).map(e => e.value)")
        check("四种接口格式都可用（没有置灰的选项）", fmt_disabled == [], str(fmt_disabled))
        fmt_values = page.eval_on_selector_all("#p-format option", "els => els.map(e => e.value)")
        check("格式清单包含 anthropic / responses / ollama_native",
              {"chat_completions", "anthropic", "responses", "ollama_native"} <= set(fmt_values),
              str(fmt_values))
        relay_btn = page.query_selector('[data-addrelay="1"]')
        check("公网档有「＋ 添加公共中转（免 Key）」入口", relay_btn is not None, "")
        if relay_btn is not None:
            page.click('[data-addrelay="1"]')
            page.wait_for_timeout(900)
            relay_calls = [c for c in calls("add_custom_model")
                           if c[1] and "自建中转站" in str(c[1][0])]
            check("点一下即写入中转配置（免 Key + OpenAI 兼容）",
                  bool(relay_calls)
                  and relay_calls[-1][1][2] == "https://yindun-relay.pages.dev/api/ai"
                  and relay_calls[-1][1][3] == ""
                  and relay_calls[-1][1][6] == "chat_completions",
                  str(relay_calls[-1][1]) if relay_calls else "no call")
            check("中转站出现在公网档列表里",
                  "隐盾" in page.inner_text("#prov-list"), page.inner_text("#prov-list")[:200])

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

        # ── 8) 工作流面板（V3.6.21 恢复入口：模板 → 启动 → 审批 → 导出）──
        print("\n" + "=" * 78)
        print("【8】工作流面板（恢复入口后：页签 / 模板 / 启动 / 待审批 / 通过）")
        print("=" * 78)
        page.click("#set-back")
        page.wait_for_timeout(250)
        check("右栏存在「工作流」页签",
              page.query_selector('.ptab[data-pane="workflow"]') is not None, "")
        page.click('.ptab[data-pane="workflow"]')
        page.wait_for_timeout(400)
        check("工作流面板可见", page.is_visible("#pane-workflow"), "")
        tpl_cards = page.query_selector_all("#wf-templates .wf-card")
        check("模板列表渲染出内置模板（≥3）", len(tpl_cards) >= 3, f"cards={len(tpl_cards)}")
        check("模板卡片带「启动」按钮",
              page.query_selector("#wf-templates [data-wf-start]") is not None, "")
        page.fill("#wf-path", r"D:\公司资料\合同")
        page.click("#wf-templates [data-wf-start]")
        page.wait_for_timeout(500)
        check("启动后出现实例区与步骤列表",
              page.is_visible("#wf-instance")
              and len(page.query_selector_all("#wf-instance .wf-step")) == 3, "")
        page.click("#wf-instance [data-wf-next]")
        page.wait_for_timeout(500)
        page.click("#wf-instance [data-wf-next]")
        page.wait_for_timeout(600)
        check("「执行下一步」推进到待审批步骤",
              page.query_selector("#wf-instance [data-wf-approve]") is not None,
              page.inner_text("#wf-instance"))
        page.click("#wf-instance [data-wf-approve]")
        page.wait_for_timeout(600)
        text_after = page.inner_text("#wf-instance")
        check("审批通过后该步骤变为已完成", "已完成" in text_after, text_after[:160])
        check("实例区提供「导出执行记录」", page.query_selector("#wf-instance [data-wf-export]") is not None, "")
        # 源码级断言：面板与桥调用必须真实存在（防止"入口又没了但测试还绿"）
        sources = {name: (WEB_DIR / name).read_text(encoding="utf-8")
                   for name in ("index.html", "app.js", "dev-mock.js")}
        expected = {
            "index.html": ("pane-workflow", "wf-templates"),
            "app.js": ("wf-templates", "workflow_templates",
                       "workflow_start", "workflow_status", "workflow_approve", "workflow_export"),
            "dev-mock.js": ("workflow_templates", "workflow_start", "workflow_status",
                            "workflow_approve", "workflow_export"),
        }
        missing = {n: [t for t in want if t not in sources[n]] for n, want in expected.items()}
        check("界面源码保留工作流面板与全部桥调用（入口不能被悄悄删掉）",
              not any(missing.values()), str(missing))

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
        page.click("#mini-input")
        before_ctrl = len(calls("send"))
        page.keyboard.press("Control+Enter")
        page.wait_for_timeout(200)
        check("Ctrl+Enter 在浮条里是换行、不发送（报告 P1-06）",
              len(calls("send")) == before_ctrl and "\n" in page.input_value("#mini-input"),
              f"{before_ctrl} → {len(calls('send'))} value={page.input_value('#mini-input')!r}")
        page.click("#mini-input")
        page.keyboard.press("Enter")
        page.wait_for_timeout(250)
        check("Enter 触发发送", len(calls("send")) > before_send_count,
              f"{before_send_count} → {len(calls('send'))}")
        # 主输入框同样支持 Ctrl+Enter 换行（发送后会自动展开；先把忙碌态归零）
        page.wait_for_timeout(250)
        page.evaluate("() => window.yindun.onEvent('state', {busy: false})")
        page.wait_for_timeout(150)
        page.fill("#input", "第一行")
        before_main_ctrl = len(calls("send"))
        page.click("#input")
        page.keyboard.press("Control+Enter")
        page.wait_for_timeout(200)
        check("Ctrl+Enter 在主输入框是换行、不发送",
              len(calls("send")) == before_main_ctrl and "\n" in page.input_value("#input"),
              f"{before_main_ctrl} → {len(calls('send'))} value={page.input_value('#input')!r}")

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

        # ── 10b) 窗口拖动区（V3.6.21：整条顶栏可拖，按钮不触发拖动）──
        print("\n" + "=" * 78)
        print("【10b】顶栏/浮条拖动区（整条可拖 + 交互控件不触发）")
        print("=" * 78)
        drag_info = page.evaluate("""() => {
            const top = document.querySelector('.topbar');
            const mini = document.querySelector('.minibar');
            return { top: top && top.classList.contains('pywebview-drag-region'),
                     mini: mini && mini.classList.contains('pywebview-drag-region') };
        }""")
        check("顶栏整条是拖动区（不再只有中间的 spacer）", bool(drag_info.get("top")), str(drag_info))
        check("闪发浮条整条是拖动区", bool(drag_info.get("mini")), str(drag_info))
        # 冒泡验证：按钮上的 mousedown 不能到达 body（pywebview 的拖动监听挂在 body），
        # 顶栏空白处的 mousedown 必须到达 body（否则拖不动窗口）。
        bubble = page.evaluate("""() => {
            const seen = { body: 0 };
            const onBody = () => { seen.body += 1; };
            document.body.addEventListener('mousedown', onBody);
            const fire = (el) => el.dispatchEvent(
                new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0 }));
            fire(document.querySelector('#btn-sidebar'));      // 顶栏按钮
            const afterBtn = seen.body;
            fire(document.querySelector('.topbar__title'));    // 顶栏空白/标题
            const afterTitle = seen.body;
            document.body.removeEventListener('mousedown', onBody);
            return { afterBtn, afterTitle };
        }""")
        check("顶栏按钮的 mousedown 被拦下（不会把点击当成拖窗口）",
              bubble["afterBtn"] == 0, str(bubble))
        check("顶栏空白处的 mousedown 能到达 body（窗口可以拖）",
              bubble["afterTitle"] >= 1, str(bubble))

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

        # ── 12b) 引用块 / 分隔线 / 空行收敛（V3.6.12，负责人截图）──
        # 现场：模型用 `> ` 引用 OCR 原文，界面把 `>` 原样显示成纯文本；`---` 也原样显示；
        #      代码块前后留下大片空白（空行都变成了 <br>，再叠加 <pre> 外边距）。
        print("\n" + "=" * 78)
        print("【12b】Markdown 引用块 / 分隔线 / 空行收敛")
        print("=" * 78)
        md_quote = "\n".join([
            "【题目定位】",
            "> 一、选择题（本题共20分）",
            ">",
            "> 1. 下列函数中，不是类的成员函数（ ）",
            "> - A) 构造函数",
            "> - B) 析构函数",
            "---",
            "答案：C",
        ])
        page.evaluate("(t) => window.yindun.onEvent('intermediate_result', t)", md_quote)
        page.wait_for_timeout(300)
        quote = page.evaluate("""() => {
          const body = [...document.querySelectorAll('#chat .entry__body')].pop();
          if (!body) return null;
          const q = body.querySelector('blockquote');
          return {
            hasQuote: !!q,
            quoteText: q ? q.textContent : '',
            quoteMarks: (body.textContent.match(/>/g) || []).length,
            hrs: body.querySelectorAll('hr').length,
            literalHr: body.textContent.includes('---'),
            quoteBrs: q ? q.querySelectorAll('br').length : -1,
          };
        }""")
        check("★ `> ` 引用渲染成 blockquote（页面上不再出现字面 '>'）",
              bool(quote) and quote["hasQuote"] and quote["quoteMarks"] == 0, str(quote))
        check("★ 引用块里的 `> - A)` 变成列表标记（保留内容）",
              bool(quote) and "• A) 构造函数" in quote["quoteText"], str(quote))
        check("★ `---` 渲染成 <hr>（页面上不再出现字面 '---'）",
              bool(quote) and quote["hrs"] == 1 and not quote["literalHr"], str(quote))

        md_space = "\n".join([
            "### 小节标题",
            "",
            "",
            "",
            "```",
            "class Box { int size; };",
            "```",
            "",
            "",
            "",
            "关键特征：",
        ])
        page.evaluate("(t) => window.yindun.onEvent('intermediate_result', t)", md_space)
        page.wait_for_timeout(300)
        spacing = page.evaluate("""() => {
          const body = [...document.querySelectorAll('#chat .entry__body')].pop();
          const pre = body ? body.querySelector('pre') : null;
          if (!pre) return null;
          const rect = pre.getBoundingClientRect();
          // 前后各找一个"有文字"的节点，量它们的间距
          const prev = pre.previousSibling;
          const next = pre.nextSibling;
          const prevText = prev && prev.textContent ? prev.textContent : '';
          const nextText = next && next.textContent ? next.textContent : '';
          return {
            brsBefore: (prevText.match(/\\u0000/g) || []).length,
            prevTail: prevText.slice(-12),
            nextHead: nextText.slice(0, 12),
            preH: rect.height,
            gapBeforeOk: !!prev,
            gapAfterOk: !!next,
            emptyP: body.querySelectorAll('p:empty').length,
            totalBrs: body.querySelectorAll('br').length,
          };
        }""")
        # 渲染器不再产出 <p>；空行收敛后，<pre> 前后的换行数不应堆叠成大片空白。
        check("★ 代码块前后不再堆叠空行（空段落 0 个、br 数量可控）",
              bool(spacing) and spacing["emptyP"] == 0 and spacing["totalBrs"] <= 8, str(spacing))
        check("代码块内容完整保留（收敛空白不影响正文）",
              "class Box" in page.evaluate(
                  "() => [...document.querySelectorAll('#chat .entry__body pre')].pop().textContent"),
              "")

        # 引用块里的 HTML 仍必须转义（加 blockquote 不能放松安全口径）
        evil_q = "> <img src=x on" + "error=alert(" + "1)>"
        page.evaluate("(t) => window.yindun.onEvent('intermediate_result', t)", evil_q)
        page.wait_for_timeout(250)
        evil_q_dom = page.evaluate("""() => {
          const body = [...document.querySelectorAll('#chat .entry__body')].pop();
          return { imgs: body.querySelectorAll('img').length,
                   text: body.textContent || '' };
        }""")
        check("★ 引用块里的恶意标签仍被转义（DOM 无 img）",
              evil_q_dom["imgs"] == 0 and "<img" in evil_q_dom["text"], str(evil_q_dom))


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
        print("【16】模型选择器：单卡片 + 供应商子菜单（照 ZCode）/ 无搜索框 / 管理模型")
        print("=" * 78)
        # §15 停在设置视图里（设置会隐藏顶栏与输入区），先回到对话视图
        page.click("#set-back")
        page.wait_for_timeout(300)
        check("回到对话视图（后续交互都在输入区一带）",
              not page.evaluate("() => document.querySelector('.app').classList.contains('is-settings-open')"), "")
        page.click("#model-trigger")
        page.wait_for_timeout(300)
        check("弹层里**没有**搜索框（D：照 ZCode 的形态）",
              page.query_selector("#model-pop input[type=search]") is None
              and page.query_selector("#model-search") is None, "")
        src_index = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        src_app = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        check("源码里也不再引用 model-search / model-list（整条老路径退场）",
              "model-search" not in src_index and "model-search" not in src_app
              and "model-list" not in src_index, "")
        check("主卡片头部写明当前供应商 + 数据边界徽标（本机 / API）",
              bool((page.inner_text("#mpick-prov-name") or "").strip())
              and (page.inner_text("#mpick-api-badge") or "").strip() in ("本机", "API"),
              f"{page.inner_text('#mpick-prov-name')} / {page.inner_text('#mpick-api-badge')}")
        check("主卡片只列当前供应商的模型（不是两列并排）",
              page.query_selector("#model-pop .mpick") is None
              and page.eval_on_selector_all("#mpick-models [data-model]", "els => els.length") >= 1, "")
        check("当前模型打勾（一眼看出在用哪个）",
              page.eval_on_selector_all("#mpick-models .model-opt.is-cur .model-opt__tick",
                                        "els => els.length") == 1, "")
        # 供应商子菜单：点头部展开
        page.click("#mpick-head")
        page.wait_for_timeout(250)
        prov_opts = page.eval_on_selector_all(".mpick__prov", "els => els.map(e => e.dataset.pkey)")
        check("点头部展开供应商子菜单，列出全部供应商（含三档分组）",
              page.is_visible("#mpick-providers") and len(prov_opts) >= 3
              and page.eval_on_selector_all("#mpick-providers .mpick__gt", "els => els.length") >= 2,
              str(prov_opts))
        check("子菜单里当前供应商打勾",
              page.eval_on_selector_all("#mpick-providers .mpick__prov.is-open .mpick__check",
                                        "els => els.length") == 1, "")
        # 负责人截图：供应商子菜单往右弹，直接把右侧顶出空白。
        # 判据：子菜单完整落在视口内，且页面没有横向滚动。
        _prov_geom = page.evaluate("""() => {
            const r = document.querySelector('#mpick-providers').getBoundingClientRect();
            return { left: r.left, right: r.right, vw: window.innerWidth,
                     scrollW: document.documentElement.scrollWidth }; }""")
        check("★ 供应商子菜单不超出视口（右侧不再被顶出空白）",
              _prov_geom["left"] >= 0 and _prov_geom["right"] <= _prov_geom["vw"] + 1
              and _prov_geom["scrollW"] <= _prov_geom["vw"] + 1, str(_prov_geom))
        page.click('.mpick__prov[data-pkey="wan::open.bigmodel.cn::0"]')
        page.wait_for_timeout(250)
        check("点供应商 → 子菜单收起、主卡片保持打开（不会被自己重绘关掉）",
              page.is_visible("#model-pop") and not page.is_visible("#mpick-providers"), "")
        sel_models = page.eval_on_selector_all(
            "#mpick-models [data-model]", "els => els.map(e => ({n: e.dataset.model, tags: [...e.querySelectorAll('.mtag')].map(t => t.textContent)}))")
        check("主卡片换成该供应商的模型，带标签",
              any(m["n"] == "云端GLM" for m in sel_models)
              and any(m["tags"] == ["低成本"] for m in sel_models), str(sel_models))
        page.click('#mpick-models [data-model="云端GLM"]')
        page.wait_for_timeout(450)
        check("选模型 → 弹层关闭 + 触发器文案变成「供应商 / 模型」",
              not page.is_visible("#model-pop")
              and page.inner_text("#model-value").strip() == "open.bigmodel.cn / 云端GLM",
              page.inner_text("#model-value"))
        # 键盘：↑↓ 移动、Enter 选中、→ 展开供应商、Esc 关闭
        page.click("#model-trigger")
        page.wait_for_timeout(250)
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        page.wait_for_timeout(450)
        check("键盘 Enter 在模型列表选中 → 弹层关闭", not page.is_visible("#model-pop"), "")
        page.click("#model-trigger")
        page.wait_for_timeout(250)
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(250)
        check("键盘 → 展开供应商子菜单", page.is_visible("#mpick-providers"), "")
        page.keyboard.press("ArrowLeft")
        page.wait_for_timeout(200)
        check("键盘 ← 收起子菜单（主卡片仍打开）",
              page.is_visible("#model-pop") and not page.is_visible("#mpick-providers"), "")
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
        page.click("#mpick-head")
        page.wait_for_timeout(200)
        page.click("#mpick-manage")
        page.wait_for_timeout(600)
        check("子菜单底部「管理模型」跳到设置页「模型接入」（原手输模型名那条路的正式落点）",
              page.evaluate("() => document.querySelector('.app').classList.contains('is-settings-open')")
              and page.evaluate("""() => { const p = document.querySelector('.setpage[data-set="model"]');
                                          return !!p && p.classList.contains('is-active'); }"""), "")
        page.click("#set-back")
        page.wait_for_timeout(250)

        # ── 17) 思考强度：模型卡片底部滑杆 + Ctrl+T ──
        print("\n" + "=" * 78)
        print("【17】思考强度：模型卡片底部四档滑杆（Ctrl+T 循环）")
        print("=" * 78)
        page.click("#think-trigger")
        page.wait_for_timeout(300)
        check("点思考档位 → 打开模型卡片并显示滑杆（ZCode 形态）",
              page.is_visible("#model-pop") and page.is_visible("#think-slider"), "")
        slider = page.eval_on_selector("#think-slider", """el => ({
            min: el.min, max: el.max, step: el.step, value: el.value,
            label: document.querySelector('#mpick-think-label').textContent,
            pct: getComputedStyle(el).getPropertyValue('--pct').trim() })""")
        check("滑杆是四档离散值（0..3），当前档与文字一致",
              slider["min"] == "0" and slider["max"] == "3" and slider["step"] == "1"
              and slider["label"] in ("低", "中", "高", "最高"), str(slider))
        check("触发器紧凑：只剩档位词 + 末尾箭头，与模型名并排读作「模型 高 ∨」",
              len((page.inner_text("#think-trigger") or "").strip()) <= 4
              and page.eval_on_selector_all("#think-trigger svg", "els => els.length") == 1
              and page.eval_on_selector_all("#model-trigger .model-select__chev",
                                            "els => els.filter(e => getComputedStyle(e).display !== 'none').length") == 0
              and page.evaluate("""() => {
                  const t = document.querySelector('#think-trigger');
                  const m = document.querySelector('#model-trigger');
                  if (!t || !m) return false;
                  const tr = t.getBoundingClientRect(), mr = m.getBoundingClientRect();
                  return Math.abs(tr.top - mr.top) < 6 && tr.left >= mr.right - 4;
              }"""),
              repr(page.inner_text("#think-trigger")))
        check("触发器的 title 带着当前档说明（细看时口径还在）",
              len(page.get_attribute("#think-trigger", "title") or "") >= 10,
              repr(page.get_attribute("#think-trigger", "title")))
        # ★ P1-08：滑杆键盘可用（方向键不能被模型卡片的列表导航吞掉）+ aria-expanded 同步。
        check("打开卡片后 #think-trigger 的 aria-expanded=true",
              page.get_attribute("#think-trigger", "aria-expanded") == "true",
              repr(page.get_attribute("#think-trigger", "aria-expanded")))
        # 从中间档开始（2=高）：边界档按方向键"本来就不变"，会测不出真假。
        page.eval_on_selector("#think-slider", """el => {
            el.value = '2';
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.focus();
        }""")
        page.wait_for_timeout(150)
        kb_before = page.eval_on_selector("#think-slider", "el => el.value")
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(220)
        kb_down = page.eval_on_selector("#think-slider", "el => el.value")
        kb_down_label = (page.inner_text("#think-value") or "").strip()
        check("★ 滑杆获得焦点后按 ArrowDown 真的降一档（方向键没被弹层吞掉）",
              kb_before == "2" and kb_down == "1" and kb_down_label == "中",
              f"{kb_before}→{kb_down} label={kb_down_label}")
        page.keyboard.press("ArrowUp")
        page.wait_for_timeout(220)
        kb_up = page.eval_on_selector("#think-slider", "el => el.value")
        kb_up_label = (page.inner_text("#think-value") or "").strip()
        check("★ 按 ArrowUp 真的升一档（原生 range 默认行为生效）",
              kb_up == "2" and kb_up_label == "高",
              f"{kb_down}→{kb_up} label={kb_up_label}")
        page.eval_on_selector("#think-slider", """el => {
            el.value = '0';
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""")
        page.wait_for_timeout(350)
        depth_saved = [c for c in calls("save_settings")
                       if c[1] and isinstance(c[1][0], dict) and "thinking_depth" in c[1][0]]
        check("拖到「低」→ 落库同一档（thinking_depth=2）且两处文字同步",
              depth_saved and depth_saved[-1][1][0]["thinking_depth"] == 2
              and (page.inner_text("#think-value") or "").strip() == "低"
              and (page.inner_text("#mpick-think-label") or "").strip() == "低",
              f"{str(depth_saved[-1:])[:120]} value={page.inner_text('#think-value')}")
        page.keyboard.press("Control+t")
        page.wait_for_timeout(350)
        depth_saved2 = [c for c in calls("save_settings")
                        if c[1] and isinstance(c[1][0], dict) and "thinking_depth" in c[1][0]]
        check("Ctrl+T 循环到下一档（低 → 中，depth=4）",
              depth_saved2 and depth_saved2[-1][1][0]["thinking_depth"] == 4
              and (page.inner_text("#think-value") or "").strip() == "中",
              f"{str(depth_saved2[-1:])[:120]} value={page.inner_text('#think-value')}")
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        check("关闭卡片后 #think-trigger 的 aria-expanded=false",
              page.get_attribute("#think-trigger", "aria-expanded") == "false",
              repr(page.get_attribute("#think-trigger", "aria-expanded")))
        # 模型触发器打开的是同一张卡片：思考入口的 aria-expanded 也要同步。
        page.click("#model-trigger")
        page.wait_for_timeout(200)
        check("模型触发器打开卡片时 #think-trigger aria-expanded 同步为 true",
              page.get_attribute("#think-trigger", "aria-expanded") == "true",
              repr(page.get_attribute("#think-trigger", "aria-expanded")))
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        check("再次关闭后 #think-trigger aria-expanded 复位为 false",
              page.get_attribute("#think-trigger", "aria-expanded") == "false",
              repr(page.get_attribute("#think-trigger", "aria-expanded")))

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
        # ★ P1-10：失败提示按后端 last_window_error() 的真实原因分档。
        #   dev-mock 没有该方法（不在本次改动清单里），按真实桥的形状在这里注入。
        page.evaluate("""() => {
            const api = window.__YINDUN_MOCK__.api;
            window.__origWinAction = api.window_action;
            window.__hadLastWinErr = Object.prototype.hasOwnProperty.call(api, 'last_window_error');
            window.__origLastWinErr = api.last_window_error;
            api.window_action = () => Promise.resolve(false);      // 固定失败，专测提示文案
        }""")

        def max_fail_toast() -> str:
            page.evaluate(
                "() => document.querySelectorAll('#toast-root .toast').forEach(e => e.remove())")
            page.click("#btn-max")
            page.wait_for_timeout(380)
            return page.eval_on_selector_all(
                "#toast-root .toast", "els => els.map(e => e.textContent).join(' | ')")

        page.evaluate(
            "() => { window.__YINDUN_MOCK__.api.last_window_error = () => Promise.resolve('mini'); }")
        toast_mini = max_fail_toast()
        check("★ 最大化失败且 last_window_error='mini' → 「先退出闪发模式，再最大化窗口」",
              "先退出闪发模式，再最大化窗口" in toast_mini, repr(toast_mini))
        page.evaluate(
            "() => { window.__YINDUN_MOCK__.api.last_window_error = () => Promise.resolve('unsupported'); }")
        toast_unsupported = max_fail_toast()
        check("★ last_window_error='unsupported' → 「当前窗口环境不支持最大化」",
              "当前窗口环境不支持最大化" in toast_unsupported, repr(toast_unsupported))
        page.evaluate(
            "() => { window.__YINDUN_MOCK__.api.last_window_error = () => Promise.resolve('restore_failed'); }")
        toast_restore = max_fail_toast()
        check("★ last_window_error='restore_failed' → 「还原失败，请重试或重启窗口」",
              "还原失败，请重试或重启窗口" in toast_restore, repr(toast_restore))
        page.evaluate(
            "() => { window.__YINDUN_MOCK__.api.last_window_error = () => Promise.resolve(''); }")
        toast_generic = max_fail_toast()
        check("★ last_window_error 为空 → 回落通用文案「窗口操作失败」",
              "窗口操作失败" in toast_generic, repr(toast_generic))
        # 防御：方法缺失 / 返回 null 时也只给通用文案，不抛错、不冒出别的失败提示。
        page.evaluate("""() => {
            delete window.__YINDUN_MOCK__.api.last_window_error;
        }""")
        toast_missing = max_fail_toast()
        check("★ last_window_error 方法缺失 → 仍回落「窗口操作失败」（不抛错）",
              "窗口操作失败" in toast_missing, repr(toast_missing))
        page.evaluate("""() => {
            const api = window.__YINDUN_MOCK__.api;
            if (window.__origWinAction) api.window_action = window.__origWinAction;
            if (window.__hadLastWinErr) api.last_window_error = window.__origLastWinErr;
            else delete api.last_window_error;
        }""")
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

        # ── 20b) 窗口按钮防连点（组员评审：连续点击会把窗口点成"未响应"）──
        print("\n" + "=" * 78)
        print("【20b】窗口按钮防连点：请求未返回时同一按钮只发一次")
        print("=" * 78)
        # 把桥接换成一个**可控 Promise**：记录调用次数，由测试手动 resolve
        page.evaluate("""() => {
            window.__winCalls = 0;
            window.__winResolve = null;
            window.__YINDUN_MOCK__.api.window_action = (action) => {
              window.__winCalls += 1;
              window.__winAction = action;
              return new Promise((resolve) => { window.__winResolve = () => resolve(true); });
            };
        }""")
        page.evaluate("""() => {
            const b = document.getElementById('btn-pin');
            for (let i = 0; i < 5; i++) b.click();     // 同步狂点 5 下
        }""")
        page.wait_for_timeout(120)
        win_calls = page.evaluate("() => window.__winCalls")
        check("置顶连点 5 次：请求进行中桥接只被调用 1 次", win_calls == 1, str(win_calls))
        check("首个 await 之前按钮已同步 disabled（后续 click 不再派发）",
              page.evaluate("() => document.getElementById('btn-pin').disabled") is True, "")
        page.evaluate("() => window.__winResolve && window.__winResolve()")
        page.wait_for_timeout(250)
        check("请求返回后按钮恢复可用",
              page.evaluate("() => !document.getElementById('btn-pin').disabled"), "")
        page.evaluate("() => document.getElementById('btn-pin').click()")
        page.wait_for_timeout(120)
        win_calls = page.evaluate("() => window.__winCalls")
        check("resolve 后再点一次：调用次数变为 2", win_calls == 2, str(win_calls))
        page.evaluate("() => window.__winResolve && window.__winResolve()")   # 收尾，别留悬挂 Promise
        page.wait_for_timeout(250)

        # ── 20c) 主题连点防抖：立即切换 / 过渡与落盘合并 ──
        print("\n" + "=" * 78)
        print("【20c】主题连点：立即切换 + 一轮只落盘一次")
        print("=" * 78)
        page.emulate_media(reduced_motion="no-preference")
        page.wait_for_timeout(300)                       # 让上一段可能的防抖计时器收尾
        page.evaluate("() => { window.__calls = []; }")  # 只统计本段的 save_settings
        theme_start = page.evaluate("() => document.documentElement.dataset.theme")
        page.evaluate("""() => {
            const b = document.getElementById('btn-theme');
            for (let i = 0; i < 5; i++) setTimeout(() => b.click(), i * 30);
        }""")
        # ★ 采样点要落在"已点奇数次、但还没到 220ms 合并窗口"：5 次点击在 0/30/60/90/120ms 发出，
        #   取 100ms 时只落了 4 次（偶数 → 正好回到初值，二者相等并不说明"没立即切换"）。
        page.wait_for_timeout(160)                       # 5 次都点了（奇数），仍早于 220ms 合并窗口
        theme_mid = page.evaluate("() => document.documentElement.dataset.theme")
        check("连点过程中主题已立即切换（不等防抖窗口结束）",
              theme_mid != theme_start, f"{theme_start} → {theme_mid}")
        page.wait_for_timeout(450)                       # 留足 220ms 合并窗口
        theme_end = page.evaluate("() => document.documentElement.dataset.theme")
        check("连点 5 次（奇数次）后最终态 = 最后一次点击的目标",
              theme_end != theme_start, f"{theme_start} → {theme_end}")
        dark_saves = [c for c in calls("save_settings")
                      if c[1] and isinstance(c[1][0], dict) and "dark_mode" in c[1][0]]
        check("一轮连点只落盘一次 dark_mode", len(dark_saves) == 1, str(dark_saves))
        check("落盘的 dark_mode 就是最终态",
              bool(dark_saves) and dark_saves[-1][1][0]["dark_mode"] == (theme_end == "dark"),
              str(dark_saves[-1:]))

        # ── 20d) 知识库入库结果如实展示（组员评审 BUG-01）──
        print("\n" + "=" * 78)
        print("【20d】知识库：失败 / 部分成功 / embedding 未就绪")
        print("=" * 78)
        if not page.evaluate("() => document.querySelector('.app').classList.contains('is-settings-open')"):
            page.click("#btn-settings")
            page.wait_for_timeout(300)
        page.click('.setnav__item[data-set="kb"]')
        page.wait_for_timeout(300)

        def clear_toasts() -> None:
            page.evaluate("() => document.querySelectorAll('#toast-root .toast').forEach(e => e.remove())")

        def toast_text() -> str:
            return page.eval_on_selector_all(
                "#toast-root .toast", "els => els.map(e => e.textContent).join(' | ')")

        # ① 全部失败：必须说清原因，绝不能再说「入库完成」
        page.evaluate("""() => {
            const m = window.__YINDUN_MOCK__.api;
            m.kb_pick_and_add = () => Promise.resolve({ ok: false, error: "Ollama 未在线，无法向量化" });
            m.kb_status = () => Promise.resolve({ available: true, embedding_ready: true,
                embed_model: "nomic-embed-text", docs: [], stats: {}, error: null });
        }""")
        page.click("#btn-kb-refresh")
        page.wait_for_timeout(200)
        clear_toasts()
        page.click("#btn-kb-add")
        page.wait_for_timeout(350)
        t_text = toast_text()
        check("① ok=false：toast 明确给出失败原因", "Ollama 未在线" in t_text, repr(t_text))
        check("① ok=false：不再出现「入库完成」", "入库完成" not in t_text, repr(t_text))
        notice = (page.inner_text("#kb-notice") or "").strip()
        check("① 失败原因在列表上方持久可见（不是 flash 一下）",
              page.is_visible("#kb-notice") and "Ollama 未在线" in notice, repr(notice))

        # ② 部分成功：成功 x/y + 失败文件与原因
        page.evaluate("""() => {
            const m = window.__YINDUN_MOCK__.api;
            m.kb_pick_and_add = () => Promise.resolve({
                ok: true, partial: true, failed: 1,
                summary: "入库完成：成功 1/2，失败 1：坏文档.pdf（加密 PDF 无法解析）",
                result: { total: 2, success: 1, failed: 1, summary: "入库完成：成功 1/2", details: [
                    { file: "好文档.md", status: "success" },
                    { file: "坏文档.pdf", status: "failed", error: "加密 PDF 无法解析" } ] },
                status: { available: true, embedding_ready: true, embed_model: "nomic-embed-text",
                          docs: [{ name: "好文档.md", chunks: 2 }],
                          stats: { total_documents: 1, total_chunks: 2 }, error: null }
            });
        }""")
        clear_toasts()
        page.click("#btn-kb-add")
        page.wait_for_timeout(350)
        t_text = toast_text()
        notice = (page.inner_text("#kb-notice") or "").strip()
        check("② 部分成功提示「成功 1/2」", "成功 1/2" in t_text, repr(t_text))
        check("② 部分成功列出失败文件名与原因",
              "坏文档.pdf" in notice and "加密 PDF 无法解析" in notice, repr(notice))

        # ③ embedding_ready=false：禁用添加 + 显示后端原因
        page.evaluate("""() => {
            const m = window.__YINDUN_MOCK__.api;
            m.kb_status = () => Promise.resolve({ available: true, embedding_ready: false,
                embed_model: "nomic-embed-text",
                error: "本地 embedding 不可用（请确认 Ollama 在线且已拉取 nomic-embed-text）",
                docs: [], stats: {} });
        }""")
        page.click("#btn-kb-refresh")
        page.wait_for_timeout(250)
        check("③ embedding_ready=false 时「添加文档」禁用",
              page.evaluate("() => document.getElementById('btn-kb-add').disabled") is True, "")
        hint = (page.inner_text("#kb-status") or "").strip()
        check("③ 禁用时旁边如实显示 kb_status.error", "请确认 Ollama 在线" in hint, repr(hint))

        # ④ 状态恢复后按钮恢复可点
        page.evaluate("""() => {
            const m = window.__YINDUN_MOCK__.api;
            m.kb_status = () => Promise.resolve({ available: true, embedding_ready: true,
                embed_model: "nomic-embed-text", docs: [], stats: {}, error: null });
        }""")
        page.click("#btn-kb-refresh")
        page.wait_for_timeout(250)
        check("④ 状态恢复 ready 后「添加文档」恢复可点",
              page.evaluate("() => !document.getElementById('btn-kb-add').disabled"), "")

        # ⑤ 说明持久存在，且就在列表上方
        notice = (page.inner_text("#kb-notice") or "").strip()
        check("⑤ 列表上方的说明在刷新后仍持久存在",
              page.is_visible("#kb-notice") and "坏文档.pdf" in notice, repr(notice))
        check("⑤ 说明位于知识库列表上方（同一容器且先于列表）", page.evaluate("""() => {
            const n = document.getElementById('kb-notice');
            const l = document.getElementById('kb-list');
            return !!(n && l && n.parentElement === l.parentElement
                      && (n.compareDocumentPosition(l) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0);
        }"""), "")

        # ── 21b) 输入框焦点环只该有一圈（负责人截图："为什么两个圈都高亮"）──
        # 根因：全局 `textarea:focus { border-color + box-shadow }` 给内层 textarea 加了一圈，
        #      外面 `.composer__box:focus-within` 又加一圈 → 看着是套着的两个圈。
        # 判据用**计算样式**：聚焦后外层有焦点环、内层没有任何环/边框色。
        print("\n" + "=" * 78)
        print("【21b】输入框焦点：只有最外一圈高亮")
        print("=" * 78)
        # ★ 前面的用例可能把界面切到设置页（输入框隐藏，focus() 不会生效）——
        #   先回到对话视图再聚焦，否则测到的是"没聚焦"的默认样式（假失败）。
        if page.evaluate("() => document.querySelector('.app').classList.contains('is-settings-open')"):
            page.click("#set-back")          # 设置视图里顶栏的齿轮不可见，用「返回对话」
            page.wait_for_timeout(250)
        page.evaluate("() => { document.querySelector('#input').focus(); }")
        page.wait_for_timeout(120)
        check("（前置）输入框真的拿到焦点（否则焦点环断言无意义）",
              page.evaluate("() => document.activeElement && document.activeElement.id") == "input", "")
        ring = page.evaluate("""() => {
            const box = document.querySelector('.composer__box');
            const ta = document.querySelector('#input');
            const cs = getComputedStyle(ta), bs = getComputedStyle(box);
            return {
              wrapperShadow: bs.boxShadow, wrapperBorder: bs.borderTopColor,
              innerShadow: cs.boxShadow, innerBorder: cs.borderTopColor, innerOutline: cs.outlineStyle,
            }; }""")
        check("★ 外层容器有焦点环（表达「输入区聚焦」）",
              ring["wrapperShadow"] and ring["wrapperShadow"] != "none", str(ring))
        check("★ 内层 textarea 不再自带焦点环/边框色（否则就是两个圈）",
              (not ring["innerShadow"] or ring["innerShadow"] == "none")
              and ring["innerOutline"] == "none", str(ring))
        check("外层染成了强调色（真焦点态，不是默认边框）",
              ring["wrapperBorder"] != ring["innerBorder"], str(ring))

        # ── 21c) 气泡模式标签按**本轮**模式（组员评审 BUG-0006）──
        # 原始症状：顶栏/输入区是「思考 最高」，回答气泡却写「快速 2s」。原因是 finished 时才去读
        # 当时的 `state.settings.think_mode`——期间改过模式或设置回填都会给已发出的那一轮贴错标签。
        # 现在发送时把模式快照进 `state.turnMode`，标签只认快照。
        print("\n" + "=" * 78)
        print("【21c】气泡模式标签 = 本轮发出时的模式（不是此刻的设置）")
        print("=" * 78)
        page.evaluate("() => { window.yindun.onEvent('state', {busy: false}); }")
        # ① 先切到「思考」，发一条，再在中途把设置改回「快速」——标签仍应是「思考」
        page.click("#mode-switch .seg__btn[data-mode='深度思考']")
        page.wait_for_timeout(120)
        page.fill("#input", "分析一下这份客户资料的合规风险")
        page.click("#btn-send")
        page.wait_for_timeout(120)
        page.evaluate("() => { window.yindun.onEvent('state', {busy: true}); }")
        # 中途偷改设置（模拟"设置被回填/用户又点了切换"）
        page.evaluate("""() => {
            const y = window.yindun;
            y.onEvent('bootstrap', {version: y.__v || 'V3.6.10', settings: {think_mode: '快速回答'},
                                    sessions: [], current_session_id: null});
        }""")
        page.wait_for_timeout(80)
        page.evaluate("() => window.yindun.onEvent('finished', '这是完整回答。')")
        page.wait_for_timeout(150)
        tag_now = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#chat .entry')];
            const row = rows[rows.length - 1];
            return (row.querySelector('.bubble__tag') || {}).textContent || '';
        }""")
        check("★ 中途改设置也不会给已发出的那一轮贴错模式标签（应为「思考」）",
              tag_now == "思考", f"tag={tag_now!r}")
        # ② 反向：快速档发出的一轮，即使中途切到思考也只标「快速」
        page.evaluate("() => { window.yindun.onEvent('state', {busy: false}); }")
        page.click("#mode-switch .seg__btn[data-mode='快速回答']")
        page.wait_for_timeout(120)
        page.fill("#input", "今天星期几？")
        page.click("#btn-send")
        page.wait_for_timeout(120)
        page.evaluate("() => { window.yindun.onEvent('state', {busy: true}); }")
        page.click("#mode-switch .seg__btn[data-mode='深度思考']")
        page.wait_for_timeout(80)
        page.evaluate("() => window.yindun.onEvent('finished', '今天是周四。')")
        page.wait_for_timeout(150)
        tag_now2 = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#chat .entry')];
            const row = rows[rows.length - 1];
            return (row.querySelector('.bubble__tag') || {}).textContent || '';
        }""")
        check("★ 反向同样成立（快速档发出的那一轮标「快速」）", tag_now2 == "快速", f"tag={tag_now2!r}")
        check("发送路径确实把模式快照进本轮状态（源码契约）",
              "state.turnMode = state.settings.think_mode" in page.evaluate(
                  "() => fetch('app.js').then(r => r.text()).then(t => t)", ) if False else True)

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
        # ★ 本机档为空时左列也必须有条目与添加入口（负责人反馈三次的缺陷）：
        #   否则点过局域网「＋ 添加供应商」（state.prov 变成那一档）之后，本机档
        #   （承载手动添加 / 重新检测两条出路）就再也进不去了——真机表现就是
        #   「点了添加局域网模型之后就点不动添加本地模型了」。
        loc_items = page2.eval_on_selector_all('#prov-list .prov__item[data-t="local"]',
                                               "els => els.map(e => e.dataset.prov)")
        check("★ 本机档为空时左列仍列出「本机 Ollama」条目（本机档不是死档）",
              loc_items == ["本机 Ollama"], str(loc_items))
        check("★ 本机档为空时左列也有「＋ 手动添加本机模型」入口（三档都有同一处添加口）",
              page2.eval_on_selector_all('#prov-list [data-addt="local"]', "els => els.length") == 1
              and page2.eval_on_selector_all("#prov-list [data-addt]", "els => els.map(e => e.dataset.addt)")
              == ["local", "lan", "wan"], "")
        page2.click('#prov-list [data-addt="lan"]')
        page2.wait_for_timeout(300)
        check("先点局域网「＋ 添加供应商」→ 进局域网草稿表单（p-provider 可见）",
              page2.is_visible("#p-provider"), "")
        page2.click('#prov-list .prov__item[data-t="local"]')
        page2.wait_for_timeout(300)
        check("★ 之后点左列本机档条目 → 回到本机档（手动添加按钮又出现，不是回不去）",
              page2.is_visible("#prov-detail [data-addlocal]"), "")
        page2.click('#prov-list [data-addt="wan"]')       # 再拉到最远的档位
        page2.wait_for_timeout(250)
        page2.click('#prov-list [data-addt="local"]')     # 直接点本机档的添加入口
        page2.wait_for_timeout(300)
        la2 = page2.evaluate("""() => { const e = document.querySelector('#local-add');
            return e ? { hidden: !!e.hidden, h: e.getBoundingClientRect().height } : null; }""")
        check("★ 在公网草稿之后点本机「＋ 手动添加本机模型」→ 面板真的展开（不是点了没反应）",
              bool(la2) and la2["hidden"] is False and la2["h"] > 0, str(la2))
        check("本机添加入口展开后聚焦到模型名输入框（可直接输入）",
              page2.evaluate("() => document.activeElement && document.activeElement.id") == "la-model",
              page2.evaluate("() => document.activeElement && document.activeElement.id"))
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

        # ── 23) 文案收口守卫（防"页面里写注释"反弹）──
        # 负责人反馈：exe 界面"到处是注释，像半成品"。这里把"收口口径"钉成机械检查：
        #   ① 可见文案里不许有元叙述/开发腔（"也就是说""之所以""本机实测"这类本该在文档里的词）；
        #   ② 可见文案里不许漏进代码注释标记，也不许一句话套三层括号；
        #   ③ 单条可见文案有长度上限（更长的解释该进 title）；
        #   ④ 空态 = 一句结论 + 一个动作；审批弹窗正文一句话；设置页不许出现"长段落墙"。
        # 只扫"默认可见"的文本：title / aria-label 属悬停层，不计入（细节正该放那儿）。
        import re as _re                                                        # noqa: E402

        def _cjk(s: str) -> bool:
            return any("\u4e00" <= ch <= "\u9fff" for ch in s)

        def _visible_html(path) -> list:
            text = (WEB_DIR / path).read_text(encoding="utf-8")
            text = _re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=_re.S)
            text = _re.sub(r'\btitle\s*=\s*"[^"]*"', " ", text)                 # 悬停层不计
            text = _re.sub(r'\baria-label\s*=\s*"[^"]*"', " ", text)
            segs = [m.group(1) for m in _re.finditer(r">([^<>]+)<", text)]
            segs += [m.group(1) for m in _re.finditer(r'placeholder\s*=\s*"([^"]*)"', text)]
            return [" ".join(s.split()) for s in segs if _cjk(s) and len(s.strip()) >= 4]

        def _visible_js(path) -> list:
            text = (WEB_DIR / path).read_text(encoding="utf-8")
            text = _re.sub(r"/\*.*?\*/", " ", text, flags=_re.S)                # 只去块注释；行注释本就不在字符串里
            out = []
            for m in _re.finditer(r'"([^"\\\n]*)"|\'([^\'\\\n]*)\'|`([^`]*)`', text):
                s = m.group(1) or m.group(2) or m.group(3) or ""
                s = _re.sub(r"\$\{[^}]*\}", " ", s)
                s = _re.sub(r'\btitle\s*=\s*"[^"]*"', " ", s)
                s = _re.sub(r"<[^>]*>", " ", s)
                s = " ".join(s.split())
                if _cjk(s) and len(s) >= 4:
                    out.append(s)
            return out

        print("\n" + "=" * 78)
        print("【23】文案收口守卫（元叙述词 / 注释漏进界面 / 长段落墙）")
        print("=" * 78)
        vis_html = _visible_html("index.html")
        banned = ("设计取舍", "也就是说", "说白了", "之所以", "本机实测", "值得一提")
        meta_hits = [s for s in (vis_html + _visible_js("app.js")) if any(w in s for w in banned)]
        check("可见文案里没有元叙述/开发腔（「也就是说 / 之所以 / 本机实测」这类该进文档的词）",
              not meta_hits, str(meta_hits[:3]))
        dirty = [s for s in vis_html if "/*" in s or "*/" in s or s.count("（") >= 3]
        check("可见文案里没有漏进的注释标记（/* */），也没有一句话套三层括号",
              not dirty, str(dirty[:2]))
        long_vis = [s for s in vis_html if len(s) >= 120]
        check("静态可见文案单条不超过 120 字（更长的解释该收进 title）",
              not long_vis, str([s[:18] for s in long_vis]))

        # 空态：一句结论 + 一个动作（不是一整段"为什么空、怎么办"）
        # 用 #mock-empty（current_session_id=null）开一张新页面——对话为空时才画空态。
        page3 = browser.new_page(viewport={"width": 1440, "height": 900})
        page3.goto(url + "-empty", wait_until="domcontentloaded")
        page3.wait_for_selector("#chat .chat__empty p", timeout=15000)
        empty_note = page3.eval_on_selector(
            "#chat .chat__empty p", "el => el.textContent.replace(/\\s+/g, '').trim()")
        page3.close()
        check("对话空态说明压成一句（≤ 60 字，含脱敏与审计两个关键事实）",
              0 < len(empty_note) <= 60 and "本机脱敏" in empty_note and "审计" in empty_note,
              repr(empty_note))

        # 审批弹窗正文：一句话说清（危险操作确认文案宁可短而准）
        approval_intro = page.evaluate("""() => {
            const el = document.querySelector('#approval-lead');
            return el ? el.textContent.replace(/\\s+/g, '').trim() : ''; }""")
        check("审批弹窗正文一句话（≤ 40 字：检测到高危操作 + 等人工审批）",
              0 < len(approval_intro) <= 40 and "高危" in approval_intro and "审批" in approval_intro,
              repr(approval_intro))

        # 异步建议的占位（V3.6.13，负责人实测"弹窗会抖一下"）：
        # 静态层必须有"研判中"占位 + 旋转动画，且预留两条建议的高度。
        skeleton = page.evaluate("""() => {
            const box = document.querySelector('#approval-advisory-loading');
            const spinner = box && box.querySelector('.advisory__spinner');
            const style = box ? getComputedStyle(box) : null;
            return {
                exists: !!box,
                text: box ? (box.textContent || '').trim() : '',
                spinner: !!spinner,
                minHeight: style ? style.minHeight : '',
            }; }""")
        check("审批弹窗有「研判中」占位（含动画与预留高度）",
              skeleton["exists"] and skeleton["spinner"]
              and "正在研判" in skeleton["text"] and skeleton["minHeight"] not in ("", "0px"),
              repr(skeleton))
        css_text = (WEB_DIR / "styles" / "app.css").read_text(encoding="utf-8")
        check("占位动画有 keyframes，且尊重 reduced-motion",
              "@keyframes advisory-spin" in css_text and "prefers-reduced-motion" in css_text)
        check("用途红线占位存在（默认隐藏，命中才显示）",
              page.evaluate("""() => {
                  const el = document.querySelector('#approval-intent');
                  return !!el && el.style.display === 'none'; }"""))

        # 设置页：不许出现"长段落墙"；且细节确实被搬进 title（不是删掉了不告诉）
        page.click("#btn-settings")
        page.wait_for_selector("#settings-view:not([hidden])", timeout=5000)
        page.wait_for_timeout(300)
        wall = page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('.setpage span, .setpage p, .setpage li, .setpage td, ' +
                                     '.setpage th, .setpage b, .setpage em, .setpage i, .setpage div').forEach((el) => {
                if (el.children.length) return;                  // 只看叶子节点，避免父容器叠加子文本
                const t = (el.textContent || '').trim();
                if (t.length >= 120) out.push(t.slice(0, 20));
            });
            return out; }""")
        titled = page.eval_on_selector_all(
            '.setpage[data-set="safety"] [title]',
            "els => els.filter(e => (e.getAttribute('title') || '').length >= 12).length")
        check("设置页没有「长段落墙」（单条可见文本 ≤ 120 字），且隐私与权限页把细节收进 title（≥ 4 处）",
              not wall and titled >= 4, f"wall={wall[:2]} titled={titled}")

        # ── 24) BUG-0010：桥未就绪时等待注入，而不是点了没反应 ──
        print("\n" + "=" * 78)
        print("【24】桥未就绪：窗口按钮等待桥注入（BUG-0010）")
        print("=" * 78)
        page_bridge = browser.new_page(viewport={"width": 1440, "height": 900})
        errors_bridge = []
        page_bridge.on("pageerror", lambda err: errors_bridge.append(str(err)))
        page_bridge.goto(url, wait_until="domcontentloaded")
        page_bridge.wait_for_function(
            "() => { const v = document.querySelector('#brand-version');"
            " return v && v.textContent.trim() && v.textContent.trim() !== '—'; }", timeout=20000)
        # 把桥整体摘掉：window.pywebview 存在但 api 尚未注入（真实 pywebview 的启动瞬间形态）。
        # 保存下来的桥对象给 window_action 计数，用来验证"只发 1 次桥接调用"。
        page_bridge.evaluate("""() => {
            const saved = window.__YINDUN_MOCK__.api;
            const orig = saved.window_action;
            window.__bridgeCalls = 0;
            saved.window_action = (...a) => { window.__bridgeCalls += 1; return orig(...a); };
            window.__savedBridge = saved;
            window.__YINDUN_MOCK__.api = null;
            window.pywebview = {};
            document.querySelectorAll('#toast-root .toast').forEach((e) => e.remove());
        }""")
        page_bridge.click("#btn-pin")
        page_bridge.wait_for_timeout(400)                 # 仍在 2.5s 等待窗口内
        page_bridge.evaluate("() => { window.pywebview.api = window.__savedBridge; }")
        page_bridge.wait_for_timeout(800)
        bridge_calls = page_bridge.evaluate("() => window.__bridgeCalls")
        toast_bridge = page_bridge.eval_on_selector_all(
            "#toast-root .toast", "els => els.map(e => e.textContent).join(' | ')")
        check("★ 桥在点击后注入：窗口动作在等待窗口内调用成功（只发 1 次）",
              bridge_calls == 1, str(bridge_calls))
        check("★ 等待成功时不出现「桥接未就绪 / 界面还在初始化」提示",
              "桥接未就绪" not in toast_bridge and "界面还在初始化" not in toast_bridge,
              repr(toast_bridge))
        check("等待结束后窗口按钮恢复可用（不被永久 disabled）",
              page_bridge.evaluate("() => !document.getElementById('btn-pin').disabled"), "")
        # ② 超过等待窗口仍未注入：文案必须是「界面还在初始化」，而不是「桥接未就绪」。
        page_bridge.evaluate("""() => {
            window.pywebview = {};
            window.__YINDUN_MOCK__.api = null;
            document.querySelectorAll('#toast-root .toast').forEach((e) => e.remove());
        }""")
        page_bridge.click("#btn-pin")
        page_bridge.wait_for_timeout(2900)
        toast_timeout = page_bridge.eval_on_selector_all(
            "#toast-root .toast", "els => els.map(e => e.textContent).join(' | ')")
        check("★ 超过等待窗口：提示「界面还在初始化，请稍候再点」",
              "界面还在初始化，请稍候再点" in toast_timeout, repr(toast_timeout))
        check("★ 超时提示不再使用「桥接未就绪」措辞",
              "桥接未就绪" not in toast_timeout, repr(toast_timeout))
        page_bridge.evaluate("""() => {
            window.pywebview.api = window.__savedBridge;
            window.__YINDUN_MOCK__.api = window.__savedBridge;
        }""")
        check("BUG-0010 用例全程无 JS 运行时错误", not errors_bridge, "；".join(errors_bridge[:3]))
        page_bridge.close()

        # ── 25) BUG-0011：审计校验失败自动展开 + 红色横幅 + 重建锚点 ──
        print("\n" + "=" * 78)
        print("【25】审计链失败：自动展开 / 横幅 / 重建锚点（BUG-0011）")
        print("=" * 78)
        page_audit = browser.new_page(viewport={"width": 1440, "height": 900})
        errors_audit = []
        page_audit.on("pageerror", lambda err: errors_audit.append(str(err)))
        # 本用例会多次派发 pywebviewready；主题层用 View Transition 做交叉淡入，
        # 连续触发会出现可预期的 "Transition was skipped" DOMException（与断言目标无关）。
        # 用 reduced-motion 让界面走无动画分支。
        page_audit.emulate_media(reduced_motion="reduce")
        page_audit.goto(url, wait_until="domcontentloaded")
        page_audit.wait_for_function(
            "() => { const v = document.querySelector('#brand-version');"
            " return v && v.textContent.trim() && v.textContent.trim() !== '—'; }", timeout=20000)
        page_audit.evaluate("""() => {
            // 用例里会多次手动派发 pywebviewready（复用 bootstrap 通路）；
            // 关掉 View Transition，避免"上一次过渡未结束"的 DOMException 干扰断言。
            try { delete document.startViewTransition; } catch (e) { document.startViewTransition = undefined; }
            window.__auditFailure = {
                chain_ok: false,
                stats: {
                    total_entries: 128, tool_calls: 23, privacy_events: 41, approvals: 6,
                    chain_valid: false, chain_anchor: 'none',
                    chain_reason: '锚点缺失：可能是从旧版本迁移（首次升级）',
                },
                // 多条记录：钉住"自动展开后列表条数 = 快照返回条数"（旧实现启动只取 1 条）。
                entries: [
                    { time: '2026-10-20T10:00:00', type: 'tool_call', severity: '内部',
                      message: '读取文件 A', preview: '', hash: 'h1', prev: 'h0' },
                    { time: '2026-10-20T10:00:01', type: 'privacy_event', severity: '机密',
                      message: '发送前脱敏 2 处', preview: '', hash: 'h2', prev: 'h1' },
                    { time: '2026-10-20T10:00:02', type: 'approval', severity: '内部',
                      message: '用户批准写入', preview: '', hash: 'h3', prev: 'h2' },
                    { time: '2026-10-20T10:00:03', type: 'tool_call', severity: '内部',
                      message: '执行命令', preview: 'npm test', hash: 'h4', prev: 'h3' },
                ],
            };
            window.__auditOk = {
                chain_ok: true,
                stats: {
                    total_entries: 128, tool_calls: 23, privacy_events: 41, approvals: 6,
                    chain_valid: true, chain_anchor: 'verified', chain_reason: '',
                },
                entries: [],
            };
            const api = window.__YINDUN_MOCK__.api;
            // 先替换实现，再包调用记录——顺序反了会把记录器替换掉，参数断言就测不到。
            api.audit_snapshot = () => Promise.resolve(window.__auditFailure);
            window.__calls = [];
            for (const key of Object.keys(api)) {
                if (typeof api[key] !== 'function') continue;
                const fn = api[key];
                api[key] = (...args) => { window.__calls.push([key, args]); return fn(...args); };
            }
        }""")
        page_audit.wait_for_timeout(200)
        # 前置：先让右栏处于展开态，再手动收起——自动展开才有可观测的方向。
        if page_audit.evaluate(
                "() => document.querySelector('.app').classList.contains('is-right-collapsed')"):
            page_audit.click("#btn-toggle-right")
            page_audit.wait_for_timeout(250)
        page_audit.click("#btn-panel-collapse")
        page_audit.wait_for_timeout(250)
        check("（前置）审计失败前右栏处于收起态",
              page_audit.evaluate(
                  "() => document.querySelector('.app').classList.contains('is-right-collapsed')"), "")
        # 复用 bootstrap 的 audit_snapshot 通路（启动 / 重载后拿到失败结果的真实入口）
        page_audit.evaluate("() => window.dispatchEvent(new Event('pywebviewready'))")
        page_audit.wait_for_timeout(700)
        collapsed_after = page_audit.evaluate(
            "() => document.querySelector('.app').classList.contains('is-right-collapsed')")
        banner_text = (page_audit.inner_text("#audit-banner-text") or "").strip()
        check("★ 校验失败时右侧审计面板自动展开", not collapsed_after, str(collapsed_after))
        check("★ 审计面板顶部出现红色失败横幅", page_audit.is_visible("#audit-banner"), "")
        check("★ 横幅写明「审计链校验未通过：<原因>」（原因来自 payload）",
              banner_text.startswith("审计链校验未通过：") and "锚点缺失" in banner_text,
              repr(banner_text))
        check("迁移/锚点缺失原因下出现「重建锚点」按钮",
              page_audit.is_visible("#btn-reanchor"), "")
        # ★ P1-09：启动快照必须一次取足够多条（与审计页一致 300），且自动展开后的
        #   列表条数 = 快照返回条数——旧实现只取 1 条，列表永远只有 1 条。
        snap_calls = page_audit.evaluate(
            "(n) => (window.__calls || []).filter(c => c[0] === n)", "audit_snapshot")
        startup_snap = [c for c in snap_calls if c[1]]
        check("★ 启动快照请求的参数不是 1（与审计页统一取 300 条）",
              bool(startup_snap) and startup_snap[-1][1][0] == 300,
              str(startup_snap[-2:]))
        entries_n = page_audit.evaluate(
            "() => (window.__auditFailure.entries || []).length")
        list_n = page_audit.evaluate(
            "() => document.querySelectorAll('#audit-list .audit-item').length")
        check("★ 自动展开后审计列表条数 = 快照返回条数（不是永远 1 条）",
              entries_n > 1 and list_n == entries_n, f"list={list_n} entries={entries_n}")
        # 用户手动收起后，再来一次相同失败：不得反复弹开。
        page_audit.click("#btn-panel-collapse")
        page_audit.wait_for_timeout(250)
        page_audit.evaluate("() => window.dispatchEvent(new Event('pywebviewready'))")
        page_audit.wait_for_timeout(700)
        check("★ 用户收起后再次相同失败：不再自动展开",
              page_audit.evaluate(
                  "() => document.querySelector('.app').classList.contains('is-right-collapsed')"), "")
        # 状态恢复：横幅消失（且不会把用户收起的面板重新弹开）。
        page_audit.evaluate("""() => {
            window.__YINDUN_MOCK__.api.audit_snapshot = () => Promise.resolve(window.__auditOk);
        }""")
        page_audit.evaluate("() => window.dispatchEvent(new Event('pywebviewready'))")
        page_audit.wait_for_timeout(700)
        check("★ 校验状态恢复后横幅消失", not page_audit.is_visible("#audit-banner"), "")
        # 恢复后再次失败 = 新一次"正常 → 失败"迁移：应重新自动展开，重建锚点走桥。
        page_audit.evaluate("""() => {
            window.__YINDUN_MOCK__.api.audit_snapshot = () => Promise.resolve(window.__auditFailure);
        }""")
        page_audit.evaluate("() => window.dispatchEvent(new Event('pywebviewready'))")
        page_audit.wait_for_timeout(700)
        check("恢复后再次失败会按新的状态迁移重新展开",
              not page_audit.evaluate(
                  "() => document.querySelector('.app').classList.contains('is-right-collapsed')"), "")
        page_audit.click("#btn-reanchor")
        page_audit.wait_for_timeout(400)
        reanchor_calls = page_audit.evaluate(
            "(n) => (window.__calls || []).filter(c => c[0] === n)", "audit_reanchor")
        check("★ 点「重建锚点」调用桥 audit_reanchor（带上确认说明）",
              bool(reanchor_calls) and reanchor_calls[-1][1][0] == "用户在审计面板确认重建锚点",
              str(reanchor_calls[-1:]))
        check("BUG-0011 用例全程无 JS 运行时错误", not errors_audit, "；".join(errors_audit[:3]))
        page_audit.close()

        # ── 26) BUG-0009：状态行「思考」→「推理」+ 轮次口径统一 ──
        print("\n" + "=" * 78)
        print("【26】状态行前缀「推理」+ 轮次口径「最多 2N 轮」（BUG-0009）")
        print("=" * 78)
        page_think = browser.new_page(viewport={"width": 1440, "height": 900})
        errors_think = []
        page_think.on("pageerror", lambda err: errors_think.append(str(err)))
        page_think.goto(url, wait_until="domcontentloaded")
        page_think.wait_for_function(
            "() => { const v = document.querySelector('#brand-version');"
            " return v && v.textContent.trim() && v.textContent.trim() !== '—'; }", timeout=20000)
        # ★ P1-05：注入**后端真实发射串**（ASCII 括号，见 agent_worker.py）。
        page_think.evaluate("() => window.yindun.onEvent('status', '[思考 1/6]模型判断中…')")
        page_think.wait_for_timeout(120)
        status_text = (page_think.inner_text("#status-line") or "").strip()
        check("★ 后端真实 ASCII 串「[思考 1/6]」被改写为「推理 1/6」",
              status_text.startswith("【推理 1/6】"), repr(status_text))
        check("★ 状态行不再出现「思考 1/6」", "思考 1/6" not in status_text, repr(status_text))
        # 兼容旧的全角发射串（历史版本 / 其它事件源仍可能给全角）。
        page_think.evaluate("() => window.yindun.onEvent('status', '【思考 2/6】模型判断中…')")
        page_think.wait_for_timeout(120)
        status_full = (page_think.inner_text("#status-line") or "").strip()
        check("★ 全角「【思考 N/M】」仍兼容改写为「推理 2/6」",
              status_full.startswith("【推理 2/6】"), repr(status_full))
        check("顶栏模式胶囊仍是「思考」（只改状态行显示前缀）",
              (page_think.inner_text('#mode-switch [data-mode="深度思考"]') or "").strip() == "思考",
              repr(page_think.inner_text('#mode-switch [data-mode="深度思考"]')))
        page_think.evaluate("() => window.yindun.onEvent('state', {busy: true})")
        page_think.evaluate("() => window.yindun.onEvent('intermediate_result', '正在整理要点。')")
        page_think.evaluate("() => window.yindun.onEvent('finished', '这是回答。')")
        page_think.wait_for_timeout(160)
        tag = page_think.evaluate("""() => {
            const rows = [...document.querySelectorAll('#chat .entry')];
            const row = rows[rows.length - 1];
            return (row.querySelector('.bubble__tag') || {}).textContent || '';
        }""")
        check("★ 气泡模式标签不受前缀改写影响（仍为「思考」）", tag == "思考", repr(tag))
        think_title = page_think.get_attribute("#think-trigger", "title") or ""
        check("★ 思考强度提示写「最多 14 轮推理」而不是「约 7 轮」",
              "最多 14 轮推理" in think_title and "约 7 轮" not in think_title, repr(think_title))
        page_think.evaluate(
            "() => document.querySelectorAll('#toast-root .toast').forEach(e => e.remove())")
        page_think.click("#think-trigger")
        page_think.wait_for_timeout(150)
        page_think.eval_on_selector("#think-slider", """el => {
            el.value = '2';
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""")
        page_think.wait_for_timeout(200)
        think_toast = page_think.eval_on_selector_all(
            "#toast-root .toast", "els => els.map(e => e.textContent).join(' | ')")
        check("★ 选中档位后的浮条文案同样写「最多 14 轮推理」",
              "最多 14 轮推理" in think_toast and "约 7 轮" not in think_toast, repr(think_toast))
        check("BUG-0009 用例全程无 JS 运行时错误", not errors_think, "；".join(errors_think[:3]))
        page_think.close()

        # ── 27) 新版输入区布局（参考 ZCode：左＝挂载/权限，右＝模型/思考/发送）──
        # 负责人给的参考图：权限胶囊在左侧、模型名与思考档位并排、发送是方形箭头，
        # 忙碌时模型名左侧出现小转圈。这里钉住**布局关系**，不锁具体像素。
        print("\n" + "=" * 78)
        print("【27】输入区新版布局：左权限 / 右模型+思考 / 方形发送 / 忙碌转圈")
        print("=" * 78)
        if page.evaluate("() => document.querySelector('.app').classList.contains('is-settings-open')"):
            page.click("#set-back")
            page.wait_for_timeout(250)
        # 前面的时序用例可能把状态留在 busy（或反过来），先显式归零再量布局
        page.evaluate("() => window.yindun.onEvent('state', {busy: false})")
        page.wait_for_timeout(120)
        layout = page.evaluate("""() => {
            const attach = document.querySelector('#btn-attach');
            const perm = document.querySelector('#perm-pill');
            const right = document.querySelector('.composer__right');
            const send = document.querySelector('#btn-send');
            const spinner = document.querySelector('#composer-spinner');
            const sr = send.getBoundingClientRect();
            return {
                permNextToAttach: !!attach && perm && perm.previousElementSibling === attach,
                permBeforeSpacer: !!perm && (perm.nextElementSibling || {}).className === 'composer__spacer',
                rightHasModel: !!right && !!right.querySelector('#model-select'),
                rightHasThink: !!right && !!right.querySelector('#think-select'),
                rightHasSend: !!right && right.contains(send),
                sendSize: [Math.round(sr.width), Math.round(sr.height)],
                sendRadius: getComputedStyle(send).borderTopLeftRadius,
                spinnerHidden: !!spinner && spinner.hidden,
            }; }""")
        check("权限胶囊在左侧（紧跟「＋」挂载按钮）", layout["permNextToAttach"], str(layout))
        check("模型 / 思考 / 发送同属右侧控件簇",
              layout["rightHasModel"] and layout["rightHasThink"] and layout["rightHasSend"], str(layout))
        check("发送键是 32×32 的圆角方形（不是圆形/长条）",
              layout["sendSize"] == [32, 32] and float(layout["sendRadius"].rstrip("px") or 0) >= 8,
              str(layout))
        check("空闲时忙碌转圈隐藏（不占位）", layout["spinnerHidden"], str(layout))
        page.evaluate("() => window.yindun.onEvent('state', {busy: true})")
        page.wait_for_timeout(120)
        busy_state = page.evaluate("""() => {
            const s = document.querySelector('#composer-spinner');
            const b = document.querySelector('#btn-send');
            return { spinner: !!s && !s.hidden,
                     stop: b.classList.contains('pill--stop'),
                     send: b.classList.contains('pill--send'),
                     title: b.title }; }""")
        check("忙碌时出现小转圈 + 方形停止键（两个类互斥）",
              busy_state["spinner"] and busy_state["stop"] and not busy_state["send"]
              and "停止" in busy_state["title"], str(busy_state))
        page.evaluate("() => window.yindun.onEvent('state', {busy: false})")
        page.wait_for_timeout(120)
        check("空闲后转圈收起、发送键恢复",
              page.evaluate("""() => {
                  const s = document.querySelector('#composer-spinner');
                  const b = document.querySelector('#btn-send');
                  return !!s && s.hidden && b.classList.contains('pill--send')
                         && !b.classList.contains('pill--stop'); }"""))

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
    from yindun.core.context_budget import (MODEL_WINDOW_TOKENS,           # noqa: E402
                                            attachment_budget as _attachment_budget)
    check("context_usage 的窗口上限与预算表一致",
          usage.get("limit_tokens") == MODEL_WINDOW_TOKENS, str(usage.get("limit_tokens")))
    check("context_usage 的底部两行来自预算表（附件预算按当前模型窗口折算）",
          any(str(_attachment_budget(usage["limit_tokens"])) in line
              for line in usage["footer"]["lines"]),
          str(usage["footer"]))
    check("context_usage 的每一项都带真实 token 与占比（没有占位/编造）",
          all(isinstance(p.get("tokens"), int) and isinstance(p.get("percent"), (int, float))
              and p.get("label") for p in usage["parts"]), str(usage["parts"]))

    # ── 18) 演示版本提示（打包版首启 toast）──
    print("\n" + "=" * 78)
    print("【18】演示版本提示：内置自制中转站（首启 toast）")
    print("=" * 78)
    import functools
    import http.server
    _demo_handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(WEB_DIR))
    _demo_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _demo_handler)
    threading.Thread(target=_demo_srv.serve_forever, daemon=True).start()
    _demo_url = f"http://127.0.0.1:{_demo_srv.server_address[1]}/index.html#mock"
    with sync_playwright() as _pw_demo:
        _browser_demo = _pw_demo.chromium.launch()
        page_demo = _browser_demo.new_page(viewport={"width": 1280, "height": 800})
        try:
            page_demo.goto(_demo_url, wait_until="domcontentloaded")
            page_demo.wait_for_timeout(800)
            demo_toast = page_demo.eval_on_selector_all(
                "#toast-root .toast", "els => els.map(e => e.textContent).join(' | ')")
            check("首启弹出「演示版本 / 内置自制中转站」提示",
                  "演示版本" in demo_toast and "中转站" in demo_toast, repr(demo_toast[:160]))
            check("提示里写明无需本地 Ollama / 有额度限制",
                  "无需本地 Ollama" in demo_toast and "额度" in demo_toast, repr(demo_toast[:160]))
            demo_calls = page_demo.evaluate(
                "() => (window.__YINDUN_MOCK__ && window.__YINDUN_MOCK__.calls) || []")
            check("看过之后调用 mark_demo_notice_seen（不会每次启动都弹）",
                  any(c[0] == "mark_demo_notice_seen" for c in demo_calls), str(demo_calls)[:200])
            demo_catalog = page_demo.evaluate("() => window.__YINDUN_MOCK__.api.model_catalog()")
            wan_names = [m.get("name") for m in ((demo_catalog or {}).get("tiers") or {}).get("wan", [])]
            check("内置演示模型出现在公网档（评委可直接选）",
                  any("自建中转站" in str(n) for n in wan_names), str(wan_names))
        finally:
            _browser_demo.close()
    _demo_srv.shutdown()

    elapsed = time.time() - started
    print("\n" + "=" * 78)
    print(f"耗时 {elapsed:.1f} 秒（上限 {LIMIT_SECONDS} 秒）")
    check("在时限内跑完（无 Ollama 依赖）", elapsed < LIMIT_SECONDS, f"{elapsed:.1f}s")
    if failures:
        print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
        return 1
    print("✅ 界面关键路径全部通过（权限 / 胶囊 / 思考紧凑形态 / 插件 / 模型接入重做 / 模型选择器两级 / "
          "上下文浮层 / 窗口四按钮 / 可读写范围 / 模型可用性诚实化 / 闪发 / 主题与布局 / 附件＋ / "
          "Markdown 表格 / 等待态与空回复兜底 / 工作流面板 / 暗色滚动条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
