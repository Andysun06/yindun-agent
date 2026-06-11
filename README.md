# 🛡️ 隐盾安全智能体 (Yindun Security Agent) - V2.5.1

欢迎来到 **隐盾安全智能体 (V2.5.1)** 官方代码库。

本应用是一款常驻系统桌面最上层的原生半透明悬浮智能体客户端。项目经历多轮工业级深度解耦重构，前端核心视舱（大厅、状态栏、控制台、设置页、气泡、弹窗）均已实现 100% 原子组件化封装。V2.2.0 新增基于 LangChain ChatMessageHistory 的上下文记忆管理，支持 max_tokens 阈值控制与自动摘要压缩。

---

## 🎨 核心功能特性

1. **🔒 内存级数据隐私网关**：内置物理脱敏隔离网关（`PrivacyEngine`），在涉密文本砸向本地算力底座前，在内存中就地执行实体模糊化与数据匿名化，并在汇报给人类时安全还原明文。
2. **🧠 纯本地多模态算力 + 外部算力扩展**：全链路采用纯离线私有化部署的 `Ollama` 算力集群，同时支持 [OpenAI 兼容流](https://platform.openai.com/docs/api-reference) 外部算力资产接入（通过设置面板动态注册 API Key）。通过 `NO_PROXY` 管道确保全隔离运行。
3. **📂 离线文档全自动挂载**：支持离线 PDF、Word (docx)、Excel (xlsx/xls)、TXT、MD、CSV 等多种机密办公文档的跨平台编码自适应提取。
4. **🚨 熔断级越界操作拦截**：当智能体企图调动物理机械臂工具并切入非默认工作区或系统核心敏感目录时，系统将强行触发独立的二次确认拦截弹窗，安全阻塞后台写盘线程，强行等待人类安全员二次审批。
5. **🧠 智能上下文记忆管理** *（V2.2.0 新增）*：
   - 基于 LangChain `BaseChatMessageHistory` 标准接口实现
   - 内置 `max_tokens` 阈值（默认 5000），超出后自动触发 LLM 摘要压缩
   - 摘要保留早期对话核心信息，最近消息完整保留
   - 支持多会话隔离，会话记录持久化到 `chat_sessions.json`
6. **💎 天花板级原生 UI/UX 体验**：
   - 强行注入无损硬件级高分屏缩放与抗锯齿策略
   - 纯原生弹性聊天气泡，锁定 **75% 最大宽度自适应换行算法**
   - 物理级圆角卡片质感，高斯半透明柔和物理阴影
   - 窗口不透明度 100%~50% 横向滑动条控制
   - 响应式双轨布局：宽屏侧边栏 + 窄屏纯对话模式
   - 极简闪发折叠模式：鼠标悬浮展开

---

## 🚀 快速开始

### 环境准备

```bash
# Windows：双击运行
准备环境.bat
```

脚本将自动检测 Python 版本、创建独立虚拟环境 `secure_env`、安装全部依赖。

### 启动应用

```bash
# Windows：双击运行
启动.bat

# 或命令行
pip install -r requirements.txt
python run.py
```

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
├── global_config.json          # 运行时配置文件
├── chat_sessions.json          # 会话记录持久化
│
└── yindun/                     # 📦 主包
    ├── __init__.py             # 包标识与版本号
    ├── main.py                 # 应用入口（初始化环境 → 启动主窗口）
    │
    ├── core/                   # 🧠 核心业务逻辑
    │   ├── __init__.py
    │   ├── file_tools.py       # 文件 CRUD 工具（LangChain Tool）
    │   ├── privacy_engine.py   # 隐私脱敏引擎
    │   └── memory_manager.py   # 上下文记忆管理器（ChatMessageHistory + 摘要）
    │
    ├── gui/                    # �️ 图形界面组件
    │   ├── __init__.py
    │   ├── main_window.py      # 主窗口框架（骨架拼接 + 信号路由）
    │   ├── styles.py           # 全局 QSS 样式表
    │   ├── chat_bubble.py      # 原生弹性聊天气泡
    │   ├── chat_display.py     # 聊天显示区域（滚动布局）
    │   ├── control_dock.py     # 底部控制栏（输入 + 发送）
    │   ├── settings_panel.py   # 设置面板（模型/安全/界面）
    │   ├── custom_model_dialog.py  # 外部模型 API 配置弹窗
    │   ├── session_selector.py     # 会话选择页
    │   ├── confirm_dialog.py       # 高危操作审批弹窗
    │   └── status_bar.py           # 动画状态栏
    │
    ├── worker/                 # ⚙️ 后台工作线程
    │   ├── __init__.py
    │   └── agent_worker.py     # Agent 推理线程（脱敏 → 上下文 → invoke → 工具调度）
    │
    └── utils/                  # � 工具集
        ├── __init__.py
        └── document_parser.py  # 离线文档解析（PDF/Word/Excel/TXT）
```

---

## 📌 版本命名规范 (SemVer)

全组统一采用 `X.Y.Z` 三段数字命名法：

| 版本位 | 名称 | 触发场景 |
| :--- | :--- | :--- |
| **X** — Major | 主版本号 | 不兼容的大重构。如从散放目录重构为 `yindun` 命名空间包 |
| **Y** — Minor | 次版本号 | 新增功能。如新增上下文摘要机制、外部算力支持 |
| **Z** — Patch | 修订版本号 | 纯 Bug 修复 / 样式调整 |

> 日常修复 Bug 时递增 Z 即可；新增功能后递增 Y 并将 Z 归零；重大架构变更由队长拍板递增 X。

---

## 🔧 依赖

| 包 | 用途 |
|---|------|
| `PySide6 >= 6.5` | Qt 图形界面引擎 |
| `langchain-ollama` | 本地 Ollama 算力连接 |
| `langchain-openai` | 外部 OpenAI 兼容流算力 |
| `langchain-core` | 消息管道与 ChatMessageHistory |
| `PyPDF2` | PDF 文档解析 |
| `python-docx` | Word 文档解析 |
| `openpyxl` | Excel 表格解析 |
| `tiktoken` | Token 计数（摘要阈值计算） |
| `pydantic` | 数据校验（Tool 参数模型） |
