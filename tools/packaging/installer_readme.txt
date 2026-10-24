隐盾安全智能体 V3.6.20 —— 安装须知

一、两种交付形态
  · 标准安装包（本程序，推荐日常使用）：
    安装到当前用户目录 %LOCALAPPDATA%\Programs\Yindun，创建开始菜单与桌面快捷方式，
    可在“设置 → 应用”或开始菜单里卸载；卸载时询问是否保留本地数据。
  · 免安装单文件 exe（评委试用 / 临时机器）：
    从 Release 下载 Yindun-Portable-<版本>.exe，双击即用，不需要安装与管理员权限；
    配置、会话、审计与知识库数据写在 exe 同目录，删掉 exe 与该目录即可清理。

二、首次使用（两种形态相同）
  隐盾需要接入一个“算力底座”大模型，两种方式二选一：
  ① 本地离线（推荐，数据不出网）：
     1. 安装 Ollama：https://ollama.com/download
     2. 命令行执行：ollama pull qwen3.5:4b
        （知识库功能还需要：ollama pull nomic-embed-text）
     3. 启动隐盾，在设置页选择检测到的本地模型即可。
  ② 云端模型：
     在设置页点击“新增外部”，填入 OpenAI 兼容 API 的
     base_url / api_key / model_id（如智谱 GLM-4-Flash）。
     注意：隐私网关开启时，发往云端的内容已自动脱敏为占位符。

三、安全提示
  · 本安装包未做代码签名，Windows Defender / 杀毒软件可能提示；
    选择“仍要运行”或添加信任即可，源码在 GitHub 公开可自查。
  · 本软件为大学生竞赛作品，仅供学习研究使用。

Yindun Team · 2026
