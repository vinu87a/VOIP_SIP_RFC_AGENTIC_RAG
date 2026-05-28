import os
import re
import logging
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from rank_bm25 import BM25Okapi

from config import CHROMA_PERSIST_DIR, EMBEDDING_MODEL, TOP_K

logger = logging.getLogger(__name__)

_RFC_COLLECTION   = "sip_rfcs"
_TRACE_COLLECTION = "sip_trace"
_DOCS_COLLECTION  = "user_docs"


class VectorStore:
    """
    Thin wrapper around three ChromaDB collections:
      • sip_rfcs   — persistent, pre-indexed RFC + glossary knowledge base
      • sip_trace  — ephemeral per-session SIP trace (cleared on init)
      • user_docs  — persistent user-uploaded documents (PDF, DOCX, HTML, TXT, URL)

    RFC search uses BM25 sparse retrieval + dense semantic retrieval fused with
    Reciprocal Rank Fusion (RRF).  Trace and document search use cosine similarity only.
    """

    def __init__(self) -> None:
        os.makedirs(CHROMA_PERSIST_DIR, exist_ok=True)
        self._client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
        self._ef = SentenceTransformerEmbeddingFunction(
            model_name=EMBEDDING_MODEL,
            device="cpu",
        )
        self._rfc_col = self._get_or_create(_RFC_COLLECTION)

        # Trace collection is ephemeral — always start clean.
        try:
            self._client.delete_collection(_TRACE_COLLECTION)
        except Exception:
            pass
        self._trace_col = self._get_or_create(_TRACE_COLLECTION)

        # User docs are persistent — not cleared on init.
        self._doc_col = self._get_or_create(_DOCS_COLLECTION)

        # BM25 index over RFC corpus.
        # Built eagerly if the collection already has data; otherwise built
        # lazily on the first search_rfc call (after ingest populates the collection).
        self._bm25_corpus: List[Dict[str, Any]] = []
        self._bm25_index:  Optional[BM25Okapi]  = None
        if self._rfc_col.count() > 0:
            self._build_bm25_index()

        logger.info(
            "VectorStore ready — RFC: %d  Trace: %d  UserDocs: %d",
            self._rfc_col.count(),
            self._trace_col.count(),
            self._doc_col.count(),
        )

    def _get_or_create(self, name: str):
        return self._client.get_or_create_collection(
            name=name,
            embedding_function=self._ef,
            metadata={"hnsw:space": "cosine"},
        )

    # ── BM25 index ────────────────────────────────────────────────────────────

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Lowercase alphanumeric tokenization — preserves RFC acronyms intact."""
        return re.findall(r'[a-zA-Z0-9]+', text.lower())

    def _build_bm25_index(self) -> None:
        """
        Pull every RFC chunk from ChromaDB and build an in-memory BM25Okapi index.

        Called once at startup (if data exists) and invalidated — then lazily
        rebuilt — whenever new RFC chunks are added via add_rfc_chunks().
        """
        res  = self._rfc_col.get(include=["documents", "metadatas"])
        docs  = res.get("documents", [])
        metas = res.get("metadatas", [])

        if not docs:
            logger.warning("BM25 build skipped: RFC collection is empty")
            return

        tokenized: List[List[str]] = []
        corpus:    List[Dict[str, Any]] = []

        for doc, meta in zip(docs, metas):
            tokenized.append(self._tokenize(doc))
            corpus.append({
                "text":          doc,
                "rfc_no":        meta["rfc_no"],
                "rfc_title":     meta["rfc_title"],
                "section_no":    meta["section_no"],
                "section_title": meta["section_title"],
            })

        self._bm25_corpus = corpus
        self._bm25_index  = BM25Okapi(tokenized)
        logger.info("BM25 index built over %d RFC chunks", len(corpus))

    def _bm25_search_rfc(
        self,
        query: str,
        top_k: int,
        rfc_filter: Optional[List[int]],
    ) -> List[Dict[str, Any]]:
        """
        Score every RFC chunk with BM25 and return the top_k hits.

        When rfc_filter is supplied, chunks whose rfc_no is not in the list are
        zeroed out before ranking — equivalent to a pre-filter without rebuilding
        a separate index.
        """
        if not self._bm25_index or not self._bm25_corpus:
            return []

        tokens = self._tokenize(query)
        scores = self._bm25_index.get_scores(tokens)  # numpy array, one score per chunk

        if rfc_filter:
            for i, entry in enumerate(self._bm25_corpus):
                if entry["rfc_no"] not in rfc_filter:
                    scores[i] = 0.0

        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)

        results: List[Dict[str, Any]] = []
        for i in ranked[:top_k]:
            if scores[i] <= 0.0:
                break
            entry = self._bm25_corpus[i]
            results.append({
                "text":          entry["text"],
                "rfc_no":        entry["rfc_no"],
                "rfc_title":     entry["rfc_title"],
                "section_no":    entry["section_no"],
                "section_title": entry["section_title"],
                "score":         float(scores[i]),
            })

        return results

    @staticmethod
    def _rrf_merge(
        semantic: List[Dict[str, Any]],
        bm25:     List[Dict[str, Any]],
        top_k:    int,
        k:        int = 60,
    ) -> List[Dict[str, Any]]:
        """
        Reciprocal Rank Fusion (Cormack et al., 2009).

        RRF_score(d) = Σ  1 / (k + rank(d))
                       over each ranked list that contains d

        k=60 is the standard constant from the original paper.  Neither the
        cosine-similarity score from the semantic pass nor the raw BM25 score
        from the sparse pass is used directly — only the rank positions matter,
        which makes the fusion robust to the very different score scales of the
        two retrievers.
        """
        rrf_scores: Dict[str, float] = {}
        store:      Dict[str, Dict]  = {}

        for rank, r in enumerate(semantic):
            key = r["text"][:80]
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            store[key] = r

        for rank, r in enumerate(bm25):
            key = r["text"][:80]
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            if key not in store:
                store[key] = r

        top_keys = sorted(rrf_scores, key=lambda x: rrf_scores[x], reverse=True)[:top_k]

        merged: List[Dict[str, Any]] = []
        for key in top_keys:
            result = store[key].copy()
            result["score"] = round(rrf_scores[key], 4)
            merged.append(result)

        return merged

    # ── RFC collection ────────────────────────────────────────────────────────

    def rfc_count(self) -> int:
        return self._rfc_col.count()

    def add_rfc_chunks(self, chunks: List[Dict[str, Any]]) -> None:
        """
        Upsert RFC chunks into ChromaDB in batches of 500.
        Invalidates the BM25 index so it is rebuilt lazily on the next search.
        """
        ids   = [c["id"] for c in chunks]
        docs  = [c["text"] for c in chunks]
        metas = [
            {
                "rfc_no":        c["rfc_no"],
                "rfc_title":     c["rfc_title"],
                "section_no":    c["section_no"],
                "section_title": c["section_title"],
                "chunk_idx":     c["chunk_idx"],
            }
            for c in chunks
        ]
        batch = 500
        for i in range(0, len(ids), batch):
            self._rfc_col.upsert(
                ids=ids[i : i + batch],
                documents=docs[i : i + batch],
                metadatas=metas[i : i + batch],
            )
        logger.info("Upserted %d RFC chunks into '%s'", len(ids), _RFC_COLLECTION)
        # Invalidate the BM25 index; it will be rebuilt lazily on the next search.
        self._bm25_index = None

    def search_rfc(
        self,
        query:      str,
        top_k:      int = TOP_K,
        rfc_filter: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Hybrid BM25 + semantic search over the RFC collection fused with RRF.

        Both retrievers fetch up to top_k * 2 candidates independently; RRF
        merges and re-ranks the union into the final top_k result list.
        Neither pass is gated by a score threshold — both always run.
        """
        count = self._rfc_col.count()
        if count == 0:
            return []

        # Rebuild BM25 index if it was invalidated by a recent add_rfc_chunks call.
        if self._bm25_index is None:
            self._build_bm25_index()

        candidates   = min(top_k * 2, count)
        where_clause = {"rfc_no": {"$in": rfc_filter}} if rfc_filter else None

        # ── 1. Dense semantic retrieval (cosine similarity via HNSW) ─────────
        sem_kwargs: Dict[str, Any] = {
            "query_texts": [query],
            "n_results":   candidates,
        }
        if where_clause:
            sem_kwargs["where"] = where_clause

        res              = self._rfc_col.query(**sem_kwargs)
        semantic_results = self._parse_rfc_query(res)

        # ── 2. Sparse BM25 retrieval ──────────────────────────────────────────
        bm25_results = self._bm25_search_rfc(query, candidates, rfc_filter)

        # ── 3. Reciprocal Rank Fusion ─────────────────────────────────────────
        return self._rrf_merge(semantic_results, bm25_results, top_k=top_k)

    def _parse_rfc_query(self, res) -> List[Dict[str, Any]]:
        """Convert a raw ChromaDB query result into the standard hit dict list."""
        hits = []
        for doc, meta, dist in zip(
            res["documents"][0], res["metadatas"][0], res["distances"][0]
        ):
            hits.append({
                "text":          doc,
                "rfc_no":        meta["rfc_no"],
                "rfc_title":     meta["rfc_title"],
                "section_no":    meta["section_no"],
                "section_title": meta["section_title"],
                "score":         round(1.0 - float(dist), 4),
            })
        return hits

    # ── Trace collection ──────────────────────────────────────────────────────

    def trace_count(self) -> int:
        return self._trace_col.count()

    def clear_trace(self) -> None:
        """Drop and recreate the trace collection (new upload replaces old)."""
        self._client.delete_collection(_TRACE_COLLECTION)
        self._trace_col = self._get_or_create(_TRACE_COLLECTION)
        logger.info("Trace collection cleared")

    def add_trace_messages(self, messages: List[Dict[str, Any]]) -> None:
        """Index parsed SIP messages into the trace collection."""
        ids, docs, metas = [], [], []
        for idx, msg in enumerate(messages):
            ids.append(f"trace_msg_{idx}")
            docs.append(msg.get("raw") or str(msg))
            metas.append(
                {
                    "msg_type":      msg.get("type", "unknown"),
                    "method":        msg.get("method") or "",
                    "response_code": int(msg.get("response_code") or 0),
                    "call_id":       str(msg.get("call_id", ""))[:64],
                    "cseq":          str(msg.get("cseq", ""))[:32],
                    "src_ip":        str(msg.get("src_ip", ""))[:40],
                    "dst_ip":        str(msg.get("dst_ip", ""))[:40],
                    "trace_idx":     idx,                              # original capture order
                    "timestamp":     str(msg.get("timestamp", "")),   # wall-clock time
                }
            )
        if ids:
            self._trace_col.upsert(ids=ids, documents=docs, metadatas=metas)
            logger.info("Indexed %d SIP messages into trace collection", len(ids))

    def search_trace(
        self, query: str, top_k: int = TOP_K
    ) -> List[Dict[str, Any]]:
        """Semantic search over the uploaded SIP trace."""
        count = self._trace_col.count()
        if count == 0:
            return []

        res = self._trace_col.query(
            query_texts=[query],
            n_results=min(top_k, count),
        )

        results = []
        for doc, meta, dist in zip(
            res["documents"][0], res["metadatas"][0], res["distances"][0]
        ):
            results.append(
                {
                    "text":          doc,
                    "method":        meta.get("method", ""),
                    "response_code": meta.get("response_code", 0),
                    "call_id":       meta.get("call_id", ""),
                    "cseq":          meta.get("cseq", ""),
                    "src_ip":        meta.get("src_ip", ""),
                    "dst_ip":        meta.get("dst_ip", ""),
                    "score":         round(1.0 - float(dist), 4),
                }
            )
        return results

    def get_all_trace_messages(self) -> List[Dict[str, Any]]:
        """Retrieve every message in the trace collection (for call-flow reconstruction)."""
        count = self._trace_col.count()
        if count == 0:
            return []
        res = self._trace_col.get(include=["documents", "metadatas"])
        return [
            {"text": doc, **meta}
            for doc, meta in zip(res["documents"], res["metadatas"])
        ]

    # ── User documents collection ─────────────────────────────────────────────

    def doc_count(self) -> int:
        return self._doc_col.count()

    def add_doc_chunks(self, chunks: List[Dict[str, Any]]) -> None:
        """Upsert user-document chunks into the persistent user_docs collection."""
        if not chunks:
            return
        ids   = [c["id"] for c in chunks]
        docs  = [c["text"] for c in chunks]
        metas = [
            {
                "doc_id":    c["doc_id"],
                "doc_name":  c["doc_name"],
                "doc_type":  c["doc_type"],
                "chunk_idx": c["chunk_idx"],
            }
            for c in chunks
        ]
        batch = 500
        for i in range(0, len(ids), batch):
            self._doc_col.upsert(
                ids=ids[i : i + batch],
                documents=docs[i : i + batch],
                metadatas=metas[i : i + batch],
            )
        logger.info("Upserted %d chunks into '%s'", len(ids), _DOCS_COLLECTION)

    def search_docs(
        self,
        query:      str,
        top_k:      int = TOP_K,
        doc_filter: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Semantic search over user-uploaded documents.
        Optionally restrict to a list of doc_id strings via doc_filter.
        """
        count = self._doc_col.count()
        if count == 0:
            return []

        kwargs: Dict[str, Any] = {
            "query_texts": [query],
            "n_results":   min(top_k, count),
        }
        if doc_filter:
            kwargs["where"] = {"doc_id": {"$in": doc_filter}}

        res = self._doc_col.query(**kwargs)
        results = []
        for doc, meta, dist in zip(
            res["documents"][0], res["metadatas"][0], res["distances"][0]
        ):
            results.append({
                "text":      doc,
                "doc_id":    meta.get("doc_id", ""),
                "doc_name":  meta.get("doc_name", ""),
                "doc_type":  meta.get("doc_type", ""),
                "chunk_idx": meta.get("chunk_idx", 0),
                "score":     round(1.0 - float(dist), 4),
            })
        return results

    def list_docs(self) -> List[Dict[str, Any]]:
        """
        Return a list of indexed documents with their metadata and chunk counts.
        Each entry: {doc_id, doc_name, doc_type, chunk_count}
        """
        count = self._doc_col.count()
        if count == 0:
            return []

        from collections import Counter
        res    = self._doc_col.get(include=["metadatas"])
        counts: Counter = Counter(m.get("doc_id") for m in res["metadatas"])
        names:  Dict[str, str] = {}
        types:  Dict[str, str] = {}
        for m in res["metadatas"]:
            did = m.get("doc_id", "")
            if did and did not in names:
                names[did] = m.get("doc_name", did)
                types[did] = m.get("doc_type", "unknown")

        return sorted(
            [
                {
                    "doc_id":      did,
                    "doc_name":    names[did],
                    "doc_type":    types[did],
                    "chunk_count": counts[did],
                }
                for did in counts
            ],
            key=lambda d: d["doc_name"].lower(),
        )

    def remove_doc(self, doc_id: str) -> None:
        """Delete all chunks belonging to *doc_id* from the user_docs collection."""
        self._doc_col.delete(where={"doc_id": doc_id})
        logger.info("Removed doc '%s' from user_docs", doc_id)

    def clear_docs(self) -> None:
        """Drop and recreate the user_docs collection, removing all user documents."""
        self._client.delete_collection(_DOCS_COLLECTION)
        self._doc_col = self._get_or_create(_DOCS_COLLECTION)
        logger.info("Cleared all user documents")

    # ── RFC collection management ─────────────────────────────────────────────

    def clear_rfcs(self) -> None:
        """Drop and recreate the RFC collection so a full re-index can be triggered."""
        self._client.delete_collection(_RFC_COLLECTION)
        self._rfc_col        = self._get_or_create(_RFC_COLLECTION)
        self._bm25_index     = None
        self._bm25_corpus    = []
        logger.info("RFC collection cleared — ready for re-indexing")
