import os
import logging
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

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
    All collections use cosine-similarity with sentence-transformers embeddings.
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

    # ── RFC collection ────────────────────────────────────────────────────────

    def rfc_count(self) -> int:
        return self._rfc_col.count()

    def add_rfc_chunks(self, chunks: List[Dict[str, Any]]) -> None:
        """Upsert RFC chunks in batches of 500 to handle large RFC sets."""
        ids = [c["id"] for c in chunks]
        docs = [c["text"] for c in chunks]
        metas = [
            {
                "rfc_no": c["rfc_no"],
                "rfc_title": c["rfc_title"],
                "section_no": c["section_no"],
                "section_title": c["section_title"],
                "chunk_idx": c["chunk_idx"],
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
        logger.info(f"Upserted {len(ids)} RFC chunks into '{_RFC_COLLECTION}'")

    def search_rfc(
        self,
        query: str,
        top_k: int = TOP_K,
        rfc_filter: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Hybrid search over the RFC collection.

        1. Semantic search (cosine similarity via sentence-transformers).
        2. Keyword fallback — ChromaDB $contains substring match — runs when the
           top semantic score is below 0.45.  Handles abbreviations and acronyms
           (e.g. 'pcfg', 'acfg', 'srtp') that don't embed well in isolation.

        Results from both passes are merged and de-duplicated before returning.
        """
        count = self._rfc_col.count()
        if count == 0:
            return []

        where_clause = {"rfc_no": {"$in": rfc_filter}} if rfc_filter else None

        # ── 1. Semantic search ────────────────────────────────────────────────
        sem_kwargs: Dict[str, Any] = {
            "query_texts": [query],
            "n_results": min(top_k, count),
        }
        if where_clause:
            sem_kwargs["where"] = where_clause

        res = self._rfc_col.query(**sem_kwargs)
        results = self._parse_rfc_query(res)

        # ── 2. Keyword fallback ───────────────────────────────────────────────
        top_score = results[0]["score"] if results else 0.0
        if top_score < 0.45:
            keyword_hits = self._keyword_search_rfc(query, top_k, where_clause)
            seen = {r["text"][:80] for r in results}
            for hit in keyword_hits:
                if hit["text"][:80] not in seen:
                    results.append(hit)
            results = results[:top_k]

        return results

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

    def _keyword_search_rfc(
        self,
        query: str,
        top_k: int,
        where_clause: Optional[Dict],
    ) -> List[Dict[str, Any]]:
        """
        Substring keyword search using ChromaDB's $contains filter.
        Tries the query as-is; if that returns nothing, tries uppercase
        to cover RFC documents that use ALL-CAPS abbreviations.
        """
        def _fetch(term: str) -> List[Dict[str, Any]]:
            try:
                get_kwargs: Dict[str, Any] = {
                    "where_document": {"$contains": term},
                    "include": ["documents", "metadatas"],
                }
                if where_clause:
                    get_kwargs["where"] = where_clause
                res = self._rfc_col.get(**get_kwargs)
                hits = []
                for doc, meta in zip(
                    res.get("documents", []), res.get("metadatas", [])
                ):
                    hits.append({
                        "text":          doc,
                        "rfc_no":        meta["rfc_no"],
                        "rfc_title":     meta["rfc_title"],
                        "section_no":    meta["section_no"],
                        "section_title": meta["section_title"],
                        "score":         0.50,  # nominal score for keyword hits
                    })
                return hits[:top_k]
            except Exception as exc:
                logger.warning("Keyword search failed for '%s': %s", term, exc)
                return []

        hits = _fetch(query)
        if not hits and query != query.upper():
            hits = _fetch(query.upper())
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
            # Embed the full raw message for best semantic coverage
            docs.append(msg.get("raw") or str(msg))
            metas.append(
                {
                    "msg_type": msg.get("type", "unknown"),
                    "method": msg.get("method") or "",
                    "response_code": int(msg.get("response_code") or 0),
                    # Truncate to ChromaDB metadata char limits
                    "call_id": str(msg.get("call_id", ""))[:64],
                    "cseq": str(msg.get("cseq", ""))[:32],
                    "src_ip": str(msg.get("src_ip", ""))[:40],
                    "dst_ip": str(msg.get("dst_ip", ""))[:40],
                }
            )
        if ids:
            self._trace_col.upsert(ids=ids, documents=docs, metadatas=metas)
            logger.info(f"Indexed {len(ids)} SIP messages into trace collection")

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
                    "text": doc,
                    "method": meta.get("method", ""),
                    "response_code": meta.get("response_code", 0),
                    "call_id": meta.get("call_id", ""),
                    "cseq": meta.get("cseq", ""),
                    "src_ip": meta.get("src_ip", ""),
                    "dst_ip": meta.get("dst_ip", ""),
                    "score": round(1.0 - float(dist), 4),
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
        query: str,
        top_k: int = TOP_K,
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
        names: Dict[str, str] = {}
        types: Dict[str, str] = {}
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
        self._rfc_col = self._get_or_create(_RFC_COLLECTION)
        logger.info("RFC collection cleared — ready for re-indexing")
