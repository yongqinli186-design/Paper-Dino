"""轻量本地 RAG：PyPDFLoader + RecursiveCharacterTextSplitter + FAISS。

设计原则（对齐项目约束）：
1. 不引入 ChromaDB；只用 FAISS 的内存索引，按文件 path+mtime 做缓存。
2. 嵌入优先用 HuggingFace 的 all-MiniLM-L6-v2（sentence-transformers）；
   维度/网络不通时**自动降级**为内置的哈希 n-gram 嵌入（纯 Python，零依赖），
   保证离线也永远有结果，而不是让页面报错。
3. 返回给大模型的每一段文本都带 `[来源：文件名 - 第X页]` 元数据，便于页码溯源。

对外接口：
    rag_status() -> dict                        当前嵌入后端 / 索引缓存概况
    build_index(path) -> (faiss_index, docs)    构建或复用缓存
    similarity_search(paths, query, k=4) -> list[dict]
    format_context(hits, max_chars=6000) -> str 带来源前缀的上下文
    rag_context_for(paths, query, ...) -> str   一步到位
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import time
from pathlib import Path

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
VECTOR_DIM = 256

INDEX_DIR = Path(__file__).with_name("rag_index")
INDEX_DIR.mkdir(exist_ok=True)

_EMBEDDER = None
_EMBEDDER_KIND = "unloaded"
_EMBEDDER_ERROR = ""
_INDEX_CACHE: dict[str, dict] = {}
_FALLBACK_CACHE: dict[str, dict] = {}

_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


# ---------------------------------------------------------------------------
# 嵌入：HuggingFace 优先，失败退化为哈希词袋
# ---------------------------------------------------------------------------
def _hash_vector(text: str, dim: int = VECTOR_DIM) -> list[float]:
    """字符 3-gram + 词哈希的固定维度向量（确定性，无需任何模型）。"""
    vec = [0.0] * dim
    low = (text or "").lower()
    if not low.strip():
        return vec
    tokens = _WORD_RE.findall(low) + list(_CJK_RE.findall(low))
    for tok in tokens:
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest()[:8], 16)
        vec[h % dim] += 1.0
    for i in range(len(low) - 2):
        gram = low[i : i + 3]
        h = int(hashlib.md5(gram.encode("utf-8")).hexdigest()[:8], 16)
        vec[h % dim] += 0.35
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


class HashEmbeddings:
    """降级用的极小嵌入器，接口与 langchain 的 Embeddings 一致。"""

    name = "hash-ngram"

    def embed_documents(self, texts):
        return [_hash_vector(t) for t in texts]

    def embed_query(self, text):
        return _hash_vector(text)


def _make_hf_embeddings():
    """尝试加载本地 HuggingFace 嵌入模型。失败返回 (None, 错误信息)。"""
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    try:
        from langchain_huggingface import HuggingFaceEmbeddings

        emb = HuggingFaceEmbeddings(
            model_name=EMBED_MODEL,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
        emb.embed_query("warmup")  # 真正把模型加载起来，失败就抛
        return emb, ""
    except Exception as exc:  # 网络 / 依赖 / 维度问题都走到这里
        return None, f"{type(exc).__name__}: {exc}"


def get_embeddings(force_hash: bool = False):
    """返回 (embedder, kind)。kind ∈ {"huggingface", "hash-ngram"}。"""
    global _EMBEDDER, _EMBEDDER_KIND, _EMBEDDER_ERROR
    if force_hash:
        return HashEmbeddings(), "hash-ngram"
    if _EMBEDDER is not None:
        return _EMBEDDER, _EMBEDDER_KIND
    if os.environ.get("RAG_EMBED", "auto").lower() == "hash":
        _EMBEDDER, _EMBEDDER_KIND = HashEmbeddings(), "hash-ngram"
        return _EMBEDDER, _EMBEDDER_KIND
    emb, err = _make_hf_embeddings()
    if emb is not None:
        _EMBEDDER, _EMBEDDER_KIND = emb, "huggingface"
    else:
        _EMBEDDER, _EMBEDDER_KIND = HashEmbeddings(), "hash-ngram"
        _EMBEDDER_ERROR = err
    return _EMBEDDER, _EMBEDDER_KIND


def rag_status() -> dict:
    emb, kind = get_embeddings()
    try:
        _backend = _vector_backend()
    except Exception:
        _backend = "numpy-fallback"
    return {
        "backend": _backend,
        "embedder": kind,
        "model": EMBED_MODEL if kind == "huggingface" else "hash-ngram(256d)",
        "error": _EMBEDDER_ERROR,
        "indexed": len(_INDEX_CACHE),
        "files": sorted(_INDEX_CACHE),
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
    }


# ---------------------------------------------------------------------------
# 载入 / 切片
# ---------------------------------------------------------------------------
def _load_documents(path: Path):
    """PDF 走 PyPDFLoader（带 page 元数据）；其它格式退回纯文本抽取。"""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from langchain_community.document_loaders import PyPDFLoader

        return PyPDFLoader(str(path)).load()

    from langchain_core.documents import Document

    text = ""
    try:
        import my_agent

        info = my_agent.extract_text(path.read_bytes(), path.name, max_chars=400000)
        text = info.get("text", "")
    except Exception:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            text = ""
    return [Document(page_content=text, metadata={"source": str(path), "page": 0})]


def _split_documents(docs):
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", "！", "？", ". ", "；", "; ", " ", ""],
    )
    chunks = splitter.split_documents(docs)
    for i, chunk in enumerate(chunks):
        chunk.metadata = dict(chunk.metadata or {})
        chunk.metadata["chunk"] = i
    return chunks


def _page_of(chunk) -> int:
    page = (chunk.metadata or {}).get("page", 0)
    try:
        return int(page) + 1  # PyPDFLoader 的 page 从 0 开始
    except Exception:
        return 1


def build_index(path, force: bool = False):
    """构建（或复用）某篇文件的 FAISS 索引，返回 (index, chunks)。"""
    from langchain_community.vectorstores import FAISS

    path = Path(path)
    key = str(path.resolve())
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    cached = _INDEX_CACHE.get(key)
    if cached and cached["mtime"] == mtime and not force:
        return cached["index"], cached["chunks"]

    docs = _load_documents(path)
    chunks = _split_documents(docs)
    emb, _kind = get_embeddings()
    index = FAISS.from_documents(chunks, emb) if chunks else None
    _INDEX_CACHE[key] = {
        "mtime": mtime,
        "index": index,
        "chunks": chunks,
        "built": time.strftime("%H:%M:%S"),
    }
    return index, chunks


def _vector_backend() -> str:
    """faiss 可用返回 "faiss"，否则 "numpy-fallback"（云端内存小也能跑）。"""
    try:
        import faiss  # noqa: F401
        from langchain_community.vectorstores import FAISS  # noqa: F401

        return "faiss"
    except Exception:
        return "numpy-fallback"


def _manual_chunks(path: Path) -> list[dict]:
    """不依赖向量库的切片：按页 + 段落切，保留页码。"""
    key = str(path.resolve())
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    cached = _FALLBACK_CACHE.get(key)
    if cached and cached["mtime"] == mtime:
        return cached["chunks"]

    docs = _load_documents(path)
    chunks: list[dict] = []
    for doc in docs:
        page = int((doc.metadata or {}).get("page", 0)) + 1
        text = (doc.page_content or "").strip()
        if not text:
            continue
        start = 0
        while start < len(text):
            piece = text[start : start + CHUNK_SIZE]
            if piece.strip():
                chunks.append({"text": piece, "page": page})
            start += CHUNK_SIZE - CHUNK_OVERLAP
    _FALLBACK_CACHE[key] = {"mtime": mtime, "chunks": chunks}
    return chunks


def _fallback_search(path: Path, query: str, k: int) -> list[dict]:
    """纯 Python 余弦召回（哈希 n-gram 向量），不依赖 faiss / torch。"""
    chunks = _manual_chunks(path)
    if not chunks:
        return []
    qv = _hash_vector(query)
    scored: list[tuple[float, dict]] = []
    for chunk in chunks:
        dv = _hash_vector(chunk["text"])
        dot = sum(a * b for a, b in zip(qv, dv))
        if dot > 0:
            scored.append((dot, chunk))
    scored.sort(key=lambda x: -x[0])
    out = []
    for score, chunk in scored[: max(k, 1)]:
        out.append(
            {
                "text": chunk["text"],
                "source": path.name,
                "page": int(chunk.get("page", 1)),
                "score": round(1.0 - float(score), 4),
            }
        )
    return out


def similarity_search(paths, query: str, k: int = 4):
    """在若干文件里做相似度检索，返回 [{text, source, page, score}]。"""
    hits: list[dict] = []
    seen: set[str] = set()
    for raw in paths or []:
        path = Path(raw)
        if not path.exists():
            continue
        if _vector_backend() != "faiss":
            for hit in _fallback_search(path, query, k):
                fp = hashlib.md5(hit["text"][:160].encode("utf-8", "ignore")).hexdigest()
                if fp in seen:
                    continue
                seen.add(fp)
                hits.append(hit)
            continue
        try:
            index, _chunks = build_index(path)
        except Exception:
            for hit in _fallback_search(path, query, k):
                fp = hashlib.md5(hit["text"][:160].encode("utf-8", "ignore")).hexdigest()
                if fp in seen:
                    continue
                seen.add(fp)
                hits.append(hit)
            continue
        if index is None:
            continue
        try:
            pairs = index.similarity_search_with_score(query, k=max(k, 2))
        except Exception:
            continue
        for doc, score in pairs:
            text = (doc.page_content or "").strip()
            if not text:
                continue
            fingerprint = hashlib.md5(text[:160].encode("utf-8", "ignore")).hexdigest()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            hits.append(
                {
                    "text": text,
                    "source": path.name,
                    "page": int((doc.metadata or {}).get("page", 0)) + 1,
                    "score": float(score),
                }
            )
    hits.sort(key=lambda h: h["score"])
    return hits[:k]


# ---------------------------------------------------------------------------
# 组装上下文
# ---------------------------------------------------------------------------
def format_context(hits, max_chars: int = 6000) -> str:
    """把命中片段拼成带 [来源：文件名 - 第X页] 前缀的文本块。"""
    if not hits:
        return ""
    blocks: list[str] = []
    used = 0
    for hit in hits:
        header = f"[来源：{hit['source']} - 第{hit['page']}页]"
        block = f"{header}\n{hit['text']}"
        if used + len(block) > max_chars:
            remain = max_chars - used
            if remain < 200:
                break
            block = block[:remain].rstrip() + "…"
        blocks.append(block)
        used += len(block)
        if used >= max_chars:
            break
    return "\n\n".join(blocks)


def rag_context_for(paths, query: str, *, k: int = 4, max_chars: int = 6000) -> str:
    """一步到位：检索 + 拼装。任何异常都返回空串，绝不打断主流程。"""
    try:
        hits = similarity_search(paths, query, k=k)
        return format_context(hits, max_chars=max_chars)
    except Exception:
        return ""


def reset_cache() -> None:
    _INDEX_CACHE.clear()
