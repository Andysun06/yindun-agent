import os
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