import fnmatch
import os
import re
import shlex
import shutil
import subprocess
import threading
from pathlib import Path
from typing import List, Union

from yindun import APP_ROOT
from yindun.core.permission import gate_reason, sandbox_roots, sandbox_scope_text
from yindun.utils.logger import get_logger
from langchain_core.tools import tool
from pydantic import BaseModel, Field

# 执行容器/沙箱相关的问题要有级别地落盘：打包成 exe 后 print 看不见，
# 而"进程容器不可用，回退普通执行"这类降级必须能在 logs/yindun.log 里查到。
log = get_logger("tools")


# ──────────────────────────────────────────
# 可读写范围（沙箱根）与越界校验
#
# ★ V3.6.8 语义变更（负责人演示需求）：沙箱不再是"绝对墙"，而是"**默认可读写范围**"。
#   历史实现里每个工具开头就 `_enforce_sandbox`，越界一律拒绝（读操作还静默回退），
#   于是"开了完全访问档也什么都做不了"——数桌面文件、在桌面建 txt 全被挡，
#   与设置页四档能力表（完全访问=允许新建/修改/删除）自相矛盾。
#   现在越界操作由**策略层按当前档位**判定：plan 拒绝 / ask 每次人工确认 /
#   auto·full 放行（放行也要留审计 + 在工具结果里标注，不允许静默）。
#
# 本模块保留两道**与档位无关**的硬校验（不得弱化）：
#   ① 最终落点的 realpath+commonpath 校验（_safe_write_path）：防符号链接/junction 逃逸；
#   ② 越界放行必须携带**宿主令牌**（grant_outside_access）：模型无法自授权，
#      令牌只由 worker 在策略层判定通过后发放，且用完即撤（见 revoke_outside_grants）。
# ──────────────────────────────────────────

def sandbox_root() -> str:
    """当前「可读写范围」的绝对路径（解析顺序：设置 sandbox_dir > 环境变量 > 应用根目录）。

    **每次调用现解析**：设置一改立即生效，不会出现"工具默认目录指向新范围、越界判定仍按旧根"
    的错位（V3.6.3 真机 E2E 撞过的缺陷）。唯一实现在 core/permission.py（那里同时管档位矩阵）。
    """
    from yindun.core.permission import sandbox_root as _root
    return _root()


# 兼容旧引用：保留导入时快照（仅供老代码读取），新代码一律用 sandbox_root()
SANDBOX = sandbox_root()


# ── 越界放行令牌（宿主专有，模型不可伪造）──
# 为什么用线程本地令牌而不是工具参数：工具参数会经 LangChain 的 pydantic 校验，
# 多余键被丢弃——但更关键的是**参数是模型可写的**。若把"允许越界"做成参数，
# 模型（或被提示注入的内容）只要传 `_outside_ok=True` 就能给自己放行。
# 令牌只在 worker 线程内有效、由策略层判定通过后发放，模型侧完全看不到它的存在。
class _OutsideGrant(threading.local):
    """本线程当前持有的越界放行目标（realpath 后，含其子树）。"""

    def __init__(self):
        self.paths: list = []


_OUTSIDE_GRANT = _OutsideGrant()


def grant_outside_access(path) -> None:
    """宿主在**策略层判定通过后**授予本次调用对 path（及其子树）的越界放行。

    只在 worker 的审批/策略通道里调用；调用方负责在同一次工具调用结束前撤销
    （revoke_outside_grants），避免令牌跨调用残留。
    """
    if not path:
        return
    try:
        real = os.path.realpath(str(path))
    except Exception:
        return
    if real and real not in _OUTSIDE_GRANT.paths:
        _OUTSIDE_GRANT.paths.append(real)


def revoke_outside_grants() -> None:
    """撤销本线程全部越界放行令牌（每次工具调用结束后必须调用）。"""
    _OUTSIDE_GRANT.paths = []


def is_outside_approved(path) -> bool:
    """path 是否落在某个已授予的越界目标内（realpath + commonpath，符号链接逃逸不认）。"""
    if not path:
        return False
    try:
        real = os.path.realpath(str(path))
    except Exception:
        return False
    for base in list(getattr(_OUTSIDE_GRANT, "paths", []) or []):
        try:
            if os.path.commonpath([os.path.normcase(base), os.path.normcase(real)]) == os.path.normcase(base):
                return True
        except ValueError:
            continue
    return False


def outside_note(path) -> str:
    """给模型的"越界已放行"标注（工具结果里必须带，让对话流里也看得见）。"""
    return f"\n（本次操作在可读写范围之外：{path}）"


def outside_refusal(path, tool_display: str = "本次操作") -> str:
    """越界被拒时的可行动话术：说清"在哪之外 / 当前范围是什么 / 怎么改"。"""
    from yindun.core.permission import LABELS, OUTSIDE_HINTS, permission_mode
    mode = permission_mode()
    return (
        f"❌ 安全拦截：{tool_display}的目标路径在**当前可读写范围之外**（{path}）。\n"
        f"当前可读写范围：{sandbox_scope_text()}\n"
        f"档位判定：{LABELS.get(mode, mode)} —— {OUTSIDE_HINTS.get(mode, '不越界访问')}。\n"
        f"怎么改：① 在设置页把「可读写范围」改到该目录（设置键 sandbox_dir，改完立即生效）；"
        f"或 ② 把文件放进当前范围再操作；或 ③ 把权限档位切到「自主模式 / 完全访问」。"
    )


def _audit_outside(tool_name: str, path: str, decision: str, allowed: bool) -> None:
    """越界操作留痕（放行与拒绝都要写）。

    为什么在**工具层**也写一遍：这是"磁盘访问真的发生了"的那一层——即便调用方
    （worker / 插件 / 脚本）忘了记，审计里也不会出现越界无痕的洞。事件类型沿用
    access_control（与审批留痕同一口径），详情含工具名、真实路径、档位、判定结果。
    """
    try:
        from yindun.core.audit_log import AuditEventType, AuditSeverity, AuditLog
        from yindun.core.permission import permission_mode
        try:
            real_path = os.path.realpath(str(path))
        except Exception:
            real_path = str(path)
        AuditLog().add_entry(
            AuditEventType.ACCESS_CONTROL,
            AuditSeverity.SECURITY if allowed else AuditSeverity.CRITICAL,
            f"可读写范围外{'放行' if allowed else '拒绝'}: {tool_name} {real_path}",
            {
                "tool_name": tool_name,
                "path": real_path,
                "outside_sandbox": True,
                "sandbox_root": sandbox_root(),
                "permission": permission_mode(),
                "decision": decision,
                "approved": allowed,
            },
        )
    except Exception:
        pass          # 审计失败不阻断工具本身，但绝不因此跳过判定


def _check_outside(base_dir: str, tool_name: str, tool_display: str):
    """越界统一判定：返回 (拦截话术 or "", 是否越界已放行)。

    · 范围内 → ("", False)：按老路径执行；
    · 范围外且带宿主令牌 → ("", True)：执行并**必须**在结果里带「在可读写范围之外」标注；
    · 范围外且无令牌 → (拒绝话术, False)：写审计后拒绝（不静默回退！）。
      ★ 为什么这里还能拦下"策略层已放行但没给令牌"的调用：令牌是宿主的承诺，
        没令牌就是没批准（例如模型伪造参数、或插件绕过审批直调工具）。
    """
    _, within = _enforce_sandbox(base_dir)
    if within:
        return "", False
    if is_outside_approved(base_dir):
        _audit_outside(tool_name, base_dir, "outside_allowed", True)
        return "", True
    _audit_outside(tool_name, base_dir, "outside_refused", False)
    return outside_refusal(base_dir, tool_display), False


def _enforce_sandbox(path: str):
    """
    将路径解析为真实物理路径，并判定其是否位于可读写范围（沙箱根）内。
    返回 (resolved, within)：resolved 为 realpath 后的绝对路径，
    within 布尔值表示是否在范围内（用 commonpath 归一化比较，先破解符号链接/junction）。

    ★ 范围可能是**多个根**（默认＝应用根目录 + 桌面/文档/下载），任一命中即范围内。
      拿单根去比会把"桌面"误判成越界 —— 这正是"什么都拒绝"的老毛病。
    """
    real = os.path.realpath(path)
    normalized = os.path.normcase(real)
    for root in sandbox_roots():
        try:
            base = os.path.normcase(os.path.realpath(str(root)))
            if os.path.commonpath([base, normalized]) == base:
                return real, True
        except (ValueError, TypeError):
            continue
    return real, False


def _safe_write_path(file_path, base_dir):
    """写盘前的最终落点校验：把"文件名已拼好的目标路径"再收敛一次到授权目录内。

    背景：仅校验 base_dir 不足以拦住两种越界——
      1. 目录内存在指向外部的符号链接/junction 时，写入会跟随链接逃出授权目录；
      2. 文件名携带分隔符或 8.3 短名等形态时，拼接结果可能落在预期之外。
    因此对最终路径做 realpath + commonpath 双校验，越界即拒绝写入。

    返回 (Path, ok)：ok=False 时调用方必须放弃写入。
    """
    real_file = os.path.realpath(str(file_path))
    real_base = os.path.realpath(str(base_dir))
    try:
        within = os.path.commonpath(
            [os.path.normcase(real_base), os.path.normcase(real_file)]
        ) == os.path.normcase(real_base)
    except ValueError:
        within = False
    return Path(real_file), within


# ──────────────────────────────────────────
# 辅助：统一解析 target_directory 参数
# 每个工具都通过本函数解析目标路径，不再依赖 SANDBOX_PATH 环境变量
# ──────────────────────────────────────────

def _resolve_target_dir(target_directory: str, default_dir=None) -> str:
    """
    将工具参数 target_directory 解析为实际绝对路径。
    支持："当前沙箱目录"、"桌面"、"系统物理桌面"、"E盘"、"D盘文档"、绝对路径等。
    """
    if default_dir is None:
        default_dir = sandbox_root()
    if not target_directory or target_directory == "当前沙箱目录":
        return default_dir

    p = target_directory.strip()

    # ★★★ 优先判断是否是绝对路径（最可靠）
    if os.path.isabs(p):
        return os.path.abspath(p)

    # 然后处理中文描述。"桌面" 走**外壳真实路径**（注册表）：
    # 被 OneDrive 重定向的机器上 `~/Desktop` 是不存在的旧位置，拼它必然落空。
    if "桌面" in target_directory:
        desktop = _shell_folder_dirs().get("Desktop")
        return desktop or os.path.join(os.path.expanduser("~"), "Desktop")

    drive_match = re.search(r"^([A-Za-z])\s*[盘盘]\s*(.*)", p)
    if drive_match:
        drive_letter = drive_match.group(1).upper()
        rest = drive_match.group(2).strip()
        if rest:
            return os.path.abspath(f"{drive_letter}:\\" + re.sub(r"^(?:的|这个|那个|该|此)\s*", "", rest))
        return os.path.abspath(f"{drive_letter}:\\")

    return default_dir


def _shell_folder_dirs() -> dict:
    """外壳"已知文件夹"（桌面/文档/下载）的真实路径；取不到的那项不出现在结果里。

    与 `permission.user_working_dirs()` 同源同口径：可读写范围收哪些目录，
    这里就必须解析到哪些目录，否则会出现"范围内允许、工具却拼错路径"的错位。
    """
    from yindun.core.permission import user_working_dirs
    out: dict = {}
    wanted = {"Desktop": ("桌面",), "Documents": ("文档",), "Downloads": ("下载",)}
    for directory in user_working_dirs():
        name = os.path.basename(directory)
        for key, aliases in wanted.items():
            if name in aliases or key.lower() == name.lower():
                out.setdefault(key, directory)
    return out


def _read_file_text(file_path: str, max_bytes: int = 20000) -> str:
    """多编码 fallback 读取文本文件，最多 max_bytes 字节。"""
    encodings = ["utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1"]
    for enc in encodings:
        try:
            with open(file_path, "r", encoding=enc) as f:
                content = f.read(max_bytes)
            return content
        except (UnicodeDecodeError, LookupError):
            continue
    with open(file_path, "rb") as f:
        return f.read(max_bytes).decode("latin-1", errors="replace")


def preview_command_script(command: str, sandbox: str = "") -> str:
    """审批弹窗用的"脚本内容预览"（防盲签）。

    `python x.py` 这样的命令，弹窗只显示命令串看不出脚本会做什么——
    这里在人工审批前把脚本开头一段读出来一并展示，让"放行"建立在看清内容之上。
    脚本按沙箱 realpath 解析（与 run_local_command 的归属校验同一套规则）；
    非解释器命令 / 读不到 / 越界一律返回空串或一句说明，**绝不因预览失败阻碍审批流程**。
    """
    try:
        if not command:
            return ""
        parts = shlex.split(str(command))
        if len(parts) < 2 or os.path.basename(parts[0]).lower() not in ("python", "python3"):
            return ""
        script = next((a for a in parts[1:] if not a.startswith("-")), "")
        if not script or not script.lower().endswith(".py"):
            return ""
        base = os.path.realpath(sandbox or sandbox_root())
        real = os.path.realpath(os.path.join(base, script))
        try:
            within = os.path.commonpath([os.path.normcase(base), os.path.normcase(real)]) == os.path.normcase(base)
        except ValueError:
            within = False
        if not within and not is_outside_approved(real):
            # 工作目录本身越界时，脚本也在范围外——执行时同样需要宿主放行（见 _check_outside）
            return "(脚本解析后不在当前可读写范围/已放行目录内——执行时会被安全策略拦住)"
        if not os.path.isfile(real):
            return "(脚本不存在)"
        text = Path(real).read_text(encoding="utf-8", errors="replace")
        head = text[:800]
        return head + ("…（已截断）" if len(text) > 800 else "")
    except Exception:
        return ""

# ==========================================
# 1. 定义强类型参数约束表单 (Args Schema)
# ==========================================

class CreateFileInput(BaseModel):
    filename: str = Field(
        ..., 
        description="期望创建的文件名，例如 '1.txt'（必须带后缀）"
    )
    content: str = Field(
        default="", 
        description="要写入文件的具体文本内容，若用户要求无内容则默认为空字符串"
    )
    target_directory: str = Field(
        default="当前沙箱目录", 
        description="操作的目标位置描述。如果提到'桌面'填'系统物理桌面'；如果提到具体盘符或路径（如'D盘'）则直接如实填写；默认填'当前沙箱目录'。"
    )

class DeleteFileInput(BaseModel):
    filename: str = Field(
        ..., 
        description="期望删除、移除或清理的文件名，例如 '1.txt'（必须带后缀）"
    )
    target_directory: str = Field(
        default="当前沙箱目录", 
        description="目标位置描述，默认填'当前沙箱目录'。由系统网关自动继承，无需AI擅自修改。"
    )


class MakeDirInput(BaseModel):
    path: str = Field(
        ...,
        description="要创建的目录：给名称（如 '备份'）就在当前目录下建；给绝对路径（如 'D:\\\\项目\\\\素材'）按绝对路径建，可多级。"
    )
    target_directory: str = Field(
        default="当前沙箱目录",
        description="path 是相对名称时的基准目录，默认填'当前沙箱目录'。"
    )


class MoveFileInput(BaseModel):
    source: Union[str, List[str]] = Field(
        ...,
        description="要移动的源路径（必须是完整路径，如 'C:\\\\Users\\\\me\\\\Desktop\\\\a.txt'）；批量移动时给列表。"
    )
    target: str = Field(
        ...,
        description="目标：既有目录（或带结尾斜杠）= 移动进去并保留原名；否则当作完整目标路径（单个源时可借此改名）。"
    )
    overwrite: bool = Field(
        default=False,
        description="目标已存在时是否覆盖。默认 false = 拒绝并说明；只有用户明确要求覆盖时才填 true。"
    )


class CopyFileInput(BaseModel):
    source: Union[str, List[str]] = Field(
        ...,
        description="要复制的源路径（完整路径）；批量复制时给列表。"
    )
    target: str = Field(
        ...,
        description="目标：既有目录（或带结尾斜杠）= 复制进去并保留原名；否则当作完整目标路径（单个源时可借此改名）。"
    )
    overwrite: bool = Field(
        default=False,
        description="目标已存在时是否覆盖。默认 false = 拒绝并说明。"
    )


class RenameFileInput(BaseModel):
    filename: str = Field(
        ...,
        description="要改名的文件（或目录）名，例如 'report_v1.docx'。"
    )
    new_name: str = Field(
        ...,
        description="新名字，**只填文件名**（如 'report_final.docx'），不许带路径分隔符；换目录请用 move_local_file。"
    )
    target_directory: str = Field(
        default="当前沙箱目录",
        description="文件所在目录，默认填'当前沙箱目录'。"
    )


class BatchRenameInput(BaseModel):
    target_directory: str = Field(
        default="当前沙箱目录",
        description="要批量改名的目录，默认填'当前沙箱目录'。"
    )
    match: str = Field(
        ...,
        description="要被替换的旧文字（**子串匹配，不是正则**），例如改后缀填 '.jpeg'。"
    )
    replace: str = Field(
        default="",
        description="替换成的新文字，例如 '.jpg'；留空 = 直接删掉 match 这段文字。"
    )
    file_pattern: str = Field(
        default="*",
        description="只处理匹配这个通配符的文件名，默认 '*'（全部文件）。"
    )
    include_dirs: bool = Field(
        default=False,
        description="是否连子目录名一起改，默认 false（只改文件）。"
    )

class ListFilesInput(BaseModel):
    target_directory: str = Field(
        default="当前沙箱目录", 
        description="查看的目标位置描述。默认填'当前沙箱目录'。"
    )

class ReadFileInput(BaseModel):
    filename: str = Field(
        ..., 
        description="要读取的文件名，例如 'main.py'（必须带后缀）"
    )
    target_directory: str = Field(
        default="当前沙箱目录", 
        description="文件所在的目标位置描述。默认填'当前沙箱目录'。"
    )

class ModifyFileInput(BaseModel):
    filename: str = Field(
        ..., 
        description="要修改的文件名，例如 'main.py'（必须带后缀）"
    )
    old_content: str = Field(
        default="", 
        description="要替换的旧内容（精确匹配），如果为空则在文件末尾追加"
    )
    new_content: str = Field(
        ..., 
        description="新内容，将替换旧内容或追加到文件末尾"
    )
    target_directory: str = Field(
        default="当前沙箱目录", 
        description="文件所在的目标位置描述。默认填'当前沙箱目录'。"
    )

class RunCommandInput(BaseModel):
    command: str = Field(
        ..., 
        description="要执行的命令，例如 'python test.py' 或 'dir'"
    )
    target_directory: str = Field(
        default="当前沙箱目录", 
        description="命令执行的工作目录描述。默认填'当前沙箱目录'。"
    )

class AnalyzeProjectInput(BaseModel):
    target_directory: str = Field(
        default="当前沙箱目录",
        description="要分析的项目根目录描述。默认填'当前沙箱目录'。"
    )
    max_depth: int = Field(
        default=3,
        description="目录递归扫描的最大深度，1-5 之间，默认 3"
    )
    focus: str = Field(
        default="",
        description="分析重点，如'架构'、'依赖'、'业务逻辑'等；留空则做通用分析"
    )

class SearchInFilesInput(BaseModel):
    pattern: str = Field(
        ...,
        description="要搜索的文本模式或正则表达式"
    )
    file_pattern: str = Field(
        default="*",
        description="文件名匹配模式，如 '*.py'；默认搜索所有文件"
    )
    target_directory: str = Field(
        default="当前沙箱目录",
        description="搜索的根目录描述。默认填'当前沙箱目录'。"
    )
    max_results: int = Field(
        default=20,
        description="最多返回的结果数，默认 20"
    )

# ==========================================
# 2. 核心物理工具函数实现
# ==========================================

@tool(args_schema=ListFilesInput)
def list_local_files(target_directory: str = "当前沙箱目录") -> str:
    """获取指定目录（默认可读写范围）下的所有文件和文件夹列表。"""
    # 只读操作在四档权限（计划/确认/自主/完全）中一律放行；**越界**则由策略层按档位判定
    # （plan 拒绝 / ask 人工确认 / auto·full 放行并标注），本函数不再静默回退到默认目录。

    base_dir = _resolve_target_dir(target_directory)
    blocked, outside_ok = _check_outside(base_dir, "list_local_files", "列出目录")
    if blocked:
        return blocked
    try:
        if not os.path.exists(base_dir):
            os.makedirs(base_dir, exist_ok=True)
        files = os.listdir(base_dir)
        folder_name = os.path.basename(base_dir) if os.path.basename(base_dir) else base_dir
        result = f"✅ 成功读取目录！目录 [{folder_name}] 内的文件有：{', '.join(files)}"
        return result + (outside_note(base_dir) if outside_ok else "")
    except Exception as e:
        return f"❌ 访问文件列表失败：{str(e)}"


@tool(args_schema=CreateFileInput)
def create_local_file(filename: str, content: str = "", target_directory: str = "当前沙箱目录") -> str:
    """
    🚨【判定铁律】这是在本地计算机磁盘上物理‘创建新文件’的工具。
    只有当用户明确发出具有物理保存意图的动词（如：“帮我创建一个文件”、“保存到本地”、“写入磁盘”、“生成文件到E盘”）时，你才可以调用此工具。
    
    如果用户仅仅是说：“帮我写一段Python代码”、“给我生成一份机密报告文档”、“帮我排版一个网页HTML”，而没有明确要求‘保存/创建为物理文件’，
    你绝对禁止、严禁调用此工具！你应当直接在对话框里使用标准的 Markdown 语法（如 ``` 块）直接输出给用户看。
    """
    _blocked = gate_reason("write", "创建文件")
    if _blocked:
        return _blocked

    base_dir = _resolve_target_dir(target_directory)
    blocked, outside_ok = _check_outside(base_dir, "create_local_file", "创建文件")
    if blocked:
        return blocked

    # 🛡️ 边界防御：文件名强制 basename 化（丢弃目录成分），再与授权目录拼接；
    #    随后对最终落点做 realpath+commonpath 二次校验，拦截符号链接跟随式越界。
    #    ★ 这道校验与档位无关、永不放松：越界放行的是"用户指定的那个目录"，
    #      不是"该目录里任何链接指向的地方"。
    safe_filename = os.path.basename(filename)
    file_path, ok = _safe_write_path(Path(base_dir) / safe_filename, base_dir)
    if not ok:
        return f"❌ 安全拦截：解析后的落点超出授权目录（{safe_filename}），已拒绝写入。"

    try:
        if not os.path.exists(base_dir):
            os.makedirs(base_dir, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        folder_name = os.path.basename(base_dir) if os.path.basename(base_dir) else base_dir
        result = f"✅ 权限验证通过！成功在授权目录 [{folder_name}] 下物理创建文件：{safe_filename}。"
        return result + (outside_note(base_dir) if outside_ok else "")
    except Exception as e:
        return f"❌ 物理写盘失败：{str(e)}"


@tool(args_schema=DeleteFileInput)
def delete_local_file(filename: str, target_directory: str = "当前沙箱目录") -> str:
    """在指定目录（默认可读写范围）下删除、移除或清理一个已存在的文本文件。"""
    _blocked = gate_reason("delete", "删除文件")
    if _blocked:
        return _blocked

    base_dir = _resolve_target_dir(target_directory)
    blocked, outside_ok = _check_outside(base_dir, "delete_local_file", "删除文件")
    if blocked:
        return blocked
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(base_dir, safe_filename)

    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            folder_name = os.path.basename(base_dir) if os.path.basename(base_dir) else base_dir
            result = f"✅ 物理删除成功！已从目录 [{folder_name}] 中彻底移除文件：{safe_filename}。"
            return result + (outside_note(base_dir) if outside_ok else "")
        else:
            return f"❌ 删除失败：在当前工作路径中未找到文件 {safe_filename}。"
    except Exception as e:
        return f"❌ 物理删除失败：{str(e)}"


# ──────────────────────────────────────────
# 文件管理（移动 / 复制 / 重命名 / 批量改名 / 建目录）
# ------------------------------------------------------------------
# 为什么单开这一族（V3.6.8，负责人实测反馈）："帮我移动几个文件、批量把后缀改一下"
# 是桌面智能体的**本职工作**，但此前工具集里根本没有移动/改名/复制能力，模型只能用
# "读出来 + 新建 + 删掉"硬拼（还容易丢内容）。于是用户看到的是"这个智能体没用"。
#
# 三条共同约定（与既有工具同口径，不新立规矩）：
#   ① 越界判定复用 `_check_outside`：范围外按档位判定（plan 拒绝 / ask 人工确认 /
#      auto·full 放行并留审计），**两端路径都要判**（源在范围内、目标在范围外也得判）；
#   ② 不改语义地拒绝危险情形：目标已存在且未显式 overwrite=true 时**拒绝**，绝不静默覆盖；
#   ③ 批量操作**先整批预检再动手**：任一环节不合法就一条都不做（不许做一半）。
# ──────────────────────────────────────────

def _has_separator(text: str) -> bool:
    """是否含路径分隔符（`\\`、`/`）。用于强制"new_name 是纯文件名"。"""
    return bool(re.search(r"[\\/]", str(text or "")))


def _looks_like_dir(target: str) -> bool:
    """target 是否应被理解为"目录"（既有目录，或以分隔符结尾）。

    这是移动/复制的关键语义开关：`D:\\存档`（存在）→ 移进去并保留原名；
    `D:\\存档\\a.txt` → 当作完整目标路径（可改名）。
    """
    if not target:
        return False
    if os.path.isdir(target):
        return True
    text = str(target).strip()
    return text.endswith(("\\", "/"))


def _plan_transfer(source, target: str, tool_name: str, tool_display: str, verb: str):
    """移动/复制共用的**整批预检**：返回 (transform 列表, 拦截话术, 是否越界放行)。

    一次把所有检查做完（存在性 / 源=目标 / 目录移入自身子树 / 目标占用 / 两端越界），
    任何一条不通过就整批不动手 —— 批量操作最怕"改了一半"。
    """
    items = [source] if isinstance(source, (str, os.PathLike)) else list(source or [])
    items = [str(item).strip() for item in items if str(item or "").strip()]
    if not items:
        return [], f"❌ {verb}失败：没有给出要{verb}的路径（source 为空）。", False
    if not target or not str(target).strip():
        return [], f"❌ {verb}失败：没有给出目标位置（target 为空）。", False

    target_as_dir = len(items) > 1 or _looks_like_dir(target)
    if len(items) > 1 and not os.path.isdir(target):
        # 多源只有一个去处：必须是一个**已存在**的目录（否则"移到哪"这件事没定义）。
        return [], (f"❌ {verb}失败：一次{verb}多个路径时，target 必须是一个**已存在的目录**"
                    f"（当前给的是 {target}）。请先用 make_local_dir 建目录，"
                    f"或 list_local_files 确认目标路径。"), False

    pairs = []
    for item in items:
        source_abs = os.path.abspath(item)
        if not os.path.exists(source_abs):
            return [], (f"❌ {verb}失败：源路径不存在 {source_abs}。"
                        f"请先用 list_local_files 列出目录、按真实名字重试。"), False
        if target_as_dir:
            destination_abs = os.path.join(os.path.abspath(target),
                                           os.path.basename(source_abs.rstrip("\\/")))
        else:
            destination_abs = os.path.abspath(target)
        if os.path.normcase(source_abs) == os.path.normcase(destination_abs):
            return [], f"❌ {verb}失败：源和目标是同一个路径（{source_abs}），不需要{verb}。", False
        if os.path.isdir(source_abs):
            source_real = os.path.normcase(os.path.realpath(source_abs))
            destination_real = os.path.normcase(os.path.realpath(destination_abs))
            if destination_real.startswith(source_real + os.sep):
                return [], (f"❌ {verb}失败：不能把目录{verb}到它自己的子目录里"
                            f"（{source_abs} → {destination_abs}）。"), False
        parent = os.path.dirname(destination_abs)
        if not os.path.isdir(parent):
            return [], (f"❌ {verb}失败：目标所在目录不存在 {parent}。"
                        f"先用 make_local_dir 建目录，或确认目标路径是否写错。"), False
        pairs.append((source_abs, destination_abs))

    outside_ok = False
    for source_abs, destination_abs in pairs:
        for path in (source_abs, destination_abs):
            blocked, granted = _check_outside(path, tool_name, tool_display)
            if blocked:
                return [], blocked, False
            outside_ok = outside_ok or granted
    return pairs, "", outside_ok


def _apply_transfer(pairs, overwrite: bool, verb: str, tool_name: str,
                    outside_ok: bool = False, copier=None) -> str:
    """执行已在 `_plan_transfer` 里预检通过的移动/复制，并如实报告每一条结果。

    `copier` 为 None 表示移动（shutil.move），否则是复制函数（shutil.copy2/copytree）。
    覆盖只在 `overwrite=True` 时发生，且**覆盖了什么会写进结果**，不静默。
    """
    done: List[str] = []
    overwritten: List[str] = []
    for source_abs, destination_abs in pairs:
        existed = os.path.exists(destination_abs)
        if existed and not overwrite:
            return (f"❌ {verb}失败：目标已存在 {destination_abs}。"
                    f"如需覆盖请在调用时明确 overwrite=true；不想覆盖就换个名字或目录。"
                    + (f"\n（本次已有 {len(done)} 项完成：{('；'.join(done))}）" if done else ""))
        try:
            if existed and overwrite:
                if os.path.isdir(destination_abs) and not os.path.isdir(source_abs):
                    return (f"❌ {verb}失败：目标是目录、源是文件，不能覆盖（{destination_abs}）。"
                            + (f"\n（本次已有 {len(done)} 项完成：{('；'.join(done))}）" if done else ""))
                if os.path.isdir(destination_abs):
                    shutil.rmtree(destination_abs)
                else:
                    os.remove(destination_abs)
                overwritten.append(os.path.basename(destination_abs))
            if copier is None:
                shutil.move(source_abs, destination_abs)
            elif os.path.isdir(source_abs):
                shutil.copytree(source_abs, destination_abs)
            else:
                shutil.copy2(source_abs, destination_abs)
        except Exception as exc:                                        # noqa: BLE001
            return (f"❌ {verb}失败：{source_abs} → {destination_abs}：{exc}"
                    + (f"\n（本次已完成 {len(done)} 项：{('；'.join(done))}）" if done else ""))
        done.append(f"{os.path.basename(source_abs)} → {destination_abs}")

    if not done:
        return f"❌ {verb}失败：没有可执行的项目。"
    head = f"✅ 已{verb} {len(done)} 项"
    if overwritten:
        head += f"（其中覆盖了已存在的：{('、'.join(overwritten))}）"
    body = "\n  " + "\n  ".join(done) if len(done) > 1 else f"：{done[0]}"
    return head + body + (outside_note(pairs[0][1]) if outside_ok else "")


@tool(args_schema=MakeDirInput)
def make_local_dir(path: str, target_directory: str = "当前沙箱目录") -> str:
    """在本地磁盘上新建一个目录（可多级，如 `D:\\项目\\素材`）。目录已存在时如实说明，不报假成功。"""
    _blocked = gate_reason("write", "新建目录")
    if _blocked:
        return _blocked

    base_dir = _resolve_target_dir(target_directory)
    raw = str(path or "").strip()
    if not raw:
        return "❌ 新建目录失败：没有给出目录名（path 为空）。"
    if _has_separator(raw) and not os.path.isabs(raw) and ".." in raw.split(os.sep):
        return "❌ 新建目录失败：相对路径里不允许带 '..'，请给名称或完整绝对路径。"
    candidate = os.path.abspath(raw) if os.path.isabs(raw) else os.path.join(base_dir, raw)

    blocked, outside_ok = _check_outside(candidate, "make_local_dir", "新建目录")
    if blocked:
        return blocked
    if os.path.isdir(candidate):
        return f"ℹ️ 目录已存在，无需创建：{candidate}"
    if os.path.exists(candidate):
        return f"❌ 新建目录失败：同名文件已存在（{candidate}），不能把它当目录。"
    try:
        os.makedirs(candidate, exist_ok=True)
    except Exception as exc:                                            # noqa: BLE001
        return f"❌ 新建目录失败：{candidate}：{exc}"
    return (f"✅ 已创建目录：{candidate}"
            + (outside_note(candidate) if outside_ok else ""))


@tool(args_schema=MoveFileInput)
def move_local_file(source: Union[str, List[str]], target: str, overwrite: bool = False) -> str:
    """移动或重命名本地文件/目录（支持一次移动多个到同一目录）。

    · target 给**既有目录**（或以 `\\`、`/` 结尾）→ 移动进去并保留原文件名；
    · 单个源 + target 给完整路径 → 移动并改名（等价于"换个地方并改名"）。
    目标已存在时默认**拒绝**（不静默覆盖）；只有用户明确要求覆盖才传 overwrite=true。
    """
    _blocked = gate_reason("write", "移动文件")
    if _blocked:
        return _blocked
    pairs, blocked, outside_ok = _plan_transfer(source, target, "move_local_file", "移动文件", "移动")
    if blocked:
        return blocked
    return _apply_transfer(pairs, overwrite, "移动", "move_local_file", outside_ok, copier=None)


@tool(args_schema=CopyFileInput)
def copy_local_file(source: Union[str, List[str]], target: str, overwrite: bool = False) -> str:
    """复制本地文件/目录（支持一次复制多个到同一目录）。目标已存在时默认拒绝覆盖。

    什么时候用复制而不是移动：用户说"备份一份""留个副本""复制到…"时用复制，
    "挪过去""移动"才用 move_local_file。
    """
    _blocked = gate_reason("write", "复制文件")
    if _blocked:
        return _blocked
    pairs, blocked, outside_ok = _plan_transfer(source, target, "copy_local_file", "复制文件", "复制")
    if blocked:
        return blocked
    return _apply_transfer(pairs, overwrite, "复制", "copy_local_file", outside_ok, copier=shutil.copy2)


@tool(args_schema=RenameFileInput)
def rename_local_file(filename: str, new_name: str, target_directory: str = "当前沙箱目录") -> str:
    """就地重命名一个文件或目录（只改名字，不换目录；换目录用 move_local_file）。

    new_name 只接受**纯文件名**（不含 `\\`、`/`），防止被用来把文件挪出当前目录。
    目标名已存在时拒绝（不静默覆盖）；只改大小写的改名允许（Windows 上合法）。
    """
    _blocked = gate_reason("write", "重命名")
    if _blocked:
        return _blocked

    clean_new = str(new_name or "").strip()
    if not clean_new or clean_new in (".", ".."):
        return "❌ 重命名失败：new_name 不能为空、也不能是 '.' 或 '..'。"
    if _has_separator(clean_new):
        return ("❌ 重命名失败：new_name 只能是**文件名**（不能带路径分隔符）。"
                "要换目录请改用 move_local_file(source, target)。")

    base_dir = _resolve_target_dir(target_directory)
    blocked, outside_ok = _check_outside(base_dir, "rename_local_file", "重命名")
    if blocked:
        return blocked
    source_abs = os.path.join(base_dir, os.path.basename(filename))
    destination_abs = os.path.join(base_dir, clean_new)
    if os.path.normcase(source_abs) == os.path.normcase(destination_abs) and \
            os.path.basename(filename) != clean_new:
        pass                                    # 只改大小写：允许
    elif os.path.normcase(source_abs) == os.path.normcase(destination_abs):
        return f"❌ 重命名失败：新名字和原名一样（{clean_new}）。"
    if not os.path.exists(source_abs):
        return (f"❌ 重命名失败：{os.path.basename(filename)} 不存在于 {base_dir}。"
                f"请先用 list_local_files 看真实文件名。")
    if os.path.exists(destination_abs) and \
            os.path.normcase(source_abs) != os.path.normcase(destination_abs):
        return (f"❌ 重命名失败：目标名已存在 {clean_new}。"
                f"换一个名字，或确认要覆盖时先删除/移动原文件。")
    try:
        os.rename(source_abs, destination_abs)
    except Exception as exc:                                            # noqa: BLE001
        return f"❌ 重命名失败：{os.path.basename(filename)} → {clean_new}：{exc}"
    return (f"✅ 已重命名：{os.path.basename(filename)} → {clean_new}（目录 {base_dir}）"
            + (outside_note(base_dir) if outside_ok else ""))


@tool(args_schema=BatchRenameInput)
def batch_rename_local_files(target_directory: str = "当前沙箱目录", match: str = "",
                             replace: str = "", file_pattern: str = "*",
                             include_dirs: bool = False) -> str:
    """批量重命名：把目录下匹配的文件名里的旧文字（match，**子串**不是正则）替换成新文字（replace）。

    典型用法：批量改后缀 `match='.jpeg', replace='.jpg'`；批量改前缀/编号同理。
    改名前先整批预检：只要出现"改名后撞车"就**整批拒绝**，绝不改一半；一个都没匹配上时如实说明。
    """
    _blocked = gate_reason("write", "批量重命名")
    if _blocked:
        return _blocked

    old_text = str(match or "")
    if not old_text:
        return ("❌ 批量重命名失败：match 不能为空 —— 它是要替换的旧文字"
                "（例如批量把后缀 jpeg 改成 jpg 时 match='.jpeg'）。")

    base_dir = _resolve_target_dir(target_directory)
    blocked, outside_ok = _check_outside(base_dir, "batch_rename_local_files", "批量重命名")
    if blocked:
        return blocked
    if not os.path.isdir(base_dir):
        return f"❌ 批量重命名失败：目录不存在 {base_dir}。"
    try:
        names = sorted(os.listdir(base_dir))
    except Exception as exc:                                            # noqa: BLE001
        return f"❌ 批量重命名失败：读不到目录 {base_dir}：{exc}"

    pattern = str(file_pattern or "*")
    planned: List = []
    for name in names:
        full = os.path.join(base_dir, name)
        if not include_dirs and not os.path.isfile(full):
            continue
        if pattern not in ("", "*") and not fnmatch.fnmatch(name.lower(), pattern.lower()):
            continue
        if old_text not in name:
            continue
        new_name = name.replace(old_text, str(replace or ""))
        if not new_name or new_name in (".", "..") or _has_separator(new_name):
            return (f"❌ 批量重命名失败：{name} 替换后的名字不合法（{new_name!r}）。"
                    f"请调整 match/replace，别让替换结果带上路径分隔符或变空。")
        if new_name != name:
            planned.append((name, new_name))

    if not planned:
        return (f"ℹ️ 没有匹配到需要改名的文件：目录 {base_dir}，文件名匹配 {pattern}、"
                f"替换 {old_text!r} → {str(replace or '')!r} 后没有变化。"
                f"请先 list_local_files 看真实文件名，再决定 match 该写什么。")

    existing = {os.path.normcase(name) for name in names}
    targets: set = set()
    collisions: List[str] = []
    for old_name, new_name in planned:
        key = os.path.normcase(new_name)
        if key in targets or (key in existing and os.path.normcase(old_name) != key):
            collisions.append(f"{old_name} → {new_name}")
        targets.add(key)
    if collisions:
        return ("❌ 批量重命名已整批拒绝（会撞车，不能改一半）：\n  "
                + "\n  ".join(collisions)
                + "\n 请把 match/replace 写得更具体（例如带上扩展名或前缀）后重试。")

    changed: List[str] = []
    for old_name, new_name in planned:
        try:
            os.rename(os.path.join(base_dir, old_name), os.path.join(base_dir, new_name))
            changed.append(f"{old_name} → {new_name}")
        except Exception as exc:                                        # noqa: BLE001
            return (f"⚠️ 批量重命名中途失败：{old_name} → {new_name}：{exc}\n"
                    f"已完成 {len(changed)} 项：\n  " + "\n  ".join(changed))
    return (f"✅ 已批量重命名 {len(changed)} 个文件（目录 {base_dir}）：\n  "
            + "\n  ".join(changed)
            + (outside_note(base_dir) if outside_ok else ""))


@tool(args_schema=ReadFileInput)
def read_local_file(filename: str, target_directory: str = "当前沙箱目录") -> str:
    """读取指定目录（默认可读写范围）下的文件内容，用于分析、总结或修改文件。"""
    # 只读一律放行（四档权限均如此）；越界与否由策略层按档位判定
    # —— 越界不再"静默回退到默认目录"（那会让模型拿着别的目录的内容当答案）。

    base_dir = _resolve_target_dir(target_directory)
    blocked, outside_ok = _check_outside(base_dir, "read_local_file", "读取文件")
    if blocked:
        return blocked
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(base_dir, safe_filename)

    try:
        if not os.path.exists(file_path):
            return f"❌ 读取失败：文件 {safe_filename} 不存在。"
        file_size = os.path.getsize(file_path)
        max_bytes = 20000
        content = _read_file_text(file_path, max_bytes)
        size_note = ""
        if file_size > max_bytes:
            size_note = f"\n\n（文件过大，已截断至前 {len(content)} 字符 / 原始 {file_size} 字节。可分段读取。）"
        result = f"✅ 成功读取文件！\n\n文件名：{safe_filename}\n内容：\n{content}{size_note}"
        return result + (outside_note(base_dir) if outside_ok else "")
    except Exception as e:
        return f"❌ 读取失败：{str(e)}"


@tool(args_schema=ModifyFileInput)
def modify_local_file(filename: str, old_content: str = "", new_content: str = "", target_directory: str = "当前沙箱目录") -> str:
    """
    修改指定安全沙箱目录下的文件内容：
    - 如果提供了 old_content，精确匹配后替换为 new_content
    - 如果 old_content 为空，将 new_content 追加到文件末尾
    """
    _blocked = gate_reason("write", "修改文件")
    if _blocked:
        return _blocked
    
    base_dir = _resolve_target_dir(target_directory)
    blocked, outside_ok = _check_outside(base_dir, "modify_local_file", "修改文件")
    if blocked:
        return blocked
    # 文件名 basename 化 + 最终落点 realpath+commonpath 校验（拦截符号链接跟随式越界；
    # 与档位无关，越界放行也不放松这道校验）
    safe_filename = os.path.basename(filename)
    file_path, ok = _safe_write_path(Path(base_dir) / safe_filename, base_dir)
    if not ok:
        return f"❌ 安全拦截：解析后的落点超出授权目录（{safe_filename}），已拒绝修改。"

    try:
        if not os.path.exists(file_path):
            return f"❌ 修改失败：文件 {safe_filename} 不存在。"
        
        content = _read_file_text(file_path)
        
        if old_content:
            if old_content not in content:
                return f"❌ 修改失败：未找到要替换的内容。请提供精确的原文片段。"
            new_content_full = content.replace(old_content, new_content)
        else:
            new_content_full = content + "\n" + new_content
        
        file_path.write_text(new_content_full, encoding="utf-8")
        
        folder_name = os.path.basename(base_dir) if os.path.basename(base_dir) else base_dir
        action = "替换内容" if old_content else "追加内容"
        result = f"✅ 成功{action}！文件 {safe_filename} 已更新。"
        return result + (outside_note(base_dir) if outside_ok else "")
    except Exception as e:
        return f"❌ 修改失败：{str(e)}"


def _run_contained(parts, cwd, env, timeout):
    """在 Windows Job Object（进程容器）里执行命令，返回 (rc, stdout, stderr, timed_out)。

    容器语义（评审意见"强化执行隔离"的 OS 级部分）：
      · KILL_ON_JOB_CLOSE：容器句柄关闭时，**整棵进程树**随之终止——
        命令结束后不留任何孤儿/驻留进程（脚本再 spawn 的后代也一并收回）；
      · ACTIVE_PROCESS = 64：限制活跃进程数，阻断 fork 炸弹式进程扇出；
      · 超时不再只杀直接子进程，而是 TerminateJobObject 终止整棵树。
    任何一步失败（非 Windows / API 失败 / 句柄获取失败）都**回退普通 subprocess.run**——
    容器是纵深防御，不是功能依赖。
    """
    if os.name != "nt":
        r = subprocess.run(parts, shell=False, cwd=cwd, capture_output=True,
                           text=True, timeout=timeout, env=env)
        return r.returncode, r.stdout or "", r.stderr or "", False
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.TerminateJobObject.restype = wintypes.BOOL
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, ctypes.c_uint]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]

        class _BasicLimit(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _IOCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class _ExtendedLimit(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _BasicLimit),
                        ("IoInfo", _IOCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]
        _JOB_KILL_ON_CLOSE, _JOB_ACTIVE_PROCESS, _EXT_INFO = 0x2000, 0x8, 9

        job = k32.CreateJobObjectW(None, None)
        if not job:
            raise OSError("CreateJobObject 失败")
        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = _JOB_KILL_ON_CLOSE | _JOB_ACTIVE_PROCESS
        info.BasicLimitInformation.ActiveProcessLimit = 64
        if not k32.SetInformationJobObject(job, _EXT_INFO, ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(job)
            raise OSError("SetInformationJobObject 失败")

        proc = subprocess.Popen(parts, shell=False, cwd=cwd, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            k32.AssignProcessToJobObject(job, wintypes.HANDLE(int(proc._handle)))
        except Exception:
            pass    # 绑定失败不阻断：最多少一层容器
        try:
            out, err = proc.communicate(timeout=timeout)
            return proc.returncode, out or "", err or "", False
        except subprocess.TimeoutExpired:
            try:
                k32.TerminateJobObject(job, 1)      # 终止整棵进程树（不只直接子进程）
            except Exception:
                proc.kill()
            try:
                out, err = proc.communicate(timeout=5)
            except Exception:
                out, err = "", ""
            return -1, out or "", err or "", True
        finally:
            try:
                k32.CloseHandle(job)
            except Exception:
                pass
    except Exception as exc:
        log.warning("进程容器不可用，回退普通执行：%s: %s", type(exc).__name__, exc)
        r = subprocess.run(parts, shell=False, cwd=cwd, capture_output=True,
                           text=True, timeout=timeout, env=env)
        return r.returncode, r.stdout or "", r.stderr or "", False


@tool(args_schema=RunCommandInput)
def run_local_command(command: str, target_directory: str = "当前沙箱目录") -> str:
    """
    在指定安全沙箱目录下执行受限的系统命令（只读查看、脚本执行等）。
    仅允许白名单命令（python/python3/pip/git/echo），去 shell 化执行并拦截危险参数；
    python 只允许执行**沙箱内**的 .py 脚本（禁止 -c/-m/stdin 内联代码；
    脚本路径经 realpath 校验，符号链接/盘符相对路径等越界形态一律拒绝），
    pip/git 只允许只读子命令（禁止 install/clone 等会触发代码执行的子命令）。

    ★ 命令执行的边界（如实声明，见技术报告"性质边界"）：
      这是**策略层**控制（入口形态收窄 + 脚本沙箱归属校验 + 环境收敛 + 人工审批 + 全量审计）
      叠加**进程容器**（Windows Job Object：命令结束/超时即回收整棵进程树、限制活跃进程数），
      **不是完整的 OS 级文件/网络沙箱**——被放行的 .py 脚本仍以当前用户权限运行、可触达沙箱外资源。
      另提供**严格模式**（设置页关闭「允许执行本地脚本」）：此时解释器执行被整体禁用，
      只剩只读命令，不存在任意代码执行路径。需要更强隔离时请配合系统级方案（受限账户/虚拟机）。
    """
    # 执行命令：计划模式下直接拒绝；其余三档放行到审批层——
    # ★ 而审批层对所有档位都要求人工确认（硬底线，见 core/permission.py 的 MODE_POLICY）
    _blocked = gate_reason("execute", "执行命令")
    if _blocked:
        return _blocked
    
    base_dir = _resolve_target_dir(target_directory)
    blocked, outside_ok = _check_outside(base_dir, "run_local_command", "执行命令的工作目录")
    if blocked:
        return blocked
    
    # 白名单许可：仅命令名 basename 命中时才允许执行
    allowed_commands = {"python", "python3", "pip", "git", "echo"}
    shell_operators = {"|", ";", "&", ">", "<"}
    # ★ 盘符形态一律拦截：既拦 C:\ 也拦 C:evil.py（盘符相对路径的解析依赖进程的
    #   per-drive 当前目录，是绝对路径校验的已知盲区）
    absolute_path_pattern = re.compile(r"^[A-Za-z]:|^\\\\|^/")
    
    # 1. 用 shlex 拆解为 argv 列表（posix=True 以正确识别引号分组，保证 -c 代码整体传入）
    try:
        parts = shlex.split(command)
    except ValueError as e:
        return f"❌ 安全拦截：命令解析失败（{e}），已拒绝执行。"
    if not parts:
        return "❌ 安全拦截：命令为空，已拒绝执行。"
    
    # 2. 白名单校验：命令名必须命中允许清单
    cmd_name = os.path.basename(parts[0]).lower()
    if cmd_name not in allowed_commands:
        return (f"❌ 安全拦截：命令 '{parts[0]}' 不在允许清单"
                f"（python/python3/pip/git/echo）内，已拒绝执行。")
    
    # 3. 参数级纵深防御：拦截 shell 操作符、路径穿越与绝对系统路径
    for arg in parts[1:]:
        if arg in shell_operators:
            return f"❌ 安全拦截：检测到 shell 操作符 '{arg}'，已拒绝执行。"
        if ".." in arg:
            return f"❌ 安全拦截：参数 '{arg}' 含路径穿越 '..'，已拒绝执行。"
        if absolute_path_pattern.match(arg) or os.path.isabs(arg):
            return f"❌ 安全拦截：参数 '{arg}' 为绝对系统路径，已拒绝执行。"

    # 4. 子命令级纵深防御（★ 关键修复）：
    #    仅靠"命令名白名单"不足以约束图灵完备的解释器/包管理器/版本控制工具——
    #      · python -c "<任意代码>"   → 读写任意文件、发起网络请求，等价任意代码执行；
    #      · python -m http.server     → 启动服务并暴露目录；
    #      · pip install <包>          → 执行第三方 setup.py，同样是任意代码执行；
    #      · git -c core.pager=... log → 经 pager/hook 间接执行命令。
    #    这些入口的参数里既没有 shell 元字符也没有路径穿越，参数级拦截天然挡不住，
    #    必须在"子命令/入口形态"这一层禁止。
    args_rest = parts[1:]

    if cmd_name in ("python", "python3"):
        # ★ 严格模式（可选，设置页开关）：关闭后禁用解释器执行——答复"允许 Python 等
        #   通用解释器即非严格沙箱"的评审意见：严格模式下 run_local_command 只剩
        #   只读命令（git 只读 / echo），不存在任意代码执行路径。
        allow_interp = os.environ.get("ALLOW_SCRIPT_EXEC", "1").strip().lower() not in ("0", "false", "no", "off")
        if not allow_interp:
            return ("❌ 安全拦截：严格模式已禁用解释器执行（可在设置页开启「允许执行本地脚本」）。"
                    "当前仅允许只读命令（git 只读子命令 / echo）。")
        # 只允许直接执行授权目录（默认可读写范围）内的 .py 脚本文件
        script = next((a for a in args_rest if not a.startswith("-")), None)
        if script is None:
            return ("❌ 安全拦截：python 不支持内联代码（-c/-m/- 标准输入），"
                    "请改为把 .py 脚本文件放进工作目录后再执行。")
        if args_rest[0].startswith("-"):
            return ("❌ 安全拦截：python 脚本执行前不允许附加解释器开关"
                    f"（如 -c/-m/-u，收到 '{args_rest[0]}'），已拒绝执行。")
        if not script.lower().endswith(".py"):
            return f"❌ 安全拦截：python 仅允许执行 .py 脚本（收到 '{script}'），已拒绝执行。"
        # ★ 脚本归属校验（红队评审整改）：仅校验"后缀是 .py"不足以防越界——
        #   目录内的符号链接/junction 可指向外部脚本，盘符相对路径（C:evil.py）可穿过
        #   绝对路径检查。这里按 realpath 解析后强制落在**可读写范围内或本次已放行的
        #   工作目录内**，并把传给解释器的路径替换为解析后的绝对路径
        #   （同时消除 CWD 歧义与 TOCTOU 空间）。
        #   ★ 与档位的关系：越界放行只覆盖"用户点过确认的那个工作目录"，不会因为
        #     "工作目录被放行"就顺带允许执行链接指向外部的脚本。
        script_abs = os.path.realpath(os.path.join(base_dir, script))
        _, script_within = _enforce_sandbox(script_abs)
        if not script_within and not is_outside_approved(script_abs):
            return (f"❌ 安全拦截：脚本 '{script}' 解析后不在可读写范围内"
                    f"（realpath={script_abs}），已拒绝执行（符号链接/越界路径不允许）。")
        if not os.path.isfile(script_abs):
            return f"❌ 安全拦截：脚本 '{script}' 不存在（按工作目录解析为 {script_abs}）。"
        parts[parts.index(script, 1)] = script_abs

    elif cmd_name == "pip":
        allowed_subcommands = {"list", "show", "check", "freeze", "--version", "-V"}
        got = args_rest[0] if args_rest else "(无)"
        if got not in allowed_subcommands:
            return (f"❌ 安全拦截：pip 仅允许只读子命令 "
                    f"[{'/'.join(sorted(allowed_subcommands))}]，收到 '{got}'，已拒绝执行"
                    "（install/download 会执行第三方代码，属高危入口）。")

    elif cmd_name == "git":
        allowed_subcommands = {"status", "log", "diff", "show", "branch", "rev-parse",
                               "ls-files", "describe", "blame", "grep", "tag", "shortlog"}
        got = args_rest[0] if args_rest else "(无)"
        if got not in allowed_subcommands:
            return (f"❌ 安全拦截：git 仅允许只读子命令 "
                    f"[{'/'.join(sorted(allowed_subcommands))}]，收到 '{got}'，已拒绝执行"
                    "（clone/fetch/config 等会改动仓库或触发外部执行）。")
        # git 危险开关：可覆盖 pager/hook/仓库指向，从而间接执行命令或越界访问。
        # ★ 前缀匹配（而非等值匹配）：--pager=x / --open-files-in-pager=calc 这类
        #   带值形态此前可绕过等值匹配（红队评审发现）。
        dangerous_prefixes = ("--pager", "--open-files-in-pager", "--exec-path", "--exec",
                              "--upload-pack", "--receive-pack", "--git-dir", "--work-tree",
                              "--config-env", "--output")
        bad = next((a for a in args_rest
                    if a in ("-c", "-C") or a.startswith(dangerous_prefixes)), None)
        if bad:
            return f"❌ 安全拦截：git 参数 '{bad}' 可绕过安全边界，已拒绝执行。"

    # ★ 子进程环境收敛（红队评审整改）：不再继承完整环境——剥离 API Key / 内部变量，
    #   只保留运行所需的基础变量，避免脚本从环境里读到密钥或内部状态。
    _PASSTHROUGH = {
        "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "PATH",
        "TEMP", "TMP", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER", "OS", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
        "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
        "PROGRAMFILES(X86)", "PYTHONIOENCODING", "PYTHONUTF8", "LANG", "LC_ALL",
    }
    child_env = {k: v for k, v in os.environ.items() if k.upper() in _PASSTHROUGH}

    try:
        rc, stdout, stderr, timed_out = _run_contained(parts, base_dir, child_env, 60)
        if timed_out:
            return "❌ 命令执行超时（超过60秒），进程树已终止。"
        output = (stdout or "")[:3000]
        error = (stderr or "")[:3000]
        out_trim = "（已截断）" if stdout and len(stdout) > 3000 else ""
        err_trim = "（已截断）" if stderr and len(stderr) > 3000 else ""
        if rc == 0:
            return f"✅ 命令执行成功！\n\n输出：\n{output}{out_trim}" + (outside_note(base_dir) if outside_ok else "")
        else:
            return (f"❌ 命令执行失败（退出码 {rc}）！\n\n标准输出：\n{output}{out_trim}\n\n"
                    f"错误输出：\n{error}{err_trim}" + (outside_note(base_dir) if outside_ok else ""))
    except subprocess.TimeoutExpired:
        return f"❌ 命令执行超时（超过60秒），已终止。"
    except Exception as e:
        return f"❌ 命令执行失败：{str(e)}"


# ──────────────────────────────────────────────
# 项目分析专用工具（支持代码项目理解、跨文件搜索）
# ──────────────────────────────────────────────

# 常见代码/文档扩展名
_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp",
    ".go", ".rs", ".rb", ".php", ".sh", ".bat", ".ps1", ".sql", ".html", ".css",
    ".vue", ".svelte", ".kt", ".swift", ".m", ".mm", ".scala", ".r", ".lua",
    ".json", ".yaml", ".yml", ".toml", ".xml", ".md", ".txt", ".cfg", ".ini",
}

# 应跳过的目录（编译产物/依赖/版本控制等）
_SKIP_DIRS = {
    "__pycache__", "node_modules", ".git", ".svn", ".hg", ".idea", ".vscode",
    "venv", "env", ".venv", ".env", "dist", "build", "target", "out",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "vendor", "packages",
    ".gradle", "bin", "obj", "DerivedData", ".next", ".nuxt",
    "site-packages", "Lib", "Scripts", "Include",  # Python 虚拟环境目录
    "Include",  # Python 虚拟环境 include 目录
}

# 关键项目文件（用于快速识别项目类型）
_KEY_FILES = {
    "package.json": "Node.js/前端项目",
    "requirements.txt": "Python 项目（pip）",
    "pyproject.toml": "Python 项目（现代）",
    "setup.py": "Python 项目（传统）",
    "Pipfile": "Python 项目（Pipenv）",
    "poetry.lock": "Python 项目（Poetry）",
    "Cargo.toml": "Rust 项目",
    "go.mod": "Go 项目",
    "pom.xml": "Java 项目（Maven）",
    "build.gradle": "Java 项目（Gradle）",
    "build.gradle.kts": "Kotlin 项目（Gradle）",
    "Gemfile": "Ruby 项目",
    "composer.json": "PHP 项目",
    ".csproj": "C# 项目",
    "Makefile": "C/C++ 项目",
    "CMakeLists.txt": "C/C++ 项目（CMake）",
    "pubspec.yaml": "Dart/Flutter 项目",
    "Project.toml": "Julia 项目",
    "deno.json": "Deno 项目",
    "README.md": "项目说明",
    "README.rst": "项目说明",
    "README.txt": "项目说明",
}


def _build_file_tree(root_dir: str, max_depth: int) -> str:
    """递归构建文件树（带深度限制）。"""
    lines = []

    def _walk(current_dir: str, prefix: str, depth: int):
        if depth > max_depth:
            return
        try:
            entries = sorted(os.listdir(current_dir))
        except (PermissionError, OSError):
            return

        # 过滤
        entries = [e for e in entries if not e.startswith(".") or e in {".env", ".gitignore"}]
        entries = [e for e in entries if e not in _SKIP_DIRS]

        dirs = [e for e in entries if os.path.isdir(os.path.join(current_dir, e))]
        files = [e for e in entries if not os.path.isdir(os.path.join(current_dir, e))]

        items = dirs + files
        for i, name in enumerate(items):
            is_last = i == len(items) - 1
            connector = "└── " if is_last else "├── "
            full_path = os.path.join(current_dir, name)
            is_dir = os.path.isdir(full_path)
            display = f"{name}/" if is_dir else name
            lines.append(f"{prefix}{connector}{display}")

            if is_dir and depth < max_depth:
                extension = "    " if is_last else "│   "
                _walk(full_path, prefix + extension, depth + 1)

    _walk(root_dir, "", 1)
    return "\n".join(lines)


def _collect_key_files(root_dir: str, max_depth: int) -> dict:
    """收集关键文件（项目配置、入口、README等）。"""
    result = {"configs": [], "readmes": [], "entry_points": []}

    for current_dir, dirs, files in os.walk(root_dir):
        # 限制深度
        rel = os.path.relpath(current_dir, root_dir)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > max_depth:
            dirs[:] = []
            continue
        # 跳过特定目录
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]

        for f in files:
            full_path = os.path.join(current_dir, f)
            rel_path = os.path.relpath(full_path, root_dir)

            if f in _KEY_FILES:
                project_type = _KEY_FILES[f]
                if "README" in f.upper():
                    result["readmes"].append((rel_path, project_type))
                else:
                    result["configs"].append((rel_path, project_type))

            # 识别常见入口文件
            elif f in {"main.py", "app.py", "index.js", "index.ts", "main.go",
                       "Main.java", "Program.cs", "main.rs", "main.cpp"}:
                result["entry_points"].append((rel_path, "入口文件"))

    return result


def _summarize_code_files(root_dir: str, max_depth: int, focus: str) -> str:
    """采样几个关键代码文件返回摘要（前 max_depth*10 行）。"""
    samples = []
    # 优先读取的关键文件（配置、数据、入口）
    priority_files = {"data.js", "config.js", "settings.js", "app.js", "main.js", "boot.js", 
                      "index.js", "game.js", "store.js", "storage.js", 
                      "data.json", "config.json", "settings.json", "package.json"}
    
    for current_dir, dirs, files in os.walk(root_dir):
        rel = os.path.relpath(current_dir, root_dir)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > max_depth:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]

        for f in files:
            _, ext = os.path.splitext(f)
            # 优先读取关键配置文件，再读取其他代码文件
            is_priority = f.lower() in priority_files
            is_code = ext.lower() in _CODE_EXTENSIONS
            
            if is_priority or is_code:
                full_path = os.path.join(current_dir, f)
                try:
                    with open(full_path, "r", encoding="utf-8") as fp:
                        content = fp.read(3000)  # 关键文件多读一点
                    rel_path = os.path.relpath(full_path, root_dir)
                    samples.append((rel_path, ext, content))
                    if len(samples) >= 8:  # 增加采样数量
                        return _format_samples(samples, max_depth)
                except Exception:
                    continue
    return _format_samples(samples, max_depth)


def _format_samples(samples: list, max_depth: int) -> str:
    if not samples:
        return "（未找到代码文件）"
    max_lines = max_depth * 10
    parts = []
    for path, ext, content in samples:
        lines = content.split("\n")[:max_lines]
        snippet = "\n".join(lines)
        parts.append(f"### {path} ({ext})\n```\n{snippet}\n```\n")
    return "\n".join(parts)


@tool(args_schema=AnalyzeProjectInput)
def analyze_project(target_directory: str = "当前沙箱目录", max_depth: int = 3, focus: str = "") -> str:
    """
    分析指定目录下的代码项目结构：
    1. 递归列出文件树（受 max_depth 限制）
    2. 识别项目类型（Node.js / Python / Java 等）
    3. 收集关键文件清单（配置、入口、README）
    4. 采样主要代码文件返回摘要（前 max_depth*10 行）
    
    适用于回答"这个项目是做什么的"、"项目结构"、"技术栈"等问题。
    """
    # 只读操作（分析 / 检索）在四档权限中一律放行；越界由策略层按档位判定（不再静默回退）
    
    # 优先使用传入的 target_directory 参数，其次使用环境变量，最后使用当前目录
    base_dir = _resolve_target_dir(target_directory)
    blocked, outside_ok = _check_outside(base_dir, "analyze_project", "分析项目")
    if blocked:
        return blocked
    
    depth = max(1, min(5, max_depth))
    
    try:
        if not os.path.exists(base_dir):
            return f"❌ 分析失败：目录 {base_dir} 不存在。"
        
        # 1. 文件树
        file_tree = _build_file_tree(base_dir, depth)
        
        # 2. 关键文件
        key_files = _collect_key_files(base_dir, depth)
        
        # 3. 代码采样
        code_samples = _summarize_code_files(base_dir, depth, focus)
        
        # 组装结果（结构化分段，便于 LLM 引用）
        result_parts = []
        result_parts.append(f"[项目根目录] {base_dir}")
        
        if focus:
            result_parts.append(f"[分析重点] {focus}")
        
        # 项目类型推断
        if key_files["configs"]:
            project_types = list(set(pt for _, pt in key_files["configs"]))
            result_parts.append(f"[项目类型] {', '.join(project_types)}")
        
        # 关键文件
        kf_lines = []
        for path, ptype in key_files["configs"]:
            kf_lines.append(f"- {path}  <{ptype}>")
        for path, ptype in key_files["readmes"]:
            kf_lines.append(f"- {path}  <{ptype}>")
        for path, ptype in key_files["entry_points"]:
            kf_lines.append(f"- {path}  <{ptype}>")
        if kf_lines:
            result_parts.append("[关键文件]\n" + "\n".join(kf_lines))
        
        # 文件树
        result_parts.append(f"[文件结构-深度{depth}]\n```\n{file_tree}\n```")
        
        # 代码采样
        result_parts.append(f"[代码采样]\n{code_samples}")
        
        result = "\n\n".join(result_parts)
        
        # 截断过长输出
        if len(result) > 8000:
            result = result[:8000] + "\n\n...（输出已截断，建议使用 search_in_files 查询具体内容）"
        # 越界已放行：结果里必须显式标注（对话流里看得见，模型不会当成"范围内的普通读取"）
        return result + (outside_note(base_dir) if outside_ok else "")
    except Exception as e:
        return f"❌ 项目分析失败：{str(e)}"


@tool(args_schema=SearchInFilesInput)
def search_in_files(pattern: str, file_pattern: str = "*", target_directory: str = "当前沙箱目录", max_results: int = 20) -> str:
    """
    在指定目录的所有文件中搜索文本模式（类似 grep）。
    支持文件名过滤（如 *.py），支持正则表达式。
    """
    # 只读操作（分析 / 检索）在四档权限中一律放行；越界由策略层按档位判定（不再静默回退）
    
    base_dir = _resolve_target_dir(target_directory)
    blocked, outside_ok = _check_outside(base_dir, "search_in_files", "文件搜索")
    if blocked:
        return blocked
    max_n = max(1, min(100, max_results))
    
    import fnmatch
    import re as _re
    
    try:
        if not os.path.exists(base_dir):
            return f"❌ 搜索失败：目录 {base_dir} 不存在。"
        
        # 编译正则
        try:
            regex = _re.compile(pattern)
        except _re.error as e:
            # 退化为字面字符串
            regex = _re.compile(_re.escape(pattern))
        
        results = []
        for current_dir, dirs, files in os.walk(base_dir):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
            
            for f in files:
                if not fnmatch.fnmatch(f, file_pattern):
                    continue
                full_path = os.path.join(current_dir, f)
                rel_path = os.path.relpath(full_path, base_dir)
                
                try:
                    with open(full_path, "r", encoding="utf-8") as fp:
                        for line_num, line in enumerate(fp, 1):
                            if regex.search(line):
                                results.append(f"{rel_path}:{line_num}: {line.rstrip()}")
                                if len(results) >= max_n:
                                    break
                except UnicodeDecodeError:
                    try:
                        with open(full_path, "r", encoding="gbk") as fp:
                            for line_num, line in enumerate(fp, 1):
                                if regex.search(line):
                                    results.append(f"{rel_path}:{line_num}: {line.rstrip()}")
                                    if len(results) >= max_n:
                                        break
                    except (PermissionError, OSError, UnicodeDecodeError):
                        continue
                except (PermissionError, OSError):
                    continue
                
                if len(results) >= max_n:
                    break
            if len(results) >= max_n:
                break
        
        if not results:
            return f"🔍 在 {base_dir} 中未找到匹配 '{pattern}' 的内容。"

        header = f"🔍 搜索结果：'{pattern}'（文件模式: {file_pattern}，共 {len(results)} 条）\n\n"
        return header + "\n".join(results) + (outside_note(base_dir) if outside_ok else "")
    except Exception as e:
        return f"❌ 搜索失败：{str(e)}"


# ==========================================
# 附件分块读取工具（解决长文档题目被截断的问题）
# ==========================================

class ReadAttachmentChunkInput(BaseModel):
    file: str = Field(
        ...,
        description="要读取的附件文件名（必须与之前挂载的文件名一致，例如 '数学试卷.pdf'）"
    )
    question: str = Field(
        default="",
        description="可选：题号或题号关键词（如 '5'、'第5题'、'12'）。传入后会返回该题及其上下文片段。"
    )
    keyword: str = Field(
        default="",
        description="可选：题干中的关键词（如 '三角函数'、'假设'），用于模糊定位不含题号的题目。"
    )
    char_start: int = Field(
        default=-1,
        description="可选：从全文第 N 个字符开始读取（用于按顺序浏览长文档），默认 -1 表示不使用此模式。"
    )
    char_length: int = Field(
        default=6000,
        description="可选：读取的字符长度，默认 6000。范围 500~12000。"
    )


@tool(args_schema=ReadAttachmentChunkInput)
def read_attachment_chunk(file: str, question: str = "", keyword: str = "",
                          char_start: int = -1, char_length: int = 6000) -> str:
    """
    📄 读取长附件文档的指定片段。

    适用场景：
    - 当附件文档较长（超过 30000 字符），初始上下文只截取了开头部分，
      后续问题涉及的题目/内容可能落在截断之外。
    - 当需要精确定位某道题（如"第 5 题"）的完整原文时。

    使用方式（任选其一）：
    1. 传 question='5' 或 '第5题'：返回该题号的完整题干及前后各 1 道题作为上下文。
    2. 传 keyword='三角函数'：返回首次出现该关键词的片段（前后各 3000 字符）。
    3. 传 char_start=30000：从全文第 30000 字符开始读取 char_length 字符。

    返回：原文片段字符串。若未找到匹配，返回提示信息。
    """
    # 该工具的实际逻辑由 Worker 在 _execute_tool 中拦截执行
    # （因为附件文本存放在 Worker 内存中，工具自身无法访问）
    # 此处仅作为占位实现，确保 LangChain 能正确绑定工具 schema
    return "[占位] read_attachment_chunk 的实际执行由 Worker 拦截处理。若你看到此消息，说明 Worker 拦截逻辑未生效。"


# ==========================================
# 知识库语义检索工具（脱敏 RAG）
# ==========================================

class SearchKnowledgeBaseInput(BaseModel):
    query: str = Field(
        ...,
        description="要在知识库中检索的问题或关键词。例如'合同违约金条款'、'张三的联系方式'。"
    )
    top_k: int = Field(
        default=4,
        description="返回的相关片段数量，默认4，范围1~8。问题复杂时可增大。"
    )


@tool(args_schema=SearchKnowledgeBaseInput)
def search_knowledge_base(query: str, top_k: int = 4) -> str:
    """
    🔍 在本地知识库中进行语义检索（脱敏 RAG）。

    适用场景：
    - 当用户询问已入库文档中的内容时（如"合同里的违约金是多少"）
    - 当需要跨多份文档对比信息时（如"哪份合同金额最高"）
    - 当关键词匹配找不到时（如问"提前终止"但文档写的是"解除协议"）

    特点：
    - 语义检索：按意思匹配，不依赖精确关键词
    - 全程脱敏：检索结果已脱敏，敏感信息以占位符显示
    - 多文档支持：可跨所有已入库文档检索

    返回：相关的脱敏文本片段（含来源标注）。
    """
    # 该工具的实际逻辑由 Worker 在 _execute_tool 中拦截执行
    # （因为知识库实例由 Worker 管理，工具自身无法访问）
    return "[占位] search_knowledge_base 的实际执行由 Worker 拦截处理。"