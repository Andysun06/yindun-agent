# -*- coding: utf-8 -*-
"""隐盾 · 应用服务层（与界面无关）

设计目标（视图层重构）：
- 把原先堆在 `gui/main_window.py` 里的业务逻辑（配置、会话、算力、工具、推理编排）
  抽到本层，使**任何界面**（当前 PySide6 / 目标 Web）都能复用同一套逻辑；
- 本层**不得导入任何界面框架**（无 PySide6/Qt、无 pywebview）；
- 对界面只暴露：数据（dict/list）+ 事件回调。

模块划分：
- `settings_store`  ：global_config.json 读写（含自定义模型密钥的加解密）
- `session_store`   ：会话持久化（消息/附件/跨轮脱敏映射，内容加密落盘）
- `llm_factory`     ：算力构建（Ollama / OpenAI 兼容）+ 模型探测
- `agent_service`   ：推理编排（创建 Worker、转发过程事件、审批、取消、落库）
- `webview_app`     ：pywebview 承载的 Web 界面（js_api 桥 + 事件推送）
"""

__all__ = ["settings_store", "session_store", "llm_factory", "agent_service"]
