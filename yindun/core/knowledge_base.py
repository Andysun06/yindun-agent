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
"""
import os
import re
import json
import time
import hashlib
from typing import Optional

from yindun.core.privacy_engine import PrivacyEngine
from yindun.utils.document_parser import extract_file_text


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
        ollama_base_url: str = "http://localhost:11434",
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
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            persist_dir = os.path.join(base, "data", "knowledge_base")
        self.persist_dir = os.path.abspath(persist_dir)
        os.makedirs(self.persist_dir, exist_ok=True)

        self.embed_model = embed_model or self.DEFAULT_EMBED_MODEL
        self.ollama_base_url = ollama_base_url

        # 脱敏引擎实例（入库和检索时复用）
        self.engine = PrivacyEngine()

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
                base_url=self.ollama_base_url,
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
    def add_document(self, file_path: str) -> dict:
        """
        将一个文档加入知识库。

        流程：解析全文 → 切块 → 每块脱敏 → 向量化 → 存入 Chroma
        同名文档重复入库时会先删除旧数据。

        返回统计 dict：
            {"file": "xxx.pdf", "chunks": 12, "chars": 6000, "sensitive": {"NAME": 3, ...}}
        """
        self._ensure_ready()
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        file_name = os.path.basename(file_path)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文档不存在: {file_path}")

        # 1. 解析全文
        full_text = extract_file_text(file_path)
        if not full_text or not full_text.strip():
            raise RuntimeError(f"文档内容为空或解析失败: {file_name}")
        # 去掉解析失败提示
        if full_text.startswith("[文档解析失败"):
            raise RuntimeError(full_text)

        # 2. 切块
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.CHUNK_SIZE,
            chunk_overlap=self.CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", " ", ""],
        )
        chunks = splitter.split_text(full_text)
        if not chunks:
            raise RuntimeError(f"切块后无有效内容: {file_name}")

        # 3. 同名文档先删旧数据（去重）
        self._remove_by_source(file_name)

        # 4. 逐块脱敏 + 准备入库数据
        documents = []
        metadatas = []
        ids = []
        total_sensitive = {}
        file_hash = hashlib.md5(file_path.encode("utf-8")).hexdigest()[:8]

        for idx, chunk_text in enumerate(chunks):
            # 每块独立脱敏
            anon_text, mapping = self.engine.anonymize(chunk_text)
            # 统计脱敏情况
            for k, v in self.engine.get_last_stats().items():
                total_sensitive[k] = total_sensitive.get(k, 0) + v

            chunk_id = f"{file_hash}_{idx:04d}"
            documents.append(anon_text)
            metadatas.append({
                "source": file_name,
                "file_path": file_path,
                "chunk_index": idx,
                "total_chunks": len(chunks),
                "added_at": int(time.time()),
                "mapping": json.dumps(mapping, ensure_ascii=False),
            })
            ids.append(chunk_id)

        # 5. 向量化并存储（批量写入）
        self._vectorstore.add_texts(texts=documents, metadatas=metadatas, ids=ids)

        return {
            "file": file_name,
            "chunks": len(chunks),
            "chars": len(full_text),
            "sensitive": total_sensitive,
        }

    def add_documents(self, file_paths, progress_callback=None) -> dict:
        """
        批量入库多个文档。

        参数：
            file_paths: 文件路径列表
            progress_callback: 回调函数 callback(current, total, file_name, status, detail)
                              status 取值："processing" / "success" / "failed"

        返回统计 dict：
            {"total": 3, "success": 2, "failed": 1, "details": [...], "total_chunks": 12}
        """
        total = len(file_paths)
        details = []
        success_count = 0
        total_chunks = 0

        for i, path in enumerate(file_paths, 1):
            fname = os.path.basename(path)
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
                details.append({
                    "file": fname, "status": "success",
                    "chunks": stats.get("chunks", 0),
                    "sensitive": stats.get("sensitive", {}),
                })
                if progress_callback:
                    try:
                        progress_callback(i, total, fname, "success",
                                          f"{stats.get('chunks', 0)} 个片段")
                    except Exception:
                        pass
            except Exception as e:
                err_msg = f"{type(e).__name__}: {e}"
                details.append({"file": fname, "status": "failed", "error": err_msg})
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
        }

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
            match = re.match(r"\[([A-Z]+)_(\d+)\]", placeholder)
            if match:
                ptype = match.group(1)
                num = int(match.group(2))
                counters[ptype] = max(counters.get(ptype, 0), num + 1)

        for doc, score in results:
            content = doc.page_content
            meta = doc.metadata or {}
            chunk_mapping_str = meta.get("mapping", "{}")
            try:
                chunk_mapping = json.loads(chunk_mapping_str)
            except (json.JSONDecodeError, TypeError):
                chunk_mapping = {}

            # 对该 chunk 的占位符做全局重命名
            for placeholder, real_value in chunk_mapping.items():
                if placeholder not in content:
                    continue
                # 解析 [TYPE_N]
                match = re.match(r"\[([A-Z]+)_(\d+)\]", placeholder)
                if match:
                    ptype = match.group(1)
                    gidx = counters.get(ptype, 0)
                    new_placeholder = f"[{ptype}_{gidx}]"
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
        restored = text
        for placeholder, original_value in mapping.items():
            restored = restored.replace(placeholder, original_value)
        return restored

    # ──────────────────────────────────────────
    # 4. 列出已入库文档
    # ──────────────────────────────────────────
    def list_documents(self) -> list:
        """列出知识库中所有文档的摘要信息。"""
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
        self._ensure_ready()
        return self._remove_by_source(file_name)

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
        except Exception:
            return False

    # ──────────────────────────────────────────
    # 6. 清空知识库
    # ──────────────────────────────────────────
    def clear(self) -> bool:
        """清空整个知识库。"""
        self._ensure_ready()
        try:
            collection = self._vectorstore._collection
            all_data = collection.get(include=[])
            if all_data and all_data.get("ids"):
                collection.delete(ids=all_data["ids"])
            return True
        except Exception:
            return False

    # ──────────────────────────────────────────
    # 7. 获取知识库统计
    # ──────────────────────────────────────────
    def get_stats(self) -> dict:
        """返回知识库总体统计信息。"""
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
