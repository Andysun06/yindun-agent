# -*- coding: utf-8 -*-
"""`python -m yindun.control` 入口 = 启动企业控制面（集中管理服务 + 控制台）。

同时作为 PyInstaller 的入口脚本（tools/yindun_control.spec）：因此这里只用**绝对导入**，
不用相对导入 —— 否则被当作脚本执行（冻结/直接运行）时会因缺少包上下文而失败。

用法：
    python -m yindun.control --port 8787        # 默认只监听 127.0.0.1
    python -m yindun.control --host 0.0.0.0     # 显式对外（必须自行加 TLS 反代）
"""
from yindun.control.server import main

if __name__ == "__main__":
    raise SystemExit(main())
