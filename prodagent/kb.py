# M7 知识库与长期记忆：本地确定性检索，不依赖任何外部服务。
#
# 设计边界（诚实声明）：
# - 检索是 BM25 词频模型 + 中文字符 bigram 分词，不是向量语义检索；
#   对同义改写（"退款周期" vs "多久到账"）没有泛化能力。
#   接真实模型后可平滑升级为 embedding 检索，接口保持不变。
# - 索引在进程内缓存，文档增删后全量重建（知识库 < 千级文档时
#   重建成本可忽略；更大规模需要引入持久化倒排索引）。
# - 记忆按 session_id 隔离，会话重置（reset）不删除长期记忆；
#   命中权重 = 分词集合的重叠大小，纯确定性，评测可复现。
"""M7：BM25 知识库 + 长期记忆评分（纯本地，零外部依赖）。"""

from __future__ import annotations

import math
import re
from typing import Optional

_ASCII_RE = re.compile(r"[A-Za-z0-9]+")
_HAN_RE = re.compile(r"[\u4e00-\u9fff]")

_MAX_CHUNK_CHARS = 400
_K1 = 1.5   # BM25 词频饱和参数
_B = 0.75   # BM25 长度归一参数


def chunk_text(content: str) -> list[str]:
    """空行分段累积成 ≤400 字的块；短文返回单块。确定性，评测可复现。"""
    paras: list[str] = []
    for raw in content.split("\n"):
        p = raw.strip()
        if p:
            paras.append(p)
    chunks: list[str] = []
    buf = ""
    for p in paras:
        while len(p) > _MAX_CHUNK_CHARS:      # 单段超长：硬切
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(p[:_MAX_CHUNK_CHARS])
            p = p[_MAX_CHUNK_CHARS:]
        if not p:
            continue
        if buf and len(buf) + 1 + len(p) > _MAX_CHUNK_CHARS:
            chunks.append(buf)
            buf = p
        else:
            buf = (buf + "\n" + p) if buf else p
    if buf:
        chunks.append(buf)
    if not chunks:
        chunks = [content.strip()[:_MAX_CHUNK_CHARS]]
    return chunks


def tokenize(text: str) -> list[str]:
    """ASCII 词小写 + 中文 bigram；单字中文不成 token（bigram 模型）。"""
    tokens = [w.lower() for w in _ASCII_RE.findall(text)]
    han = "".join(_HAN_RE.findall(text))
    for i in range(len(han) - 1):
        tokens.append(han[i : i + 2])
    return tokens


def memory_score(query: str, content: str) -> int:
    """长期记忆命中权重 = 查询与记忆内容的 token 集合交集大小。"""
    q = set(tokenize(query))
    if not q:
        return 0
    return len(q & set(tokenize(content)))


class KnowledgeBase:
    """BM25 检索。存储层是 Store（SQLite kb_docs/kb_chunks），索引进程内缓存。"""

    def __init__(self, store):
        self._store = store
        self._entries: list[dict] = []   # [{doc_id,title,seq,chunk,tf,len}]
        self._df: dict[str, int] = {}
        self._avgdl: float = 0.0
        self._built = False

    # ---- 管理面 ----
    def add_doc(self, title: str, content: str, source: Optional[str] = None) -> tuple[int, int]:
        chunks = chunk_text(content)
        doc_id = self._store.add_kb_doc(title=title, source=source)
        self._store.add_kb_chunks(doc_id, chunks)
        self._built = False  # 增删后失效，检索时惰性重建
        return doc_id, len(chunks)

    def delete_doc(self, doc_id: int) -> bool:
        ok = self._store.delete_kb_doc(doc_id)
        if ok:
            self._built = False
        return ok

    def list_docs(self) -> list[dict]:
        return self._store.list_kb_docs()

    def stats(self) -> dict:
        docs = self.list_docs()
        chunks = sum(int(d.get("chunk_count") or 0) for d in docs)
        return {"docs": len(docs), "chunks": chunks}

    # ---- 检索面 ----
    def search(self, query: str, k: int = 3) -> list[dict]:
        self._ensure()
        q = tokenize(query)
        if not q or not self._entries:
            return []
        n = len(self._entries)
        scored: list[tuple[float, dict]] = []
        for e in self._entries:
            s = 0.0
            dl = e["len"] or 1
            for t in q:
                tf = e["tf"].get(t, 0)
                if not tf:
                    continue
                df = self._df.get(t, 0)
                idf = math.log((n - df + 0.5) / (df + 0.5) + 1.0)
                s += idf * (tf * (_K1 + 1)) / (tf + _K1 * (1 - _B + _B * dl / self._avgdl))
            if s > 0:
                scored.append((s, e))
        scored.sort(key=lambda x: (-x[0], x[1]["doc_id"], x[1]["seq"]))
        return [
            {
                "doc_id": e["doc_id"],
                "title": e["title"],
                "seq": e["seq"],
                "score": round(s, 3),
                "chunk": e["chunk"],
            }
            for s, e in scored[: max(1, k)]
        ]

    # ---- 内部 ----
    def _ensure(self) -> None:
        if self._built:
            return
        entries: list[dict] = []
        df: dict[str, int] = {}
        for doc_id, seq, chunk in self._store.kb_chunks_all():
            tokens = tokenize(chunk)
            tf: dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            for t in tf:
                df[t] = df.get(t, 0) + 1
            entries.append(
                {"doc_id": doc_id, "seq": seq, "chunk": chunk, "tf": tf, "len": len(tokens), "title": ""}
            )
        # 补标题（doc_id → title）
        title_map = {d["id"]: d["title"] for d in self._store.list_kb_docs()}
        for e in entries:
            e["title"] = title_map.get(e["doc_id"], "")
        total = sum(e["len"] for e in entries)
        self._entries = entries
        self._df = df
        self._avgdl = (total / len(entries)) if entries else 0.0
        self._built = True
