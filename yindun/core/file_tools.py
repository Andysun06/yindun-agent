import os
import subprocess
from langchain_core.tools import tool
from pydantic import BaseModel, Field

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
    """获取指定安全沙箱目录下的所有文件和文件夹列表。"""
    perm = os.environ.get("PERMISSION_LEVEL", "完全控制 (读/写/列表)")
    if "彻底审计" in perm:
        return "❌ 权限安全拦截：当前系统权限等级为【彻底审计】，已彻底断开大模型的所有本地物理文件访问权限！"
        
    base_dir = os.environ.get("SANDBOX_PATH", os.path.abspath("."))
    try:
        if not os.path.exists(base_dir):
            os.makedirs(base_dir, exist_ok=True)
        files = os.listdir(base_dir)
        folder_name = os.path.basename(base_dir) if os.path.basename(base_dir) else base_dir
        return f"✅ 成功读取目录！当前目标沙箱 [{folder_name}] 内的文件有：{', '.join(files)}"
    except Exception as e:
        return f"❌ 访问沙箱文件列表失败：{str(e)}"


@tool(args_schema=CreateFileInput)
def create_local_file(filename: str, content: str = "", target_directory: str = "当前沙箱目录") -> str:
    """
    🚨【判定铁律】这是在本地计算机磁盘上物理‘创建新文件’的工具。
    只有当用户明确发出具有物理保存意图的动词（如：“帮我创建一个文件”、“保存到本地”、“写入磁盘”、“生成文件到E盘”）时，你才可以调用此工具。
    
    如果用户仅仅是说：“帮我写一段Python代码”、“给我生成一份机密报告文档”、“帮我排版一个网页HTML”，而没有明确要求‘保存/创建为物理文件’，
    你绝对禁止、严禁调用此工具！你应当直接在对话框里使用标准的 Markdown 语法（如 ``` 块）直接输出给用户看。
    """
    perm = os.environ.get("PERMISSION_LEVEL", "完全控制 (读/写/列表)")
    if "彻底审计" in perm:
        return "❌ 权限安全拦截：当前系统权限等级为【彻底审计】，已彻底断开大模型的所有本地物理文件访问权限！"
    elif "安全只读" in perm:
        return "❌ 权限安全拦截：当前系统权限等级为【安全只读】，大模型无权在当前目录进行任何写盘、修改或创建文件操作！"

    base_dir = os.environ.get("SANDBOX_PATH", os.path.abspath("."))
    
    # 🛡️ 边界防御：强制过滤掉路径穿越符号（如 ../../），防止 AI 乱写到系统核心区
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(base_dir, safe_filename)
    
    try:
        if not os.path.exists(base_dir):
            os.makedirs(base_dir, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        folder_name = os.path.basename(base_dir) if os.path.basename(base_dir) else base_dir
        return f"✅ 权限验证通过！成功在当前授权沙箱 [{folder_name}] 下物理创建文件：{safe_filename}。"
    except Exception as e:
        return f"❌ 物理写盘失败：{str(e)}"


@tool(args_schema=DeleteFileInput)
def delete_local_file(filename: str, target_directory: str = "当前沙箱目录") -> str:
    """在指定的安全沙箱目录下删除、移除或清理一个已存在的文本文件。"""
    perm = os.environ.get("PERMISSION_LEVEL", "完全控制 (读/写/列表)")
    if "完全控制" not in perm:
        return "❌ 权限安全拦截：当前系统权限等级限制，大模型无权执行物理删除操作！"

    base_dir = os.environ.get("SANDBOX_PATH", os.path.abspath("."))
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(base_dir, safe_filename)
    
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            folder_name = os.path.basename(base_dir) if os.path.basename(base_dir) else base_dir
            return f"✅ 物理删除成功！已从沙箱 [{folder_name}] 中彻底移除文件：{safe_filename}。"
        else:
            return f"❌ 删除失败：在当前工作路径中未找到文件 {safe_filename}。"
    except Exception as e:
        return f"❌ 物理删除失败：{str(e)}"


@tool(args_schema=ReadFileInput)
def read_local_file(filename: str, target_directory: str = "当前沙箱目录") -> str:
    """读取指定安全沙箱目录下的文件内容，用于分析、总结或修改文件。"""
    perm = os.environ.get("PERMISSION_LEVEL", "完全控制 (读/写/列表)")
    if "彻底审计" in perm:
        return "❌ 权限安全拦截：当前系统权限等级为【彻底审计】，已彻底断开大模型的所有本地物理文件访问权限！"
    
    base_dir = os.environ.get("SANDBOX_PATH", os.path.abspath("."))
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(base_dir, safe_filename)
    
    try:
        if not os.path.exists(file_path):
            return f"❌ 读取失败：文件 {safe_filename} 不存在。"
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return f"✅ 成功读取文件！\n\n文件名：{safe_filename}\n内容：\n{content}"
    except UnicodeDecodeError:
        try:
            with open(file_path, "r", encoding="gbk") as f:
                content = f.read()
            return f"✅ 成功读取文件（GBK编码）！\n\n文件名：{safe_filename}\n内容：\n{content}"
        except Exception as e:
            return f"❌ 读取失败：无法解码文件内容（{str(e)}）。"
    except Exception as e:
        return f"❌ 读取失败：{str(e)}"


@tool(args_schema=ModifyFileInput)
def modify_local_file(filename: str, old_content: str = "", new_content: str = "", target_directory: str = "当前沙箱目录") -> str:
    """
    修改指定安全沙箱目录下的文件内容：
    - 如果提供了 old_content，精确匹配后替换为 new_content
    - 如果 old_content 为空，将 new_content 追加到文件末尾
    """
    perm = os.environ.get("PERMISSION_LEVEL", "完全控制 (读/写/列表)")
    if "彻底审计" in perm:
        return "❌ 权限安全拦截：当前系统权限等级为【彻底审计】，已彻底断开大模型的所有本地物理文件访问权限！"
    elif "安全只读" in perm:
        return "❌ 权限安全拦截：当前系统权限等级为【安全只读】，大模型无权在当前目录进行任何写盘、修改或创建文件操作！"
    
    base_dir = os.environ.get("SANDBOX_PATH", os.path.abspath("."))
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(base_dir, safe_filename)
    
    try:
        if not os.path.exists(file_path):
            return f"❌ 修改失败：文件 {safe_filename} 不存在。"
        
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        if old_content:
            if old_content not in content:
                return f"❌ 修改失败：未找到要替换的内容。请提供精确的原文片段。"
            new_content_full = content.replace(old_content, new_content)
        else:
            new_content_full = content + "\n" + new_content
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content_full)
        
        folder_name = os.path.basename(base_dir) if os.path.basename(base_dir) else base_dir
        action = "替换内容" if old_content else "追加内容"
        return f"✅ 成功{action}！文件 {safe_filename} 已更新。"
    except Exception as e:
        return f"❌ 修改失败：{str(e)}"


@tool(args_schema=RunCommandInput)
def run_local_command(command: str, target_directory: str = "当前沙箱目录") -> str:
    """
    在指定安全沙箱目录下执行系统命令（如 python 脚本、pip 安装、git 操作等）。
    仅限在当前沙箱目录内执行，禁止执行危险命令（如 format、rm -rf / 等）。
    """
    perm = os.environ.get("PERMISSION_LEVEL", "完全控制 (读/写/列表)")
    if "彻底审计" in perm or "安全只读" in perm:
        return "❌ 权限安全拦截：当前系统权限等级不允许执行命令！"
    
    base_dir = os.environ.get("SANDBOX_PATH", os.path.abspath("."))
    
    dangerous_patterns = [
        "format", "rm -rf", "del /s", "del /f", "shutdown", "reboot", "restart",
        "> /dev", "> /sys", "> /etc", "chmod -R", "chown -R",
    ]
    cmd_lower = command.lower()
    for pattern in dangerous_patterns:
        if pattern in cmd_lower:
            return f"❌ 安全拦截：检测到危险命令模式 '{pattern}'，已拒绝执行。"
    
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=base_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = result.stdout if result.stdout else ""
        error = result.stderr if result.stderr else ""
        
        if result.returncode == 0:
            return f"✅ 命令执行成功！\n\n输出：\n{output}"
        else:
            return f"❌ 命令执行失败（退出码 {result.returncode}）！\n\n标准输出：\n{output}\n\n错误输出：\n{error}"
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
    """采样几个关键代码文件返回摘要（前 30 行）。"""
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
                        return _format_samples(samples)
                except Exception:
                    continue
    return _format_samples(samples)


def _format_samples(samples: list) -> str:
    if not samples:
        return "（未找到代码文件）"
    parts = []
    for path, ext, content in samples:
        # 取前 30 行
        lines = content.split("\n")[:30]
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
    4. 采样主要代码文件返回摘要（前 30 行）
    
    适用于回答"这个项目是做什么的"、"项目结构"、"技术栈"等问题。
    """
    perm = os.environ.get("PERMISSION_LEVEL", "完全控制 (读/写/列表)")
    if "彻底审计" in perm:
        return "❌ 权限安全拦截：当前系统权限等级为【彻底审计】，已彻底断开大模型的所有本地物理文件访问权限！"
    
    # 优先使用传入的 target_directory 参数，其次使用环境变量，最后使用当前目录
    if target_directory and target_directory != "当前沙箱目录":
        base_dir = os.path.abspath(target_directory)
    else:
        base_dir = os.environ.get("SANDBOX_PATH", os.path.abspath("."))
    
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
        
        # 组装结果
        result = f"# 项目分析报告：{base_dir}\n\n"
        
        if focus:
            result += f"**分析重点**：{focus}\n\n"
        
        # 项目类型推断
        if key_files["configs"]:
            project_types = list(set(pt for _, pt in key_files["configs"]))
            result += f"## 项目类型\n推断为：{', '.join(project_types)}\n\n"
        
        # 关键文件
        if key_files["configs"] or key_files["readmes"] or key_files["entry_points"]:
            result += "## 关键文件\n"
            for path, ptype in key_files["configs"]:
                result += f"- 📄 {path} ({ptype})\n"
            for path, ptype in key_files["readmes"]:
                result += f"- 📖 {path} ({ptype})\n"
            for path, ptype in key_files["entry_points"]:
                result += f"- 🚀 {path} ({ptype})\n"
            result += "\n"
        
        # 文件树
        result += f"## 文件结构（深度 {depth}）\n```\n{file_tree}\n```\n\n"
        
        # 代码采样
        result += f"## 主要代码文件摘要（前 5 个）\n{code_samples}\n"
        
        # 截断过长输出
        if len(result) > 8000:
            result = result[:8000] + "\n\n...（输出已截断，建议使用 search_in_files 查询具体内容）"
        
        return result
    except Exception as e:
        return f"❌ 项目分析失败：{str(e)}"


@tool(args_schema=SearchInFilesInput)
def search_in_files(pattern: str, file_pattern: str = "*", target_directory: str = "当前沙箱目录", max_results: int = 20) -> str:
    """
    在指定目录的所有文件中搜索文本模式（类似 grep）。
    支持文件名过滤（如 *.py），支持正则表达式。
    """
    perm = os.environ.get("PERMISSION_LEVEL", "完全控制 (读/写/列表)")
    if "彻底审计" in perm:
        return "❌ 权限安全拦截：当前系统权限等级为【彻底审计】，已彻底断开大模型的所有本地物理文件访问权限！"
    
    base_dir = os.environ.get("SANDBOX_PATH", os.path.abspath("."))
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
                except (UnicodeDecodeError, PermissionError, OSError):
                    continue
                
                if len(results) >= max_n:
                    break
            if len(results) >= max_n:
                break
        
        if not results:
            return f"🔍 在 {base_dir} 中未找到匹配 '{pattern}' 的内容。"
        
        header = f"🔍 搜索结果：'{pattern}'（文件模式: {file_pattern}，共 {len(results)} 条）\n\n"
        return header + "\n".join(results)
    except Exception as e:
        return f"❌ 搜索失败：{str(e)}"