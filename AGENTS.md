# AGENTS.md · 隐盾安全智能体

本文件是**给 AI 助手（ZCode / Claude Code 等）的协作约定**。人类协作者请看 [docs/dev/开发记忆.md](docs/dev/开发记忆.md) 与 [README.md](README.md)。

## 最重要的一条

**每次改动，必须同步更新 [docs/dev/开发记忆.md](docs/dev/开发记忆.md)：**
1. 在文末「更新日志」追加一条（date · 版本/主题 · 变更与影响 · 涉及文件）；
2. 更新受影响的章节（版本号 §2、目录结构 §3、安全不变量 §5、已知问题与路线图 §7、常用命令 §9 等）。

其他文档按 [docs/dev/开发记忆.md §11.1](docs/dev/开发记忆.md) 的对照表同步（README / 技术报告 / 使用说明 / PPT / 截图）。

## 硬性约定

- **安全不变量不可破坏**：动手前先读 [docs/dev/开发记忆.md §5](docs/dev/开发记忆.md)。改到隐私引擎 / 文件工具 / 审计 / 审批 / 取消逻辑，必须连带跑对应测试。
- **回归门禁**：改动后跑 `tests/` 下相关套件；隐私/工具/审计相关改动要跑全量（`python tests/...` 逐个执行，任一非 0 即失败）。知识库套件需 Ollama，不在线会自动跳过。
- **改完 bug 必须重建 exe 并同步 Release（负责人明确要求）**：
  1. 重建两个 exe：`python tools/packaging/build_portable.py`、`python tools/packaging/build_control.py`；
  2. 用 **fresh 产物路径** 过一遍 `tools/packaging/verify_archive.py`（正式文件名被运行中的程序锁住时，
     新产物会留在 `dist/YindunPortable.exe` / `dist/YindunControl.exe`，`--all` 报的"与源码不一致"
     指的是那个**旧**文件——先关程序再重跑脚本即可落名，或设 `YINDUN_BUILD_ALLOW_LOCKED=1` 先构建）；
  3. 上传到 Release（资产名用 ASCII，不许中文）：
     `gh release upload v<版本> dist/Yindun-Portable-v<版本>.exe dist/Yindun-Control-v<版本>.exe --clobber`；
     新版本则 `gh release create v<版本> <两个资产> --title "隐盾安全智能体 V<版本>" --notes-file <说明>`。
  ★ 判据不是"构建成功"，而是**归档校验对 fresh 产物报 0 问题**且 **Release 上的资产能下回来**（curl 取 1KB 看 206/200）。
- **提交**：中文提交信息，`主题：要点1；要点2`。先开分支、提交后快进合并到 `main`；**不要 push**（推送由项目负责人决定）——但负责人已明确要求"改完发 Release"，发版所需的 push 与 `gh release` 属其授权范围。
- **架构边界**：`yindun/core/`、`yindun/utils/` 必须保持**与界面无关**（不得引入 PySide6/Qt）。现状（V3.4.0）：`yindun/gui/` 已整体移除，界面只有 Web（pywebview），`core/`、`utils/`、`worker/` 全部零 Qt；需要向外通知就走 `yindun/core/event_bus.py`。
- **插件只能做加法（不可破坏）**：`yindun/plugins/host.py` 的 `HOOK_SPECS` 是唯一钩子清单，`FORBIDDEN_HOOKS` 是永不开放的判定类能力。新增/修改钩子必须同时满足：
  1. 插件不得参与脱敏豁免 / 白名单 / 审批放行 / 审计写入；
  2. 增强型钩子（识别 / 解析 / 模板 / 导出）只能扩大能力范围，返回值必须经 `normalize` 净化；
  3. 内核兜底不可被顶替（内核导出格式保留名、内核原生附件格式优先）；
  4. 能力跟着开关走：启停插件后必须能立刻注册/注销它贡献的能力；
  5. 不允许静默失效：钩子函数缺失要报错、自检要能发现、读不到配置要告警。
  回归门禁：`tests/test_plugins.py` + `tests/test_plugin_capabilities.py`；契约与示例见 `docs/插件开发.md`。
- **不要把运行时状态提交进库**：`chat_sessions.json`、`global_config.json`、`config/`、`audit_logs/`、`workflow_reports/`、`data/`、`plugins_data/`、`*.mp4`、`*.exe` 均已忽略，勿用 `-f` 强加。

## 本仓库的环境陷阱（原 Mimosa 安全门禁 · 已按负责人要求关闭）

**现状**：负责人多次反馈「每次都中断进度」，`~/.zcode/cli/config.json` 里已把
`mimosa@zcode-plugins-official` 置为 `false`（**可逆**：改回 `true` 并重载会话即可恢复扫描）。
需要一次性深度审计时，也可临时调用 `mimosa:mimosa-security-scan` 技能，不必常开。

下面这张表是**被它拦出来的历史经验**——即便门禁关着，也建议照做（这些写法本身就更稳、更可移植）：

| 曾经的坑 | 建议写法 |
|---|---|
| 用 Bash 写 `.py`（heredoc / `python -c open(...,'w')`） | 用编辑器 / Write 工具改文件（顺带留下可复核的 diff） |
| `git mv` 移动 `.py` 文件 | 用一次性脚本，或请人在资源管理器里手动移动 |
| 仓库脚本里出现 `subprocess.*`（即使 `shell=False` + 参数列表） | 不要把"调用外部命令"的脚本写进仓库；改写成文档里的手动命令 |
| `open(path, 'w')` 写文件 | `pathlib.Path(path).write_text(...)`，并注释说明落点为何受控 |
| 测试语料里的假密钥 / 假 JWT 字面量 | 分段拼接生成（见 `tests/privacy_audit_test.py` 顶部做法） |

> 说明：这些约定与 Mimosa 是否开启无关——它们是这个仓库自己的工程习惯，AI 助手请照旧遵守。
