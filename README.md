# 🛡️ 隐盾安全智能体 (Yindun Security Agent) - V3.3

欢迎来到 **隐盾安全智能体 (V3.3)** 官方代码库。

本应用是一款常驻系统桌面最上层的原生半透明悬浮智能体客户端。项目经历多轮工业级深度解耦重构，前端核心视舱（大厅、状态栏、控制台、设置页、气泡、弹窗）均已实现 100% 原子组件化封装。

**V3.0 重大升级**：全面迁移至 LangChain 原生 Tool Calling 协议，彻底抛弃正则解析与结构化字段提取，实现真正的 ReAct 推理循环。

**V3.3 新增**：全链路审计黑匣子、本地知识库+脱敏RAG、10类实体脱敏引擎、跨轮脱敏还原、摘要二次脱敏、工具调用对配平。

---

## 🎨 核心功能特性

### 🔒 安全与隐私

1. **内存级数据隐私网关**：内置物理脱敏隔离网关（`PrivacyEngine`），10 类敏感实体（手机号/邮箱/身份证/银行卡/IP/金额/API密钥/微信号/地址/人名）识别与匿名化，在内存中就地执行实体模糊化与数据匿名化，并在汇报给人类时安全还原明文。

2. **熔断级越界操作拦截**：当智能体企图调动物理机械臂工具并切入非默认工作区或系统核心敏感目录时，系统将强行触发独立的二次确认拦截弹窗，安全阻塞后台写盘线程，强行等待人类安全员二次审批。

3. **权限分级控制**：支持"完全控制"、"安全只读"、"彻底审计"三级权限模式，灵活适配不同安全场景。

4. **全链路审计黑匣子**（V3.3 新增）：
   - 每轮完整记录工具调用、LLM I/O、数据流转、隐私检测/脱敏/还原全过程
   - SHA-256 哈希链式存储保证日志不可篡改，任何篡改都会被完整性校验立即发现
   - GUI 面板提供统计概览、哈希链可视化、日志筛选，支持导出 JSON/HTML 审计报告
   - 审计只存脱敏预览，不落盘真实敏感数据，隐私安全闭环

### 🧠 智能推理架构（V3.0 核心升级）

4. **原生 Tool Calling 协议**：
   - 基于 LangChain `bind_tools()` 实现真正的工具绑定
   - 模型自主决定工具调用，无需正则解析
   - 支持 `AIMessage.tool_calls` → `ToolMessage` 完整调用链

5. **ReAct 推理循环**：
   - 推理 → 工具调用 → 观察 → 总结 的完整闭环
   - 快速模式：3 轮推理，秒级响应
   - 深度模式：可配置思考深度（1-10），最多 20 轮推理

6. **思考深度控制** *（V3.1 新增）*：
   - 设置面板滑动条调节（1-10，默认 3）
   - 动态影响最大推理轮次（depth × 2）
   - 最低思考轮数约束（max(2, depth × 0.8)），确保深度分析不过早退出

7. **即时取消响应** *（V3.1.4 新增）*：
   - 线程池包装 `llm.invoke()`，主线程定期检查取消标志
   - 响应时间从 10-60 秒缩短至 **最多 0.5 秒**
   - 用户点击取消后立即中断推理

### 📂 文件与项目管理

8. **项目智能分析**：
   - 自动识别项目类型（Node.js / Python / Java / C# 等）
   - 递归扫描文件结构，提取关键配置文件
   - 代码文件智能采样，返回核心实现摘要
   - **强制工具调用机制**：检测到分析请求时直接执行工具，不依赖模型决策

9. **离线文档全自动挂载**：支持离线 PDF、Word (docx)、Excel (xlsx/xls)、TXT、MD、CSV 等多种机密办公文档的跨平台编码自适应提取。

10. **智能上下文记忆管理**：
    - 基于 LangChain `BaseChatMessageHistory` 标准接口实现
    - 内置 `max_tokens` 阈值（默认 5000），超出后自动触发 LLM 摘要压缩
    - 摘要保留早期对话核心信息，最近消息完整保留
    - 支持多会话隔离，会话记录持久化到 `chat_sessions.json`
    - **跨轮脱敏还原**（V3.3）：脱敏映射表跨会话持久化，历史轮次的占位符在后续对话中也能正确还原，杜绝"裸占位符"泄露给用户
    - **摘要二次脱敏**（V3.3）：摘要生成后再过一遍脱敏引擎，防止真实敏感值被复制进摘要并落盘
    - **工具调用对配平**（V3.3）：上下文裁剪时自动补全 `AIMessage(tool_calls)` ↔ `ToolMessage` 配对，避免模型收到不完整工具序列报错

### 📚 本地知识库 + 脱敏 RAG（V3.3 新增）

11. **本地知识库**：
    - 基于 Chroma 向量库 + Ollama 本地 embedding（nomic-embed-text），全程无云端依赖
    - 支持多格式文档入库（PDF/Word/Excel/TXT/MD），切块、向量化、语义检索
    - GUI 设置面板可视化管理：添加/删除文档、进度条、状态检测

12. **入库即脱敏的 RAG 管线**：
    - 文档在向量化前逐块脱敏，**向量库中只存占位符文本，真实隐私永不落盘**
    - 检索时全局占位符重命名 + `KB_` 前缀隔离，避免与用户输入占位符冲突
    - LLM 只看到脱敏片段，即使接外部 API 也零泄露；回答中的占位符自动还原给用户

### 💎 UI/UX 体验

11. **原生弹性聊天气泡**：
    - 锁定 **75% 最大宽度自适应换行算法**
    - 物理级圆角卡片质感，高斯半透明柔和物理阴影
    - **气泡底部元信息栏**：显示回答模式 + 耗时（如"快速 1m36s"、"思考 4m10s"）

12. **响应式双轨布局**：
    - 宽屏侧边栏 + 窄屏纯对话模式
    - 极简闪发折叠模式：鼠标悬浮展开
    - 窗口不透明度 100%~50% 横向滑动条控制

13. **数据看板**：
    - 实时显示当前模型、对话次数、上下文 Token 估算
    - 状态栏动画指示推理进度

14. **会话管理**：
    - 多会话隔离，独立上下文记忆
    - 会话列表快速切换，历史记录持久化
    - 新建会话对话框，支持自定义会话名称

15. **LaTeX 公式渲染** *（V3.2 新增）*：
    - 气泡内嵌 LaTeX 公式渲染，无需网络依赖
    - 支持 `$...$` 行内公式（如 $E=mc^2$）和 `$$...$$` 独立公式块
    - 基于 matplotlib mathtext 引擎，渲染为高分辨率 PNG 图片嵌入气泡
    - 深色/浅色主题自动适配，渲染失败时优雅降级为纯文本显示

---

## 🚀 快速开始

### 环境准备

```bash
# Windows：双击运行
准备环境.bat
```

脚本将自动检测 Python 版本、创建独立虚拟环境、安装全部依赖。

### 启动应用

```bash
# Windows：双击运行
启动.bat

# 或命令行
pip install -r requirements.txt
python run.py
```

### 首次使用

1. 确保 Ollama 已安装并运行（默认地址 `http://127.0.0.1:11434`）
2. 至少下载一个模型（推荐 `qwen2.5:7b` 或其他支持 Tool Calling 的模型）
3. 启动隐盾后，设置面板会自动检测可用模型

---

## 📂 项目结构

本项目严格遵循 Python 包管理规范，使用 `yindun` 作为顶层命名空间包：

```text
yindun-agent/
├── run.py                      # 🚀 程序入口
├── requirements.txt            # 依赖清单
├── .gitignore
├── 启动.bat                    # 一键启动脚本
├── 准备环境.bat                # 环境部署脚本
├── global_config.json          # 运行时配置文件（模型、隐私、界面设置）
├── chat_sessions.json          # 会话记录持久化
│
    └── yindun/                     # 📦 主包
    ├── __init__.py             # 包标识与版本号
    ├── main.py                 # 应用入口（初始化环境 → 启动主窗口）
    │
    ├── core/                   # 🧠 核心业务逻辑
    │   ├── __init__.py
    │   ├── file_tools.py       # 文件 CRUD 工具集（10 个 LangChain Tool）
    │   │                       #   - 本地文件操作 8 个 + read_attachment_chunk
    │   │                       #   - search_knowledge_base（知识库语义检索）
    │   ├── privacy_engine.py   # 隐私脱敏引擎（20+类实体识别/脱敏/还原）
    │   ├── knowledge_base.py   # 本地知识库 + 脱敏 RAG 引擎
    │   ├── audit_log.py        # 全链路审计黑匣子（HMAC 哈希链防篡改）
    │   ├── memory_manager.py   # 上下文记忆管理器（ChatMessageHistory + 摘要）
    │   ├── policy_manager.py   # 权限策略引擎（deny-by-default + 分级审批）
    │   ├── secret_manager.py   # 本地密钥管理器（Fernet + DPAPI 包裹）
    │   ├── key_manager.py      # 脱敏映射表密钥托管与完整性校验
    │   ├── behavior_analyzer.py# 行为分析器（异常操作检测）
    │   ├── health_scanner.py   # 系统健康扫描
    │   ├── flow_builder.py     # 工作流构建器
    │   └── workflow.py         # 协同工作流编排（合同分析/安全扫描等）
    │
    ├── gui/                    # 🖼️ 图形界面组件
    │   ├── __init__.py
    │   ├── main_window.py      # 主窗口框架（骨架拼接 + 信号路由）
    │   ├── styles.py           # 全局 QSS 样式表（亮色/暗色双主题）
    │   ├── chat_bubble.py      # 原生弹性聊天气泡（含元信息栏）
    │   ├── chat_display.py     # 聊天显示区域（滚动布局 + 宽度自适应）
    │   ├── control_dock.py     # 底部控制栏（输入 + 发送 + 模式切换）
    │   ├── settings_panel.py   # 设置面板（模型/安全/界面/思考深度/知识库管理）
    │   ├── audit_panel.py      # 审计日志查看面板（哈希链/统计/导出）
    │   ├── workflow_panel.py   # 工作流编排面板
    │   ├── data_dashboard.py   # 数据看板（模型/对话次数/Token）
    │   ├── status_bar.py       # 动画状态栏（思考进度 + 取消按钮）
    │   ├── session_selector.py # 会话选择页（历史会话列表）
    │   ├── new_session_dialog.py   # 新建会话对话框
    │   ├── custom_model_dialog.py  # 外部模型 API 配置弹窗
    │   ├── confirm_dialog.py       # 高危操作审批弹窗
    │   └── rounded_scrollbar.py     # 圆角滚动条样式
    │
    ├── worker/                 # ⚙️ 后台工作线程
    │   ├── __init__.py
    │   └── agent_worker.py     # Agent 推理引擎（ReAct 循环 + Tool Calling）
    │                           #   - 路径解析与强制工具调用
    │                           #   - 可中断的 LLM 调用（即时取消响应）
    │                           #   - 最低思考轮数约束
    │
    └── utils/                  # 🔧 工具集
        ├── __init__.py
        ├── document_parser.py  # 离线文档解析（PDF/Word/Excel/TXT/MD/CSV）
        ├── latex_renderer.py   # LaTeX 公式渲染（matplotlib mathtext 引擎）
        └── privacy_scanner.py  # 文档隐私扫描（位置化风险报告）
```

---

## 🔧 技术架构

### ReAct 循环流程

```text
用户输入
    ↓
[路径检测] → 检测到分析请求 → 强制调用 analyze_project
    ↓
[隐私脱敏] → PrivacyEngine.anonymize()
    ↓
[上下文加载] → SummarizableChatHistory.get_context_messages()
    ↓
┌─────────────────────────────────────┐
│  ReAct 循环（max_rounds = depth × 2）│
│                                     │
│  ① llm.invoke(messages)            │
│     ↓                               │
│  ② 检查 tool_calls                  │
│     ├─ 有工具调用 → 执行工具 → ToolMessage
│     └─ 无工具调用 → 检查是否达到最低轮数
│         ├─ 未达到 → 强制继续分析
│         └─ 达到 → 生成总结          │
│                                     │
│  ③ 循环直到模型停止调用工具          │
│     或达到最大轮次                   │
│                                     │
│  ★ 取消检测：每 0.5 秒检查一次       │
└─────────────────────────────────────┘
    ↓
[隐私还原] → PrivacyEngine.deanonymize()
    ↓
[持久化] → 保存完整工具调用链
    ↓
输出给用户
```

### 工具调用链

隐盾支持以下 10 个工具：

| 工具名 | 功能 | 权限要求 |
|--------|------|----------|
| `list_local_files` | 列出目录下的文件和子目录 | 读/列表 |
| `read_local_file` | 读取文件内容 | 读/列表 |
| `analyze_project` | 递归分析项目结构和关键代码 | 读/列表 |
| `search_in_files` | 在文件中搜索文本模式 | 读/列表 |
| `read_attachment_chunk` | 检索长附件文档指定片段 | 读/列表 |
| `search_knowledge_base` | 本地知识库语义检索（脱敏 RAG） | 读/列表 |
| `create_local_file` | 创建新文件并写入内容 | 完全控制 |
| `modify_local_file` | 修改已有文件的内容 | 完全控制 |
| `delete_local_file` | 删除指定文件 | 完全控制 |
| `run_local_command` | 执行系统命令 | 完全控制 |

---

## 📌 版本历史

| 版本 | 发布日期 | 主要变更 |
|------|----------|----------|
| **V3.3.1** | 2026-09 | 安全加固回归：修复审计工具结果参数颠倒回归、审批超时死弹窗（60s→300s+自动关闭）、姓氏表补全当代大姓、中文口令字段脱敏；回答模式持久化；完成红队测试→修复→回归的安全工程闭环 |
| **V3.3** | 2025-07 | 全链路审计黑匣子；本地知识库+脱敏RAG；10类实体脱敏引擎；跨轮脱敏还原；摘要二次脱敏；工具调用对配平 |
| **V3.2** | 2025-06 | LaTeX 公式渲染（$...$ 行内 / $$...$$ 块公式）；气泡内嵌高清 PNG 图片 |
| **V3.1.4** | 2025-06 | 即时取消响应（0.5秒内）；强制工具调用机制；路径解析修复 |
| **V3.1** | 2025-06 | 思考深度滑动条；最低思考轮数约束；气泡元信息栏；数据看板 |
| **V3.0** | 2025-05 | 原生 Tool Calling 协议；ReAct 循环架构；会话管理 |
| **V2.5** | 2025-04 | 外部算力扩展；响应式布局优化 |
| **V2.2** | 2025-03 | 上下文记忆管理；自动摘要压缩 |
| **V2.0** | 2025-02 | 原子组件化重构；隐私脱敏引擎 |

---

## 🔧 依赖

| 包 | 用途 |
|---|------|
| `PySide6 >= 6.5` | Qt 图形界面引擎 |
| `langchain-ollama` | 本地 Ollama 算力连接 + 本地 embedding |
| `langchain-openai` | 外部 OpenAI 兼容流算力 |
| `langchain-core` | 消息管道、Tool Calling、ChatMessageHistory |
| `langchain-chroma` | Chroma 向量库接入 |
| `langchain-text-splitters` | 文档切块（RecursiveCharacterTextSplitter） |
| `chromadb` | 本地向量数据库 |
| `PyPDF2` | PDF 文档解析 |
| `python-docx` | Word 文档解析 |
| `openpyxl` | Excel 表格解析 |
| `tiktoken` | Token 计数（摘要阈值计算） |
| `pydantic` | 数据校验（Tool 参数模型） |
| `rapidocr-onnxruntime` | OCR 识别（Python < 3.13） |

---

## 📝 配置文件

### global_config.json

```json
{
  "model": "qwen2.5:7b",
  "privacy": true,
  "dark_mode": false,
  "topmost": true,
  "thinking_depth": 3,
  "permission": "完全控制 (读/写/列表)",
  "custom_models": {}
}
```

| 字段 | 说明 |
|------|------|
| `model` | 当前使用的模型名称 |
| `privacy` | 是否启用隐私脱敏 |
| `dark_mode` | 是否启用暗色主题 |
| `topmost` | 是否始终置顶 |
| `thinking_depth` | 思考深度（1-10） |
| `permission` | 权限等级 |
| `custom_models` | 自定义外部模型配置 |

---

## 🤝 贡献指南

本项目采用 SemVer 版本命名规范：

| 版本位 | 名称 | 触发场景 |
| :--- | :--- | :--- |
| **X** — Major | 主版本号 | 不兼容的大重构 |
| **Y** — Minor | 次版本号 | 新增功能 |
| **Z** — Patch | 修订版本号 | Bug 修复 / 样式调整 |

---

## 📄 许可证

本项目仅供学习与研究使用。

---

**隐盾安全智能体** — 让本地 AI 更安全、更智能、更可控。