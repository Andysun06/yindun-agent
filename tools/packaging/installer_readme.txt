隐盾安全智能体 V3.3 —— 安装须知

一、安装说明
  · 本软件安装到当前用户目录（%LOCALAPPDATA%\Yindun），无需管理员权限。
  · 卸载：开始菜单 → 隐盾安全智能体 → 卸载，或经控制面板"程序和功能"。

二、首次使用（重要）
  隐盾需要接入一个"算力底座"大模型，两种方式二选一：
  ① 本地离线（推荐，数据不出网）：
     1. 安装 Ollama：https://ollama.com/download
     2. 命令行执行：ollama pull qwen2.5:7b-instruct
        （可选，知识库功能需要：ollama pull nomic-embed-text）
     3. 启动隐盾，在设置页选择检测到的本地模型即可。
  ② 云端模型：
     在设置页点击"新增外部"，填入 OpenAI 兼容 API 的
     base_url / api_key / model_id（如智谱 GLM-4-Flash）。
     注意：隐私网关开启时，发往云端的内容已自动脱敏为占位符。

三、安全提示
  · 未签名的绿色软件可能被杀毒软件/Windows Defender 提示，
    选择"仍要运行"或添加信任即可（源码可自查：仓库公开）。
  · 本软件为大学生竞赛作品，仅供学习研究使用。

Yindun Team · 2026
