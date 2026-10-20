# -*- coding: utf-8 -*-
"""
隐盾本地知识库 + 脱敏 RAG 引擎

设计原则：
1. 入库即脱敏 —— 向量库里只存占位符文本，真实隐私永不落盘
2. 本地优先 —— embedding 用 Ollama 本地模型，向量库用 Chroma 本地文件存储
3. 接口简洁 —— add_document / search / restore / list / remove 五个方法
4. 优雅降级 —— 依赖未安装时返回明确错误，不影响现有功能

数据流：
  【建库】文档 → 解析全文 → 切块(500字+50重叠) → 每块脱敏 → 向量化 → 存入 Chroma
  【检索】问题 → 脱敏 → 向量化 → Chroma 检索 top-K → 返回脱敏片段 + 全局映射表
  【还原】LLM 回答(含占位符) → 用全局映射表替换 → 展示真实内容给用户

【安全机制：双层加密】
chunk 的敏感数据映射表采用双层加密：PrivacyEngine 层加密真实值（Fernet），
SecretManager 层整体加密映射表，metadata 中只存密文，磁盘上无任何明文对应关系。
"""
import os
import re
import json
import threading
import time
import hashlib
from typing import Optional

from yindun import APP_ROOT
from yindun.core.privacy_engine import PrivacyEngine
from yindun.core.secret_manager import SecretManager
# 判定"解析器到底有没有产出正文"必须走解析器自己的契约（PARSE_FAIL_PREFIXES /
# is_parse_failure / parse_failure_reason），KB 不再维护第二份前缀清单：解析器以后
# 新增失败提示（图片 OCR 缺失、音频缺 FunASR、PPT 损坏……）时 KB 自动跟上，
# 不会再把"一句提示语"当成正文入库。
from yindun.utils.document_parser import (
    extract_file_text,
    is_parse_failure,
    parse_failure_reason,
    describe_parse_error,
)


# 占位符解析正则：兼容新旧两套格式
#   旧格式 [PHONE_0]       → group(3) 为 None
#   新格式 [PHONE_0_a3f9]  → group(3) 为 nonce（含 4 位随机 nonce）
_PLACEHOLDER_RE = re.compile(r"\[([A-Z][A-Z0-9_]*)_(\d+)(?:_([a-z0-9]{4}))?\]")
# ★ 类型段必须允许下划线与数字：内核实体里有 MEDICAL_RECORD / IDCARD15 / PRIVATE_KEY /
#   ACCESS_TOKEN，插件补充识别的实体是 PLUGIN_*。旧正则 [A-Z]+ 匹配不到这些名字，后果是
#   检索时它们既不参与全局重编号、也不带隔离前缀 —— 多文档检索时可能把 A 文档的真实值
#   还原到 B 文档的同名占位符上（串值），KB_ 隔离形同虚设。


# ──────────────────────────────────────────
# 解析结果分类：把"解析器提示语"与真实正文分开
# ──────────────────────────────────────────
# 为什么需要：document_parser.extract_file_text 读不出内容时**不抛异常**，而是返回一句
# 提示文本（超限拒绝 / 图片缺 OCR / 音频缺 FunASR / PDF 无文字层……）。旧实现只挡了
# "[文档解析失败"，于是 .png/.pptx/.zip/.doc、超过大小上限的文件、纯图片 PDF 都会被
# 当成正文入库并计为"成功"——用户看到"入库完成"，向量库里其实只有那句提示语。
# 失败判定统一走 document_parser.is_parse_failure()（前缀清单的唯一出处）；这里只
# 负责把它翻成"给用户看的原因"，并补上解析器没说清时的格式提示。
_PARTIAL_NOTICE_RE = re.compile(
    r"\[(?:第 \d+ 页尺寸过大，已跳过 OCR"
    r"|本页为扫描图片[^\]]*"
    r"|本页文字层为空[^\]]*"
    r"|文档含 \d+ 张内嵌图片，未自动解析"
    r"|本页含 \d+ 张图片，未自动解析"
    r"|本文档部分页面通过 OCR 识别，可能存在识别误差)\]"
)
_MAX_NOTICE_ITEMS = 5

# 正文里出现上面这些提示 = 文档**部分内容**没进库（图片页 / 未解析的内嵌图片）。
# 这类文件仍算入库成功（确有正文），但必须把损失如实带出去，不能当作完整入库。

# 失败时的"为什么读不出"兜底提示：解析器给出的说明通常已经可行动，这里只在它过于笼统
# （或来自旧式/插件式提示）时补一句格式层面的建议。**不参与放行/拦截判定** —— 判定永远
# 以"解析器这次是否真的返回了正文"为准，解析器将来补上 OCR/PPT 能力后这些格式自动能进库。
_FORMAT_HINTS = {
    **{ext: "图片类文件需要 OCR 支持才能转成文本"
       for ext in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp")},
    **{ext: "演示文稿（PPT）的解析取决于内核解析器是否支持该格式"
       for ext in (".ppt", ".pptx")},
    **{ext: "压缩包不会被展开解析，请先解压后导入其中的文档"
       for ext in (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz")},
    **{ext: "音频文件需要 FunASR 转写组件才能得到文本"
       for ext in (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma")},
    ".doc": "旧版 .doc 是二进制格式，请另存为 .docx 后导入",
}

# 环境级向量化错误（Ollama 不可达 / embedding 模型缺失）的固定说法：命中前缀后，
# 批量入库不再对后续文件逐个重试（必然同样失败），但每个文件都记为 failed 并带同一原因。
_EMBED_ENV_ERROR_PREFIXES = ("向量化失败：本地 embedding 服务不可用",
                             "向量化失败：embedding 模型")


def _clip(text: str, limit: int) -> str:
    """截断长文本用于一句话汇总（界面 toast 长度有限）。"""
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "…"


def _format_hint(file_path: str) -> str:
    """按扩展名给一句"这类文件为什么读不出"的兜底提示（仅失败提示用）。"""
    ext = os.path.splitext(file_path or "")[1].lower()
    hint = _FORMAT_HINTS.get(ext)
    return f"{hint}；" if hint else ""


def _classify_extracted_text(text, file_path: str = "") -> tuple:
    """判断 extract_file_text 的返回值是真实正文还是解析器的提示语。

    返回 (is_content, reason)：
      · is_content=True  → 可以入库，reason 为空
      · is_content=False → reason 是给用户看的中文原因（解析器说明 + 必要的格式提示）
    """
    stripped = (text or "").strip()
    if not stripped:
        return False, f"{_format_hint(file_path)}没有解析出任何文本（空文件，或纯图片/加密文档）"
    if is_parse_failure(text):
        # 解析器的失败串本身就是"可行动说明"（请 另存为 / 解压 / 装 OCR …）：优先原样透出，
        # 避免 KB 再拼一句同义提示把真正的原因淹掉；只有它短得没说清下一步时才补格式提示
        reason = parse_failure_reason(text) or _clip(stripped, 300)
        # 前缀切掉后可能留下一个顿号/逗号（"[PDF 全文无可提取文字，…]"→"，OCR…"），清一下
        reason = reason.lstrip("，,。;；:：、 ")
        hint = _format_hint(file_path)
        return False, f"{hint}{reason}" if hint and len(reason) < 24 else reason
    return True, ""


def _collect_partial_notices(text: str) -> list:
    """收集"有内容没进库"的解析器提示（图片页 / 内嵌图片），去重并限量。"""
    seen = []
    for match in _PARTIAL_NOTICE_RE.finditer(text or ""):
        notice = match.group(0)
        if notice not in seen:
            seen.append(notice)
    if len(seen) > _MAX_NOTICE_ITEMS:
        extra = len(seen) - _MAX_NOTICE_ITEMS
        seen = seen[:_MAX_NOTICE_ITEMS] + [f"…另有 {extra} 处类似提示"]
    return seen


def _vectorize_error_message(exc: Exception, model: str) -> str:
    """把向量化异常翻译成用户能照着做的中文原因（保留原始错误便于排查）。"""
    raw = f"{type(exc).__name__}: {exc}"
    low = raw.lower()
    if "not found" in low and "pull" in low:
        reason = (f"向量化失败：embedding 模型 {model} 未就绪，"
                  f"请运行 ollama pull {model} 后重试")
    elif "connect" in low or "connection" in low:
        reason = ("向量化失败：本地 embedding 服务不可用（Ollama 未启动或不可达），"
                  "请确认 Ollama 已运行后重试")
    else:
        reason = f"向量化失败：写入向量库时出错（embedding 模型 {model}）"
    return f"{reason}；原始错误：{_clip(raw, 200)}"


class KnowledgeBase:
    """本地知识库 + 脱敏 RAG 引擎

    核心机制：
    - 入库脱敏：每个 chunk 在向量化前先经过 PrivacyEngine 脱敏
    - 映射表隔离：每个 chunk 的脱敏映射表独立存于 metadata（JSON 序列化）
    - 检索时全局重命名：search 返回时对多个 chunk 的占位符做全局重编号，避免冲突
    - 用后即焚：还原后映射表立即销毁
    """

    # ──────────────────────────────────────────
    # 配置常量
    # ──────────────────────────────────────────
    DEFAULT_EMBED_MODEL = "nomic-embed-text"
    DEFAULT_COLLECTION = "yindun_kb"
    CHUNK_SIZE = 500        # 每块字符数
    CHUNK_OVERLAP = 50      # 块间重叠字符数
    DEFAULT_TOP_K = 4       # 默认检索片段数

    def __init__(
        self,
        persist_dir: Optional[str] = None,
        embed_model: Optional[str] = None,
        ollama_base_url: str = None,
    ):
        """
        初始化知识库。

        参数：
            persist_dir: 向量库持久化目录，默认为 yindun/data/knowledge_base
            embed_model: embedding 模型名，默认 nomic-embed-text
            ollama_base_url: Ollama 服务地址
        """
        # 基础路径
        if persist_dir is None:
            persist_dir = os.path.join(str(APP_ROOT), "data", "knowledge_base")
        self.persist_dir = os.path.abspath(persist_dir)
        os.makedirs(self.persist_dir, exist_ok=True)

        self.embed_model = embed_model or self.DEFAULT_EMBED_MODEL
        self.ollama_base_url = ollama_base_url or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")

        # 脱敏引擎实例（入库和检索时复用）
        self.engine = PrivacyEngine()
        # 加密器：用于对 metadata 中的 mapping 整体加密（双层加密的外层）
        self._crypto = SecretManager.get_instance()

        # 实例级并发锁：保护所有 Chroma 读/写操作（add_texts / similarity_search /
        # collection.get/delete / _ensure_ready），入库线程与 Worker 共享实例时串行化。
        # 使用 RLock：add_document 内部会调用 _remove_by_source，get_stats 会调用
        # list_documents，可重入锁避免自死锁。
        self._lock = threading.RLock()

        # 懒加载：首次使用时才初始化向量库和 embedding
        self._embeddings = None
        self._vectorstore = None
        self._available: Optional[bool] = None  # None=未检测, True/False=已检测

    # ──────────────────────────────────────────
    # 可用性检测（优雅降级）
    # ──────────────────────────────────────────
    def _check_available(self) -> bool:
        """检测依赖是否就绪。结果缓存，避免重复检测。"""
        if self._available is not None:
            return self._available
        try:
            import chromadb  # noqa: F401
            from langchain_ollama import OllamaEmbeddings  # noqa: F401
            from langchain_chroma import Chroma  # noqa: F401
            from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: F401
            self._available = True
        except ImportError:
            self._available = False
        return self._available

    def is_available(self) -> bool:
        """对外暴露的可用性检查。"""
        return self._check_available()

    def embedding_ready(self, timeout: float = 1.5, cache_seconds: float = 30.0) -> bool:
        """本地 embedding 是否**真的能跑**（V3.6.12，组员评审 BUG-01）。

        为什么不能只看 `_check_available()`：那只检查 Python 依赖装没装。
        Ollama 没起 / 没拉 `nomic-embed-text` 时它照样返回 True，界面显示"就绪"、
        用户点添加也一直可点，直到向量化那一步才失败——而失败又被批量结果吞掉，
        于是变成"提示入库完成、库其实是空的"。

        这里真问一句 Ollama（`/api/tags`，1.5 秒超时、**绕过系统代理**）并核对模型名。
        结果做短缓存（默认 30 秒）：界面轮询/反复重绘时不会每次都打服务。
        """
        if not self._check_available():
            return False
        now = time.time()
        cached = getattr(self, "_embedding_ready_cache", None)
        if cached and now - cached[0] < cache_seconds:
            return cached[1]
        ready = False
        try:
            from yindun.app.llm_factory import detect_ollama_models_via_api
            models, _note = detect_ollama_models_via_api(self.ollama_base_url)
            wanted = str(self.embed_model or "").split(":")[0].lower()
            ready = any(str(name or "").split(":")[0].lower() == wanted for name in models)
        except Exception:
            ready = False
        self._embedding_ready_cache = (now, ready)
        return ready

    def _ensure_ready(self):
        """确保向量库和 embedding 已初始化。若依赖缺失则抛出 RuntimeError。"""
        if not self._check_available():
            raise RuntimeError(
                "知识库依赖未安装。请运行: pip install chromadb langchain-chroma "
                "langchain-ollama langchain-text-splitters"
            )
        if self._embeddings is None:
            from langchain_ollama import OllamaEmbeddings
            self._embeddings = OllamaEmbeddings(
                model=self.embed_model,
            )
        if self._vectorstore is None:
            from langchain_chroma import Chroma
            self._vectorstore = Chroma(
                collection_name=self.DEFAULT_COLLECTION,
                embedding_function=self._embeddings,
                persist_directory=self.persist_dir,
            )

    # ──────────────────────────────────────────
    # 1. 入库：文档 → 切块 → 脱敏 → 向量化 → 存储
    # ──────────────────────────────────────────
    # ──────────────────────────────────────────
    # 文本管线：整篇脱敏 → 切块 → 每块映射（与向量库解耦，可独立测试）
    # ──────────────────────────────────────────
    @classmethod
    def mask_and_split(cls, full_text: str, engine):
        """把一篇原文变成"可直接入库的脱敏分块"。

        顺序不可颠倒：**先整篇脱敏，再切块**。
        旧实现是"先切块、再逐块脱敏"，任何长于 CHUNK_SIZE 的实体（PEM 私钥、长连接串、
        大段 base64）会被切碎，碎片两边都匹配不到正则，于是明文碎片进了向量库。
        整篇脱敏后长实体已被单个占位符替换，再切块就不可能把它切碎。

        返回 (chunks, chunk_mappings, stats)：
          · chunks          已脱敏的文本块
          · chunk_mappings  每块只含"本块真的出现"的占位符映射（跨块不串值，体积也更小）
          · stats           整篇的脱敏类型统计
        """
        anon_full, full_mapping = engine.anonymize(full_text)
        stats = dict(engine.get_last_stats())
        from langchain_text_splitters import RecursiveCharacterTextSplitter   # 延迟导入：不拉重依赖
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=cls.CHUNK_SIZE,
            chunk_overlap=cls.CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", " ", ""],
        )
        chunks = splitter.split_text(anon_full)
        mappings = []
        for chunk_text in chunks:
            mappings.append({m.group(0): full_mapping[m.group(0)]
                             for m in _PLACEHOLDER_RE.finditer(chunk_text)
                             if m.group(0) in full_mapping})
        return chunks, mappings, stats

    def add_document(self, file_path: str) -> dict:
        """
        将一个文档加入知识库。

        流程：解析全文 → 切块 → 每块脱敏 → 向量化 → 存入 Chroma
        同名文档重复入库时会先删除旧数据。

        返回统计 dict：
            {"file": "xxx.pdf", "chunks": 12, "chars": 6000,
             "sensitive": {"NAME": 3, ...}, "warnings": ["[...]"]}
            warnings：解析器明确提示"有内容没解析"的地方（图片页/内嵌图片等），
                      非空表示这次入库**不完整**，调用方应如实展示。
        """
        with self._lock:
            self._ensure_ready()

            file_name = os.path.basename(file_path)
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"文档不存在: {file_path}")
            # 目录不做递归：这里明确说清楚，避免解析器抛出的 PermissionError 让人看不懂
            if os.path.isdir(file_path):
                raise RuntimeError(
                    f"这是目录，不是文件 [文件：{file_name}]：知识库暂不支持目录递归导入，"
                    "请进入目录后逐个选择要入库的文件")

            # 1. 解析全文
            full_text = extract_file_text(file_path)
            # ★ 解析器读不出内容时返回的是"一句提示语"而不是空串/异常，必须按失败处理，
            #   否则提示语会被当成正文入库并计为成功（判定走 document_parser.is_parse_failure）
            is_content, reason = _classify_extracted_text(full_text, file_path)
            if not is_content:
                raise RuntimeError(f"{reason} [文件：{file_name}]")
            warnings = _collect_partial_notices(full_text)

            # 2. ★ 整篇脱敏 → 切块（顺序不可颠倒，见 mask_and_split 的说明）
            chunks, chunk_mappings, total_sensitive = self.mask_and_split(full_text, self.engine)
            if not chunks:
                raise RuntimeError(f"切块后无有效内容: {file_name}")

            # 3. 准备入库数据
            #    chunk id 用文件名（而非完整路径）派生：避免把本机目录结构写进向量库。
            #    这里的哈希只用来生成稳定的 chunk id，不承担任何完整性校验（校验另有审计哈希链）。
            documents = []
            metadatas = []
            ids = []
            file_hash = hashlib.sha256(file_name.encode("utf-8")).hexdigest()[:8]

            for idx, chunk_text in enumerate(chunks):
                chunk_mapping = chunk_mappings[idx]
                chunk_id = f"{file_hash}_{idx:04d}"
                documents.append(chunk_text)
                # 把整个 mapping dict 序列化后再整体加密，metadata 只存密文
                mapping_json = json.dumps(chunk_mapping, ensure_ascii=False)
                encrypted_mapping = self._crypto.encrypt(mapping_json)
                metadatas.append({
                    "source": file_name,
                    # ★ 不写 file_path：向量库属落盘面，完整路径会泄露本机目录结构，
                    #   而它从来没被读过（列表/删除都只按 source 文件名）。
                    "chunk_index": idx,
                    "total_chunks": len(chunks),
                    "added_at": int(time.time()),
                    "mapping_encrypted": encrypted_mapping,
                    "has_mapping": bool(chunk_mapping),  # 标记是否有敏感数据，便于检索时快速判断
                })
                ids.append(chunk_id)

            # 4. 向量化并存储（批量写入）
            #    embedding 失败要给出可读原因（Ollama 没起 / 模型没拉 / 其它），并带上原始错误
            try:
                self._vectorstore.add_texts(texts=documents, metadatas=metadatas, ids=ids)
            except Exception as exc:
                raise RuntimeError(_vectorize_error_message(exc, self.embed_model)) from exc

            # 5. 覆盖语义：写成功之后才清理旧版本残留 —— 顺序不可颠倒。
            #    旧实现"先删旧、再写入"，一旦向量化失败（例如 Ollama 中途掉线），
            #    同名文档的**旧版本也会一起消失**：用户重导一次反而把库里那份弄丢了。
            #    先写后删：新块的 id 与旧块相同（同名同下标）直接 upsert，只有旧版本多出来的
            #    尾部块需要删掉；同名文档依旧只保留一份，不会产生重复块。
            new_ids = set(ids)
            try:
                stale_ids = [i for i in self._ids_by_source(file_name) if i not in new_ids]
                if stale_ids:
                    self._vectorstore._collection.delete(ids=stale_ids)
            except Exception as exc:
                # 旧数据残留必须可见：本次新内容已入库，但库里可能留下旧版本的冗余块
                raise RuntimeError(
                    f"新内容已写入，但清理同名文档旧数据失败 [文件：{file_name}]："
                    f"{type(exc).__name__}: {_clip(exc, 200)}") from exc

            return {
                "file": file_name,
                "chunks": len(chunks),
                "chars": len(full_text),
                "sensitive": total_sensitive,
                "warnings": warnings,
            }

    def add_documents(self, file_paths, progress_callback=None) -> dict:
        """
        批量入库多个文档。

        参数：
            file_paths: 文件路径列表
            progress_callback: 回调函数 callback(current, total, file_name, status, detail)
                              status 取值："processing" / "success" / "failed"

        返回统计 dict：
            {"total": 3, "success": 2, "failed": 1, "details": [...], "total_chunks": 12,
             "summary": "入库完成：成功 2/3，失败 1：b.png（…原因…）"}

        ★ 失败必须留在面上：details 逐条给出每个文件的结果与原因（success 项带 warnings），
          summary 是一句话汇总（含失败文件名与原因），供界面直接展示——不允许"只说完成、
          不说哪些没进"。环境级错误（Ollama 不可达/模型缺失）会停止后续尝试，但每个文件
          仍逐条记为 failed 并带同一原因，不静默跳过任何一条。
        """
        total = len(file_paths)
        details = []
        success_count = 0
        total_chunks = 0
        env_fail_reason = None     # 环境级失败原因：命中后后续文件不再逐个尝试
        imported_names = {}        # 同批次内同名文件：后一条会覆盖前一条，需如实备注

        for i, path in enumerate(file_paths, 1):
            fname = os.path.basename(path)

            if env_fail_reason is not None:
                details.append({"file": fname, "status": "failed", "error": env_fail_reason})
                if progress_callback:
                    try:
                        progress_callback(i, total, fname, "failed", env_fail_reason)
                    except Exception:
                        pass
                continue

            # 通知开始处理
            if progress_callback:
                try:
                    progress_callback(i, total, fname, "processing", "")
                except Exception:
                    pass

            try:
                stats = self.add_document(path)
                success_count += 1
                total_chunks += stats.get("chunks", 0)
                detail = {
                    "file": fname, "status": "success",
                    "chunks": stats.get("chunks", 0),
                    "sensitive": stats.get("sensitive", {}),
                }
                warnings = stats.get("warnings") or []
                if warnings:
                    detail["warnings"] = warnings
                # 同一批次里同名文件会被后来者覆盖：库中只保留最后一份，前一条必须挂备注，
                # 否则"成功 2 条"与库中"1 篇文档"看起来就是自相矛盾
                prev = imported_names.get(fname)
                if prev is not None:
                    prev.setdefault("notes", []).append(
                        "同批次里出现了同名文件，本条已被本次导入里的同名文件覆盖（库中只保留最后一份）")
                imported_names[fname] = detail
                details.append(detail)
                if progress_callback:
                    try:
                        progress_callback(i, total, fname, "success",
                                          f"{stats.get('chunks', 0)} 个片段" + (
                                              f"；{len(warnings)} 处内容未解析" if warnings else ""))
                    except Exception:
                        pass
            except Exception as e:
                err_msg = self._describe_failure(e, path, fname)
                details.append({"file": fname, "status": "failed", "error": err_msg})
                # 环境级向量化错误（Ollama 不可达 / 模型缺失）后续文件必然同样失败：
                # 停止逐个尝试以免用户等 N 次超时，但仍逐条记 failed（见上方循环开头的分支）
                if any(p in err_msg for p in _EMBED_ENV_ERROR_PREFIXES):
                    env_fail_reason = err_msg + "（同一环境问题，后续文件不再逐个尝试）"
                if progress_callback:
                    try:
                        progress_callback(i, total, fname, "failed", err_msg)
                    except Exception:
                        pass

        return {
            "total": total,
            "success": success_count,
            "failed": total - success_count,
            "details": details,
            "total_chunks": total_chunks,
            "summary": self._format_batch_summary(total, success_count, details),
        }

    @staticmethod
    def _describe_failure(exc: Exception, file_path: str, file_name: str = "") -> str:
        """逐文件失败 → "人话 + 原始异常名"（details[].error 的最终文案）。

        解析层已经把常见底层异常（假的/损坏的 Office 容器、加密文档、坏 PDF）在
        源头翻成可执行说明；这里再兜一层，覆盖"异常直接从解析器/插件抛到这里"的情况，
        保证界面不会只看到 `PackageNotFoundError: Package not found at …` 这种原文。
        已翻过的文案原样保留（`describe_parse_error` 以特征词判断），不会叠成
        "入库失败：…入库失败：…"。
        """
        explained = describe_parse_error(exc, file_path)
        if explained:
            if file_name and file_name not in explained:
                return f"{explained} [文件：{file_name}]"
            return explained
        return f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _format_batch_summary(total: int, success: int, details: list) -> str:
        """一句话汇总（含失败文件名与原因、部分未解析提醒），供界面直接展示。"""
        failed = [d for d in details if d.get("status") != "success"]
        parts = [f"入库完成：成功 {success}/{total}"]
        if failed:
            parts.append(f"，失败 {len(failed)}：")
            items = [f"{d.get('file')}（{_clip(d.get('error') or '原因未知', 80)}）"
                     for d in failed[:3]]
            parts.append("；".join(items))
            if len(failed) > 3:
                parts.append(f"；另有 {len(failed) - 3} 个失败，见 details")
        warned = [d for d in details if d.get("warnings")]
        if warned:
            parts.append(f"；{len(warned)} 个文件有部分内容未解析（见 details[].warnings）")
        return "".join(parts)

    # ──────────────────────────────────────────
    # 2. 检索：问题脱敏 → 向量检索 → 返回脱敏片段 + 全局映射表
    # ──────────────────────────────────────────
    def search(self, query: str, top_k: int = None) -> dict:
        """
        在知识库中语义检索。

        流程：问题脱敏 → 向量化 → Chroma 检索 top-K → 占位符全局重命名

        返回 dict：
            {
                "chunks": [{"content": "脱敏片段", "source": "文件名", "score": 0.9}, ...],
                "global_mapping": {"[NAME_0]": "张伟", "[PHONE_1]": "138...", ...},
                "query_anonymized": "脱敏后的问题",
            }
        """
        with self._lock:
            self._ensure_ready()
            if top_k is None:
                top_k = self.DEFAULT_TOP_K

            # 1. 问题脱敏（避免用户的敏感信息进入向量库查询）
            anon_query, query_mapping = self.engine.anonymize(query)

            # 2. 向量检索
            results = self._vectorstore.similarity_search_with_score(anon_query, k=top_k)

            if not results:
                return {"chunks": [], "global_mapping": {}, "query_anonymized": anon_query}

            # 3. 占位符全局重命名 + 合并映射表
            #    不同 chunk 独立脱敏时，可能都产生 [NAME_0]，但对应不同真实值
            #    这里统一重编号，避免还原时冲突
            global_mapping = {}
            counters = {}  # {TYPE: 当前全局编号}
            output_chunks = []

            # 把问题本身的脱敏映射也并入全局映射
            for placeholder, real_value in query_mapping.items():
                global_mapping[placeholder] = real_value
                match = _PLACEHOLDER_RE.match(placeholder)
                if match:
                    ptype = match.group(1)
                    num = int(match.group(2))
                    counters[ptype] = max(counters.get(ptype, 0), num + 1)

            for doc, score in results:
                content = doc.page_content
                meta = doc.metadata or {}

                # 还原 chunk 的 mapping：优先读加密字段，兼容旧版明文字段
                chunk_mapping = {}
                raw_encrypted = meta.get("mapping_encrypted", "")
                if raw_encrypted:
                    # 新版：密文 mapping，先解密再反序列化
                    try:
                        mapping_json = self._crypto.decrypt(raw_encrypted)
                        chunk_mapping = json.loads(mapping_json)
                    except Exception as e:
                        # 解密失败必须可见（安全机制降级不能无告警）：
                        # 跳过该 chunk 映射，但打印告警
                        print(f"[KnowledgeBase] chunk mapping 解密失败，跳过该 chunk 映射：{type(e).__name__}: {e}")
                        chunk_mapping = {}
                elif meta.get("mapping"):
                    # 兼容旧数据：metadata 里还有旧的明文 "mapping" 字段
                    try:
                        chunk_mapping = json.loads(meta["mapping"])
                    except (json.JSONDecodeError, TypeError):
                        chunk_mapping = {}

                # 对该 chunk 的占位符做全局重命名
                for placeholder, real_value in chunk_mapping.items():
                    if placeholder not in content:
                        continue
                    # 解析 [TYPE_N]（旧）/[TYPE_N_nonce]（新）
                    match = _PLACEHOLDER_RE.match(placeholder)
                    if match:
                        ptype = match.group(1)
                        gidx = counters.get(ptype, 0)
                        nonce = match.group(3)
                        # 重编号时保留 nonce（新格式），旧格式保持原样
                        new_placeholder = f"[{ptype}_{gidx}]" if nonce is None else f"[{ptype}_{gidx}_{nonce}]"
                        content = content.replace(placeholder, new_placeholder)
                        global_mapping[new_placeholder] = real_value
                        counters[ptype] = gidx + 1
                    else:
                        # 非标准占位符，直接保留
                        global_mapping[placeholder] = real_value

                output_chunks.append({
                    "content": content,
                    "source": meta.get("source", "未知"),
                    "chunk_index": meta.get("chunk_index", -1),
                    "score": float(score),
                })

            return {
                "chunks": output_chunks,
                "global_mapping": global_mapping,
                "query_anonymized": anon_query,
            }

    # ──────────────────────────────────────────
    # 3. 还原：用映射表把 LLM 回答中的占位符换回真实值
    # ──────────────────────────────────────────
    @staticmethod
    def restore(text: str, mapping: dict) -> str:
        """
        将 LLM 回答中的占位符替换回真实敏感数据。

        参数：
            text: LLM 回答（含占位符）
            mapping: search 返回的 global_mapping

        返回：还原后的文本。
        """
        if not text or not mapping:
            return text
        # ★ 修复：mapping 的值是 Fernet 密文（由 PrivacyEngine 加密存储），
        #   旧实现直接 str.replace 会把密文当明文写回，界面出现 "gAAAAA..." 垃圾。
        #   这里复用隐私引擎已验证的还原语义：解密后替换；解不开则保留占位符（绝不写密文）。
        try:
            from yindun.core.privacy_engine import PrivacyEngine
            return PrivacyEngine().deanonymize(text, mapping, strict=True)
        except Exception as exc:
            print(f"[KnowledgeBase] 调用隐私引擎还原失败，退回逐项替换（兼容旧版明文映射）：{exc}")
            restored = text
            for placeholder, original_value in mapping.items():
                if isinstance(original_value, str):
                    restored = restored.replace(placeholder, original_value)
            return restored

    # ──────────────────────────────────────────
    # 4. 列出已入库文档
    # ──────────────────────────────────────────
    def list_documents(self) -> list:
        """列出知识库中所有文档的摘要信息。"""
        with self._lock:
            self._ensure_ready()
            from langchain_chroma import Chroma

            # 通过底层 collection 获取所有 metadata
            collection = self._vectorstore._collection
            all_data = collection.get(include=["metadatas"])

            if not all_data or not all_data.get("metadatas"):
                return []

            # 按文件名聚合
            doc_stats = {}
            for meta in all_data["metadatas"]:
                if not meta:
                    continue
                source = meta.get("source", "未知")
                if source not in doc_stats:
                    doc_stats[source] = {
                        "file": source,
                        "chunks": 0,
                        "added_at": meta.get("added_at", 0),
                    }
                doc_stats[source]["chunks"] += 1
                # 取最早的入库时间
                added = meta.get("added_at", 0)
                if added and (not doc_stats[source]["added_at"] or added < doc_stats[source]["added_at"]):
                    doc_stats[source]["added_at"] = added

            return list(doc_stats.values())

    # ──────────────────────────────────────────
    # 5. 删除文档
    # ──────────────────────────────────────────
    def remove_document(self, file_name: str) -> bool:
        """从知识库删除指定文档的所有 chunk。返回是否删除了内容。"""
        with self._lock:
            self._ensure_ready()
            return self._remove_by_source(file_name)

    def _ids_by_source(self, file_name: str) -> list:
        """内部方法：查同名文档当前的 chunk id 列表（覆盖导入时用来清理旧版本的尾部块）。

        读失败直接抛出（由调用方翻成可读错误）：静默返回空表会让旧数据悄悄留着。
        """
        collection = self._vectorstore._collection
        results = collection.get(where={"source": file_name}, include=[])
        return list(results.get("ids") or []) if results else []

    def _remove_by_source(self, file_name: str) -> bool:
        """内部方法：按 source metadata 删除指定文档的所有向量。"""
        try:
            collection = self._vectorstore._collection
            # 查找该文件的所有 chunk id
            results = collection.get(
                where={"source": file_name},
                include=[]
            )
            if not results or not results.get("ids"):
                return False
            collection.delete(ids=results["ids"])
            return True
        except Exception as e:
            # 删除（写盘）失败必须可见：返回失败标志并打印，不能静默吞掉
            print(f"[KnowledgeBase] 删除文档 {file_name} 失败：{type(e).__name__}: {e}")
            return False

    # ──────────────────────────────────────────
    # 6. 清空知识库
    # ──────────────────────────────────────────
    def clear(self) -> bool:
        """清空整个知识库。"""
        with self._lock:
            self._ensure_ready()
            try:
                collection = self._vectorstore._collection
                all_data = collection.get(include=[])
                if all_data and all_data.get("ids"):
                    collection.delete(ids=all_data["ids"])
                return True
            except Exception as e:
                # 清空（写盘）失败必须可见：返回失败标志并打印，不能静默吞掉
                print(f"[KnowledgeBase] 清空知识库失败：{type(e).__name__}: {e}")
                return False

    # ──────────────────────────────────────────
    # 7. 获取知识库统计
    # ──────────────────────────────────────────
    def get_stats(self) -> dict:
        """返回知识库总体统计信息。"""
        with self._lock:
            self._ensure_ready()
            try:
                collection = self._vectorstore._collection
                count = collection.count()
                docs = self.list_documents()
                return {
                    "total_chunks": count,
                    "total_documents": len(docs),
                    "documents": docs,
                    "persist_dir": self.persist_dir,
                    "embed_model": self.embed_model,
                }
            except Exception as e:
                return {"error": str(e)}
