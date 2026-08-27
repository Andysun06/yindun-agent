# -*- coding: utf-8 -*-
"""T9 并发验证：双线程 _save_global_config 无 lost-update + knowledge_base 并发入库/检索"""
import sys
import os
import json
import threading
import tempfile
from pathlib import Path

sys.path.insert(0, r"d:\庆园杯\3.14\yindun-agent")

# ── 1. main_window._save_global_config 并发写锁 ──
import yindun.gui.main_window as mw


class Dummy:
    pass


d = Dummy()
d._settings = {"base": True}
d._config_file = Path(tempfile.mkdtemp()) / "global_config.json"

barrier = threading.Barrier(2)


def writer_a():
    barrier.wait()
    for i in range(50):
        d._settings["alpha"] = "A" + str(i)
        mw.MainWindow._save_global_config(d)


def writer_b():
    barrier.wait()
    for i in range(50):
        d._settings["beta"] = "B" + str(i)
        mw.MainWindow._save_global_config(d)


ta = threading.Thread(target=writer_a)
tb = threading.Thread(target=writer_b)
ta.start(); tb.start(); ta.join(); tb.join()

with open(d._config_file, encoding="utf-8") as f:
    data = json.load(f)  # 解析失败即 JSON 损坏
print("saved file fields:", sorted(data.keys()))
assert "alpha" in data and "beta" in data and "base" in data, "lost-update detected!"
print("PASS: main_window 并发写锁 -> 文件 JSON 完整，无 lost-update")

# ── 2. knowledge_base 并发入库 + 检索（mock vectorstore，无外部依赖） ──
from yindun.core.knowledge_base import KnowledgeBase


class FakeCollection:
    def __init__(self):
        self.data = {}
        self.lock = threading.Lock()

    def get(self, where=None, include=None, ids=None):
        with self.lock:
            if where and "source" in where:
                return {"ids": [i for i in self.data if self.data[i][0] == where["source"]]}
            return {"ids": list(self.data.keys()),
                    "metadatas": [m for _, m in self.data.values()]}

    def delete(self, ids):
        with self.lock:
            for i in ids:
                self.data.pop(i, None)

    def count(self):
        with self.lock:
            return len(self.data)


class FakeVS:
    def __init__(self):
        self._collection = FakeCollection()

    def add_texts(self, texts, metadatas, ids):
        with self._collection.lock:
            for t, m, i in zip(texts, metadatas, ids):
                self._collection.data[i] = (m.get("source"), m)

    def similarity_search_with_score(self, query, k=4):
        with self._collection.lock:
            items = list(self._collection.data.values())
            return [(_Doc(m["source"], m), 1.0) for _, m in items[:k]]


class _Doc:
    def __init__(self, content, meta):
        self.page_content = content
        self.metadata = meta


kb = KnowledgeBase(persist_dir=tempfile.mkdtemp())
kb._available = True
kb._embeddings = object()
kb._vectorstore = FakeVS()

tmp = tempfile.mkdtemp()
p1 = os.path.join(tmp, "a.txt")
p2 = os.path.join(tmp, "b.txt")
with open(p1, "w", encoding="utf-8") as f:
    f.write("甲方电话 13812345678 姓名 张伟")
with open(p2, "w", encoding="utf-8") as f:
    f.write("乙方电话 13912345678 姓名 李娜")

errors = []


def worker_add(path):
    try:
        for _ in range(5):
            kb.add_document(path)
    except Exception as e:
        errors.append(("add", repr(e)))


def worker_search():
    try:
        for _ in range(5):
            kb.search("张伟")
    except Exception as e:
        errors.append(("search", repr(e)))


ths = [
    threading.Thread(target=worker_add, args=(p1,)),
    threading.Thread(target=worker_add, args=(p2,)),
    threading.Thread(target=worker_search),
    threading.Thread(target=worker_search),
]
for t in ths:
    t.start()
for t in ths:
    t.join()

print("kb errors:", errors)
assert not errors, "knowledge_base 并发异常!"
stats = kb.get_stats()
print("PASS: knowledge_base 并发入库+检索无异常，total_chunks:", stats.get("total_chunks"))
print("ALL T9 CHECKS PASSED")
