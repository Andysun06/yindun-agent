# 视频素材总目录

本目录集中存放 **4:55 产品介绍视频** 所需的全部素材。成片脚本、口播与时间轴见
[`docs/dev/项目介绍视频脚本.md`](../../docs/dev/项目介绍视频脚本.md)。

> `MP4原片/` 中的视频被 `.gitignore` 的 `*.mp4` 排除，**不入库**；换机器或交给剪辑同学时，
> 请把整个 `assets/视频素材/` 一起拷贝。图片素材是副本，原图仍保留在 `docs/` 与 `assets/icons/`。

## 目录

```text
assets/视频素材/
├── README.md
├── MP4原片/                     # 8 段原始录屏（1728×1080）
│   ├── 01_脱敏对话.mp4          # 10.1s / 60fps
│   ├── 02_审批放行.mp4          #  9.1s / 60fps
│   ├── 03_审计滚动.mp4          #  9.0s / 30fps
│   ├── 04_插件与扩展.mp4        # 24.1s / 60fps
│   ├── 05_脱敏知识库.mp4        # 22.1s / 60fps
│   ├── 06_网关占位符.mp4        #  9.7s / 60fps
│   ├── 07_用途红线拒绝.mp4      # 10.3s / 60fps
│   └── 08_laya决策模型.mp4      # 17.3s / 60fps
└── 图片素材/
    ├── Logo/
    │   ├── yindun-client.png    # 客户端盾牌图标（S0/S9）
    │   └── yindun-control.png   # 控制端图标（S7B 备用）
    ├── 界面截图/                 # docs/screenshots 全量副本
    │   ├── 00_架构图.png
    │   ├── 01_会话选择主界面.png
    │   ├── 02_隐私对话演示.png
    │   ├── 03_审计面板.png
    │   ├── 04_设置面板.png
    │   ├── 05b_审批弹窗_含待审内容.png
    │   ├── 05c_审批弹窗_研判中.png
    │   ├── 06_插件设置.png
    │   ├── 07_命令拦截拒绝.png
    │   ├── 08_知识库入库.png
    │   ├── 09_模型选择.png
    │   ├── 10_控制台_态势总览.png
    │   └── 11_控制台_策略下发.png
    ├── 控制端/
    │   ├── control_overview_card.png
    │   ├── control_events_card.png
    │   ├── control_policy_card.png
    │   └── control_stats_card.png
    └── 证据图/
        ├── audit_tamper.png
        └── audit_tamper_alert.png
```

## 用在哪些场景

| 场景 | 用什么 |
|---|---|
| S0 开场 / S9 收尾 | `图片素材/Logo/yindun-client.png` |
| S1 四个泄露面 / S7 数据卡 / S8 场景 | HTML/CSS + SVG，不需要额外图片 |
| S2 架构 | 参考 `图片素材/界面截图/00_架构图.png`，按 PPT P2 重画 |
| S3 脱敏对话 | `MP4原片/01_脱敏对话.mp4` |
| S4 审批 | `MP4原片/02_审批放行.mp4` + `05b/05c` 静帧备用 |
| S5 审计 | `MP4原片/03_审计滚动.mp4` + `证据图/audit_tamper_alert.png` |
| S6 知识库 + 插件 | `MP4原片/05_脱敏知识库.mp4` + `04_插件与扩展.mp4` |
| 创新点（按讲稿取舍） | `06_网关占位符.mp4` / `07_用途红线拒绝.mp4` / `08_laya决策模型.mp4` |
| S7B 控制端四宫格 | `图片素材/控制端/control_*_card.png` |

## GIF 预览

8 段正式 GIF 与 PPT 内嵌版本一致，放在
[`docs/formal_assets/`](../../docs/formal_assets/)：

`01_脱敏对话.gif` … `08_laya决策模型.gif`（1280×800 / 30fps）。

GIF 只用于快速预览与 PPT；**成片请直接使用 `MP4原片/`，不要把 GIF 放大**。

## 维护约定

- 重新录屏后：MP4 按同名覆盖 `MP4原片/`；GIF 同步覆盖 `docs/formal_assets/01–08_*.gif`；
  PPT 只替换媒体流（不动版式），详见 `docs/dev/开发记忆.md` 的 2026-10-25 条目。
- `图片素材/` 是 `docs/screenshots/`、`docs/formal_assets/control_*_card.png` 和
  `assets/icons/*.png` 的副本；上游更新后重新复制，避免两套图源各自漂移。
