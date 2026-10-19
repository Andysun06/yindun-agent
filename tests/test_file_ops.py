# -*- coding: utf-8 -*-
"""隐盾 · 文件管理工具与「可读写范围」默认值回归（V3.6.8）

负责人实测反馈（原话）：“我让他做点啥都拒绝……你想用这个智能体移动一些文件、批量修改
一些文件名文件后缀，然后你的桌面智能体告诉你做不了，那你要这个智能体干嘛，没用啊”。

两个根因，本套件逐条钉住：

  【A】**默认可读写范围太小**：旧实现的默认范围只有应用根目录，于是“数一下桌面有多少文件”
      “在桌面创建 txt”全被判成越界（'变更前确认'档逐个弹窗、'计划模式'直接拒绝）。
      现在默认 = 应用根目录 + **用户桌面 / 文档 / 下载**（`sandbox_roots()`），
      用户显式配置 `sandbox_dir` 时只认它（不叠加）。
  【B】**根本没有移动/改名/复制的工具**：模型只能用“读出来 + 新建 + 删掉”硬拼，
      批量改后缀更是无从下手。新增 `move_local_file` / `copy_local_file` /
      `rename_local_file` / `batch_rename_local_files` / `make_local_dir`。

断言口径与前人一致：**看磁盘真值**（文件真的动了、名字真的变了、失败时一个字都没改），
不看工具返回的“成功”字样。

用法：python tests/test_file_ops.py   （退出码 0 = 全部通过）
沙箱：全部操作在临时目录里进行；范围用文件内的沙箱根覆盖（不碰真实桌面/文档）。
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yindun.core import file_tools as FT                      # noqa: E402
from yindun.core import permission as PERM                    # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def call(tool, **kwargs):
    """按 LangChain 工具的真实调用路径执行（与 worker 的 _execute_tool 同一条路）。"""
    return str(tool.invoke(kwargs))


TMP = Path(tempfile.mkdtemp(prefix="yindun_fileops_"))
WORK = TMP / "workdir"          # 中性名：不往审计链里塞"xx区"这种像地址的目录名
DESK = TMP / "桌面"
OUTSIDE = TMP / "范围外"
for directory in (WORK, DESK, OUTSIDE):
    directory.mkdir(parents=True, exist_ok=True)

# ── 范围覆盖：把「可读写范围」钉到临时目录上（默认集合里放进 WORK 与 DESK 的模拟桌面）──
_real_working_dirs = PERM.user_working_dirs
PERM.user_working_dirs = lambda: [str(DESK)]
PERM.register_sandbox_settings_source(lambda: str(WORK))
os.environ[PERM.PERMISSION_ENV] = "full"      # 越界在 full 档自动放行，专测"写得进去"

print("=" * 78)
print("【A】默认可读写范围：桌面/文档/下载 进范围，显式配置时只认配置的目录")
print("=" * 78)
PERM.register_sandbox_settings_source(lambda: "")
roots = [Path(r) for r in PERM.sandbox_roots()]
check("默认范围包含应用根目录", any(r == Path(ROOT) for r in roots), str(roots))
check("★ 默认范围包含用户桌面（负责人演示的第一场景）",
      any(r == DESK for r in roots), str(roots))
check("桌面下的文件判定为范围内",
      PERM.is_within_sandbox(str(DESK / "随便.txt")))
check("范围外的目录判定为范围外", not PERM.is_within_sandbox(str(OUTSIDE / "x.txt")))
check("范围文本列出全部根（提示词/话术用）",
      str(DESK) in PERM.sandbox_scope_text() and str(WORK) not in PERM.sandbox_scope_text())

PERM.register_sandbox_settings_source(lambda: str(WORK))
check("显式配置 sandbox_dir 后**只认它**（桌面不再进范围）",
      [r for r in PERM.sandbox_roots()] == [str(WORK)], str(PERM.sandbox_roots()))
check("显式配置后桌面属于范围外", not PERM.is_within_sandbox(str(DESK / "a.txt")))
check("工具层的 _enforce_sandbox 与策略层同口径",
      FT._enforce_sandbox(str(WORK / "a.txt"))[1] is True
      and FT._enforce_sandbox(str(DESK / "a.txt"))[1] is False)

print("\n" + "=" * 78)
print("【B1】rename_local_file：就地改名（磁盘真值）")
print("=" * 78)
(WORK / "报告_v1.docx").write_text("hello", encoding="utf-8")
res = call(FT.rename_local_file, filename="报告_v1.docx", new_name="报告_final.docx")
check("返回里如实写了旧名 → 新名", "报告_v1.docx" in res and "报告_final.docx" in res, res)
check("新名字在磁盘上真的存在", (WORK / "报告_final.docx").exists())
check("旧名字在磁盘上真的没了", not (WORK / "报告_v1.docx").exists())
check("内容没有被改动",
      (WORK / "报告_final.docx").read_text(encoding="utf-8") == "hello")

res = call(FT.rename_local_file, filename="报告_final.docx", new_name="a\\b.docx")
check("带路径分隔符的 new_name 被拒绝（防穿越）", "只能是**文件名**" in res, res)
check("被拒绝时磁盘未变", (WORK / "报告_final.docx").exists())

(WORK / "占用.txt").write_text("x", encoding="utf-8")
(WORK / "已存在.txt").write_text("y", encoding="utf-8")
res = call(FT.rename_local_file, filename="占用.txt", new_name="已存在.txt")
check("目标名已存在时拒绝覆盖", "目标名已存在" in res, res)
check("拒绝后两个文件都还在",
      (WORK / "占用.txt").exists() and (WORK / "已存在.txt").read_text(encoding="utf-8") == "y")

res = call(FT.rename_local_file, filename="不存在.txt", new_name="x.txt")
check("源不存在时如实报错并给出下一步（先 list_local_files）",
      "不存在" in res and "list_local_files" in res, res)

print("\n" + "=" * 78)
print("【B2】move_local_file：单个改名式移动 / 批量移动 / 目标占用 / 父目录缺失")
print("=" * 78)
ARCHIVE = WORK / "存档"
ARCHIVE.mkdir(exist_ok=True)
(WORK / "b1.txt").write_text("1", encoding="utf-8")
(WORK / "b2.txt").write_text("2", encoding="utf-8")
res = call(FT.move_local_file, source=str(WORK / "b1.txt"), target=str(ARCHIVE))
check("单文件移动到既有目录：保留原名", (ARCHIVE / "b1.txt").exists() and "b1.txt" in res, res)
check("源位置已清空", not (WORK / "b1.txt").exists())

res = call(FT.move_local_file, source=str(ARCHIVE / "b1.txt"), target=str(WORK / "改名后.txt"))
check("单个源 + 完整目标路径 = 移动并改名",
      (WORK / "改名后.txt").exists() and not (ARCHIVE / "b1.txt").exists(), res)

(WORK / "m1.txt").write_text("a", encoding="utf-8")
(WORK / "m2.txt").write_text("b", encoding="utf-8")
res = call(FT.move_local_file, source=[str(WORK / "m1.txt"), str(WORK / "m2.txt")],
           target=str(ARCHIVE))
check("批量移动：两个文件都进了目标目录",
      (ARCHIVE / "m1.txt").exists() and (ARCHIVE / "m2.txt").exists(), res)
check("批量移动的结果里逐条列了去向", "m1.txt" in res and "m2.txt" in res, res)

res = call(FT.move_local_file, source=[str(WORK / "改名后.txt"), str(WORK / "占用.txt")],
           target=str(WORK / "不存在.txt"))
check("多源 + target 不是目录 → 报错并说清原因（要已存在的目录）",
      "已存在的目录" in res and "make_local_dir" in res, res)

(WORK / "t1.txt").write_text("1", encoding="utf-8")
(WORK / "t2.txt").write_text("2", encoding="utf-8")
res = call(FT.move_local_file, source=str(WORK / "t1.txt"), target=str(WORK / "t2.txt"))
check("目标已存在且未授权覆盖 → 拒绝", "目标已存在" in res, res)
check("拒绝时目标文件内容没被改掉",
      (WORK / "t2.txt").read_text(encoding="utf-8") == "2")
res = call(FT.move_local_file, source=str(WORK / "t1.txt"), target=str(WORK / "t2.txt"),
           overwrite=True)
check("显式 overwrite=true 才覆盖，且结果里说明覆盖了谁",
      "覆盖" in res and (WORK / "t2.txt").read_text(encoding="utf-8") == "1"
      and not (WORK / "t1.txt").exists(), res)

res = call(FT.move_local_file, source=str(WORK / "t2.txt"), target=str(WORK / "没有的目录" / "x.txt"))
check("目标父目录不存在 → 拒绝并给出可执行下一步（先建目录）",
      "目录不存在" in res and "make_local_dir" in res, res)
check("拒绝时源文件仍在原处", (WORK / "t2.txt").exists())

nested = WORK / "外层"
(nested / "内层").mkdir(parents=True, exist_ok=True)
res = call(FT.move_local_file, source=str(nested), target=str(nested / "内层" / "外层"))
check("拒绝把目录移动到自己的子目录里", "子目录" in res, res)

print("\n" + "=" * 78)
print("【B3】copy_local_file：复制（源保持不动）")
print("=" * 78)
(WORK / "src.txt").write_text("原文", encoding="utf-8")
res = call(FT.copy_local_file, source=str(WORK / "src.txt"), target=str(ARCHIVE))
check("复制到目录：副本存在", (ARCHIVE / "src.txt").exists() and "src.txt" in res, res)
check("★ 复制后源文件仍在（这是与移动的关键差别）",
      (WORK / "src.txt").read_text(encoding="utf-8") == "原文")
res = call(FT.copy_local_file, source=str(WORK / "src.txt"), target=str(ARCHIVE))
check("副本已存在、未授权覆盖 → 拒绝", "目标已存在" in res, res)

print("\n" + "=" * 78)
print("【B4】batch_rename_local_files：批量改后缀（负责人点名的场景）")
print("=" * 78)
BATCH = WORK / "批量"
BATCH.mkdir(exist_ok=True)
for name in ("pic1.jpeg", "pic2.jpeg", "pic3.jpeg", "note.txt"):
    (BATCH / name).write_text("x", encoding="utf-8")
res = call(FT.batch_rename_local_files, target_directory=str(BATCH),
           match=".jpeg", replace=".jpg")
check("三个 jpeg 全部改成 jpg",
      all((BATCH / f"pic{i}.jpg").exists() for i in (1, 2, 3)), res)
check("旧后缀文件一个不剩",
      not any((BATCH / f"pic{i}.jpeg").exists() for i in (1, 2, 3)))
check("结果里逐条列出 旧名 → 新名", res.count("→") >= 3, res)
check("★ 不相关的文件保持不动（note.txt）", (BATCH / "note.txt").exists())

res = call(FT.batch_rename_local_files, target_directory=str(BATCH), match=".png", replace=".jpg")
check("没有匹配时如实说明（不报成功）", "没有匹配到" in res, res)

for name in ("c1.dat", "c2.dat"):
    (BATCH / name).write_text("x", encoding="utf-8")
(WORK / "c3.dat").write_text("x", encoding="utf-8")
res = call(FT.batch_rename_local_files, target_directory=str(BATCH),
           match=".dat", replace=".txt")
check("批量改到另一种常见扩展名（.dat → .txt）",
      (BATCH / "c1.txt").exists() and (BATCH / "c2.txt").exists(), res)

COLLIDE = WORK / "撞车"
COLLIDE.mkdir(exist_ok=True)
for name in ("a.txt", "b.md", "b.txt"):          # b.md → b.txt 会与已存在的 b.txt 撞车
    (COLLIDE / name).write_text("x", encoding="utf-8")
res = call(FT.batch_rename_local_files, target_directory=str(COLLIDE), match=".md", replace=".txt")
check("★ 真的会撞车时：整批拒绝并列出冲突对",
      "已整批拒绝" in res and "b.md → b.txt" in res, res)
check("撞车时磁盘一个字都没改",
      (COLLIDE / "b.md").exists() and (COLLIDE / "b.txt").exists()
      and (COLLIDE / "a.txt").exists())

res = call(FT.batch_rename_local_files, target_directory=str(BATCH), match="", replace="x")
check("match 为空 → 拒绝并解释 match 是什么", "match 不能为空" in res, res)

res = call(FT.batch_rename_local_files, target_directory=str(BATCH),
           match="pic", replace="照片", file_pattern="*.jpg")
check("file_pattern 生效：只处理 *.jpg", "照片" in res and (BATCH / "照片1.jpg").exists(), res)

print("\n" + "=" * 78)
print("【B5】make_local_dir：建目录（含多级），已存在时如实说明")
print("=" * 78)
res = call(FT.make_local_dir, path="新建目录")
check("相对名称建在当前工作区", (WORK / "新建目录").is_dir() and "已创建目录" in res, res)
res = call(FT.make_local_dir, path=str(WORK / "多层" / "子层"))
check("绝对路径可一次建多级", (WORK / "多层" / "子层").is_dir(), res)
res = call(FT.make_local_dir, path="新建目录")
check("已存在时如实说明（不报假成功）", "已存在" in res, res)

print("\n" + "=" * 78)
print("【C】越界与档位：范围外的移动按档位判定，两端都要判")
print("=" * 78)
(WORK / "越界源.txt").write_text("x", encoding="utf-8")
os.environ[PERM.PERMISSION_ENV] = "plan"
res = call(FT.move_local_file, source=str(WORK / "越界源.txt"), target=str(OUTSIDE / "x.txt"))
check("计划模式：写类操作被 gate_reason 拦下（连越界判定都到不了）",
      "计划模式" in res, res)
os.environ[PERM.PERMISSION_ENV] = "ask"
res = call(FT.move_local_file, source=str(WORK / "越界源.txt"), target=str(OUTSIDE / "x.txt"))
check("★ 确认档：目标越界时**不给令牌就拒绝**（令牌只能由宿主策略层授予）",
      "可读写范围之外" in res, res)
check("拒绝时源文件没被动", (WORK / "越界源.txt").exists())
check("拒绝话术里列出了当前范围的全部根", str(WORK) in res, res)

os.environ[PERM.PERMISSION_ENV] = "full"
FT.grant_outside_access(str(OUTSIDE))
try:
    res = call(FT.move_local_file, source=str(WORK / "越界源.txt"), target=str(OUTSIDE / "x.txt"))
    check("完全访问 + 宿主令牌：越界移动放行", (OUTSIDE / "x.txt").exists(), res)
    check("放行时结果里带「在可读写范围之外」标注", "可读写范围之外" in res, res)
finally:
    FT.revoke_outside_grants()
    os.environ[PERM.PERMISSION_ENV] = "full"

os.environ[PERM.PERMISSION_ENV] = "plan"
res = call(FT.batch_rename_local_files, target_directory=str(BATCH), match="照片", replace="pic")
check("计划模式：批量改名被拦（不产生任何变更）", "计划模式" in res, res)

print("\n" + "=" * 78)
print("【D】工具面接线：模型真的能调、快速模式会拦、用户路径覆盖不会打乱参数")
print("=" * 78)
import inspect                                                        # noqa: E402
from yindun.app.agent_service import build_tools                     # noqa: E402
from yindun.worker.agent_worker import Worker, _SYSTEM_PROMPT        # noqa: E402

os.environ[PERM.PERMISSION_ENV] = "full"      # D 段要做真实改名，切回"完全访问"
PERM.register_sandbox_settings_source(lambda: "")   # 回到默认集合（含模拟桌面）
PERM.user_working_dirs = lambda: [str(WORK), str(DESK)]

names = {getattr(tool, "name", "") for tool in build_tools()}
for tool_name in ("make_local_dir", "move_local_file", "copy_local_file",
                  "rename_local_file", "batch_rename_local_files"):
    check(f"build_tools() 里有 {tool_name}（没进清单=模型看不见）", tool_name in names)
for tool_name in ("move_local_file", "batch_rename_local_files", "make_local_dir"):
    check(f"系统提示词里写了 {tool_name}（模型知道有这个能力）", tool_name in _SYSTEM_PROMPT)

check("快速模式把文件管理族算作写操作（快速档不动盘）",
      '"batch_rename_local_files", "make_local_dir"' in inspect.getsource(Worker))
display = Worker._map_tool_display_name(Worker, "batch_rename_local_files")
check("审批/状态里显示中文名而不是裸工具名", display == "批量重命名", display)
check("移动/复制的审批话术也是中文",
      Worker._map_tool_display_name(Worker, "move_local_file") == "移动文件"
      and Worker._map_tool_display_name(Worker, "make_local_dir") == "新建目录")

_worker = Worker.__new__(Worker)                 # 只为验证分发路径，不做真实推理
_worker.tools_map = {getattr(t, "name", ""): t for t in build_tools()}
_worker.sandbox_path = str(WORK)
(WORK / "dispatch_src.txt").write_text("x", encoding="utf-8")
_worker.user_input = f"把 {WORK / 'dispatch_src.txt'} 改名成 dispatch_dst.txt"
res = _worker._execute_tool("rename_local_file",
                            {"filename": "dispatch_src.txt", "new_name": "dispatch_dst.txt"})
check("★ 用户输入含绝对路径时（worker 会强制注入 target_directory）改名仍正确落到目标目录",
      (WORK / "dispatch_dst.txt").exists(), res)
(DESK / "桌面文件.txt").write_text("x", encoding="utf-8")
_worker.user_input = f"看一下 {DESK / '桌面文件.txt'}"
res = _worker._execute_tool("batch_rename_local_files",
                            {"target_directory": str(DESK), "match": "桌面", "replace": "desk"})
check("批量改名经 worker 分发后生效（桌面文件.txt → desk文件.txt）",
      (DESK / "desk文件.txt").exists(), res)

# 收尾：清理临时沙箱（不碰真实桌面/文档/仓库）
PERM.user_working_dirs = _real_working_dirs
PERM.register_sandbox_settings_source(None)
os.environ.pop(PERM.PERMISSION_ENV, None)
shutil.rmtree(TMP, ignore_errors=True)

print("\n" + "=" * 78)
if failures:
    print(f"❌ 文件管理工具回归失败：{len(failures)} 项")
    for item in failures:
        print("   ·", item)
    sys.exit(1)
print("✅ 文件管理工具回归通过（默认范围含桌面 / 移动 / 复制 / 改名 / 批量改后缀 / 建目录 / 越界档位）")
sys.exit(0)
