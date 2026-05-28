# Low Level Design (LLD) — SIP/RTP Agentic RAG System

---

## Table of Contents

1. [Module Map](#1-module-map)
2. [config.py — Global Configuration](#2-configpy--global-configuration)
3. [store/vector_store.py — ChromaDB Wrapper](#3-storevector_storepy--chromadb-wrapper)
4. [ingest/rfc_fetcher.py — RFC Downloader](#4-ingestrfc_fetcherpy--rfc-downloader)
5. [ingest/rfc_chunker.py — RFC Text Processor](#5-ingestrfc_chunkerpy--rfc-text-processor)
6. [ingest/doc_ingest.py — User Document Ingestion](#6-ingestdoc_ingestpy--user-document-ingestion)
7. [ingest/parsers/text_parser.py — SIP Text Trace Parser](#7-ingestparserstext_parserpy--sip-text-trace-parser)
8. [ingest/parsers/html_parser.py — SIP HTML Trace Parser](#8-ingestparsershtml_parserpy--sip-html-trace-parser)
9. [ingest/parsers/pcap_parser.py — PCAP Trace Parser](#9-ingestparserspcap_parserpy--pcap-trace-parser)
10. [agent/tools.py — Tool Definitions & Implementations](#10-agenttoolspy--tool-definitions--implementations)
11. [agent/orchestrator.py — LangGraph Agent Engine](#11-agentorchestratorpy--langgraph-agent-engine)
12. [agent/prompts.py — System Prompt](#12-agentpromptspy--system-prompt)
13. [app.py — Streamlit UI Layer](#13-apppy--streamlit-ui-layer)
14. [Data Schemas Reference](#14-data-schemas-reference)
15. [Inter-Module Call Flow Diagrams](#15-inter-module-call-flow-diagrams)

---

## 1. Module Map

```
Agentic RAG/
│
├── config.py                        ← All constants and environment variables
│
├── store/
│   └── vector_store.py              ← ChromaDB PersistentClient wrapper (3 collections)
│
├── ingest/
│   ├── rfc_fetcher.py               ← HTTP download + disk cache for RFC .txt files
│   ├── rfc_chunker.py               ← Clean, section-split, and chunk RFC text
│   ├── doc_ingest.py                ← User document ingestion (PDF/DOCX/HTML/TXT/URL)
│   └── parsers/
│       ├── text_parser.py           ← Parse SIP messages from plain-text trace
│       ├── html_parser.py           ← Extract SIP blocks from HTML trace exports
│       └── pcap_parser.py           ← Extract SIP + RTP streams from PCAP/PCAPNG files
│
├── agent/
│   ├── prompts.py                   ← Static SYSTEM_PROMPT string
│   ├── tools.py                     ← Tool JSON schemas + Python dispatch implementations
│   └── orchestrator.py             ← LangGraph StateGraph, LLM wiring, public run() API
│
└── app.py                           ← Streamlit application (UI, sidebar, chat loop)
```

**Dependency flow (imports only go downward):**

```
app.py
  └── agent/orchestrator.py
        ├── agent/tools.py
        │     └── store/vector_store.py
        ├── agent/prompts.py
        └── config.py
  └── ingest/rfc_fetcher.py
        └── config.py
  └── ingest/rfc_chunker.py
        └── config.py
  └── ingest/doc_ingest.py   (no project imports)
  └── ingest/parsers/text_parser.py
  └── ingest/parsers/html_parser.py
        └── ingest/parsers/text_parser.py
  └── ingest/parsers/pcap_parser.py
        └── ingest/parsers/text_parser.py
```

---

## 2. config.py — Global Configuration

**Purpose:** Single source of truth for all tuneable parameters, API keys, model names, paths, RFC numbers, and per-RFC metadata. Every other module imports from here; no module hardcodes these values directly.

### Constants Reference

| Constant | Type | Value / Source | Purpose |
|---|---|---|---|
| `GROQ_API_KEY` | `str` | `env: GROQ_API_KEY` | Groq Cloud authentication |
| `GROQ_MODEL` | `str` | `"meta-llama/llama-4-scout-17b-16e-instruct"` | Primary LLM for reasoning |
| `OLLAMA_MODEL` | `str` | `env: OLLAMA_MODEL` (default `"gemma4:e4b"`) | Local fallback LLM |
| `OLLAMA_BASE_URL` | `str` | `env: OLLAMA_BASE_URL` (default `"http://localhost:11434"`) | Ollama server endpoint |
| `EMBEDDING_MODEL` | `str` | `"all-MiniLM-L6-v2"` | SentenceTransformers model for ChromaDB |
| `CHROMA_PERSIST_DIR` | `str` | `<project_root>/chroma_db` | ChromaDB on-disk storage path |
| `RFC_CACHE_DIR` | `str` | `<project_root>/rfc_cache` | Local cache for downloaded RFC `.txt` files |
| `RFC_NUMBERS` | `List[int]` | 25 integers | RFCs to index into the knowledge base |
| `CHUNK_SIZE` | `int` | `2000` | Max characters per RFC chunk (~500 tokens) |
| `CHUNK_OVERLAP` | `int` | `300` | Overlap between consecutive RFC chunks |
| `TOP_K` | `int` | `6` | Default number of search results returned |

### RFC_META Dictionary

`RFC_META` maps each RFC number to a dict with two keys:

```python
RFC_META = {
    3261: {
        "title": "SIP: Session Initiation Protocol",
        "topics": [
            "UAC (User Agent Client) — request originator",
            "INVITE method — call setup request",
            # ... ~50 topic strings
        ]
    },
    # ... 24 more RFCs
}
```

- **`title`** — Human-readable RFC title; stored as `rfc_title` metadata in ChromaDB.
- **`topics`** — Descriptive strings that guide chunking quality (not currently injected into chunk text but available for future topic-guided retrieval). Each topic string is crafted to describe common query patterns, e.g., `"488 Not Acceptable Here — SDP mismatch"`.

---

## 3. store/vector_store.py — ChromaDB Wrapper

**Purpose:** Encapsulates all ChromaDB interactions behind a clean Python API. The rest of the system never calls ChromaDB directly.

### Class: `VectorStore`

```
VectorStore
├── __init__()
├── _get_or_create(name) → Collection
│
├── BM25 Index (in-memory, RFC corpus only)
│   ├── _tokenize(text) → List[str]                 [static, private]
│   ├── _build_bm25_index()                          [private]
│   ├── _bm25_search_rfc(query, top_k, rfc_filter) → List[Dict]  [private]
│   └── _rrf_merge(semantic, bm25, top_k, k) → List[Dict]        [static, private]
│
├── RFC Collection API
│   ├── rfc_count() → int
│   ├── add_rfc_chunks(chunks)
│   ├── search_rfc(query, top_k, rfc_filter) → List[Dict]
│   ├── _parse_rfc_query(res) → List[Dict]           [private]
│   └── clear_rfcs()
│
├── Trace Collection API
│   ├── trace_count() → int
│   ├── clear_trace()
│   ├── add_trace_messages(messages)
│   ├── search_trace(query, top_k) → List[Dict]
│   └── get_all_trace_messages() → List[Dict]
│
└── User Docs Collection API
    ├── doc_count() → int
    ├── add_doc_chunks(chunks)
    ├── search_docs(query, top_k, doc_filter) → List[Dict]
    ├── list_docs() → List[Dict]
    ├── remove_doc(doc_id)
    └── clear_docs()
```

### `__init__(self) → None`

Initialises the ChromaDB persistent client and three collections.

```
Step 1: os.makedirs(CHROMA_PERSIST_DIR, exist_ok=True)
Step 2: chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
Step 3: SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2", device="cpu")
Step 4: get_or_create "sip_rfcs"    (cosine, persistent)
Step 5: delete + recreate "sip_trace" (cosine, ephemeral — cleared on each app start)
Step 6: get_or_create "user_docs"   (cosine, persistent)
Step 7: If rfc_col.count() > 0: _build_bm25_index()   ← eager build on startup
        Else: set _bm25_index = None                   ← lazy build on first search
Step 8: log counts of all three collections
```

**Why trace is ephemeral:** A SIP trace is session-specific; users load a new capture per session. Clearing on init ensures no stale trace from a previous session leaks into new analysis.

### `_get_or_create(name: str) → Collection`

```python
return self._client.get_or_create_collection(
    name=name,
    embedding_function=self._ef,
    metadata={"hnsw:space": "cosine"},
)
```

All collections use cosine distance so that similarity scores can be normalised as `1.0 − distance` and lie in `[0, 1]`.

---

### RFC Collection Methods

#### `rfc_count() → int`
Returns `self._rfc_col.count()`. Used by `app.py` to decide whether indexing is needed.

#### `add_rfc_chunks(chunks: List[Dict]) → None`

Upserts RFC chunks in batches of 500 to respect ChromaDB limits.

**Input schema for each chunk dict:**

| Key | Type | Description |
|---|---|---|
| `id` | `str` | Unique stable ID, e.g. `"rfc3261_s8_1_2_c0"` |
| `text` | `str` | The chunk body text (up to CHUNK_SIZE chars) |
| `rfc_no` | `int` | RFC number, e.g. `3261` |
| `rfc_title` | `str` | Title from RFC_META |
| `section_no` | `str` | Section number, e.g. `"8.1.2"` |
| `section_title` | `str` | Section heading text |
| `chunk_idx` | `int` | 0-based index within the section |

Uses `upsert` (not `add`) so re-indexing is idempotent — running twice does not duplicate chunks.

After upserting, sets `self._bm25_index = None` to **invalidate** the in-memory BM25 index. The index is rebuilt lazily on the next `search_rfc` call, ensuring it reflects all newly added chunks without rebuilding 25 times during the initial ingest of 25 RFCs.

#### BM25 Index Methods

##### `_tokenize(text: str) → List[str]`  *(static)*

```python
return re.findall(r'[a-zA-Z0-9]+', text.lower())
```

Lowercase alphanumeric tokenization. Keeps RFC acronyms (`pcfg`, `srtp`, `ssrc`) as single tokens. No stemming or stopword removal — RFC vocabulary is too specialised for general NLP preprocessing.

##### `_build_bm25_index() → None`

Pulls every document and metadata entry from the `sip_rfcs` ChromaDB collection via `get(include=["documents", "metadatas"])`. Tokenizes each document with `_tokenize` and builds a `BM25Okapi` index from the token lists. The parallel `_bm25_corpus` list stores the original text and metadata for each entry at the same index position, enabling O(1) metadata lookup after scoring.

Time complexity: O(N × L) where N = corpus size (1,806 chunks), L = average tokens per chunk. Typical build time < 500ms on CPU.

##### `_bm25_search_rfc(query, top_k, rfc_filter) → List[Dict]`

```
1. tokens = _tokenize(query)
2. scores = self._bm25_index.get_scores(tokens)   ← numpy array, one score per chunk
3. If rfc_filter: set scores[i] = 0.0 for all i where corpus[i].rfc_no ∉ rfc_filter
4. ranked = argsort(scores, descending)
5. Return top_k entries where score > 0.0
```

`rfc_filter` is applied as a **post-scoring mask** rather than by rebuilding a filtered index. This is correct because `get_scores` is already vectorised over the full corpus; zeroing non-matching entries before ranking is O(N) and avoids the overhead of constructing a sub-index.

##### `_rrf_merge(semantic, bm25, top_k, k=60) → List[Dict]`  *(static)*

Reciprocal Rank Fusion (Cormack et al., 2009):

```
For each result list (semantic, bm25):
  For rank, chunk in enumerate(list):
    rrf_scores[chunk_key] += 1.0 / (k + rank + 1)

Sort all chunks by rrf_scores descending → return top_k
```

`k=60` is the standard constant from the original paper. It prevents the top-ranked item from dominating (score ≈ 1/61 ≈ 0.016) and makes fusion stable even when one retriever is highly confident. A chunk appearing at rank 1 in both lists scores ≈ 0.033; a chunk appearing at rank 1 in only one list scores ≈ 0.016.

The raw cosine-similarity score and raw BM25 score are **not used** in the merge — only rank positions matter. This makes the fusion robust to the incompatible score scales of the two retrievers (cosine: [0,1]; BM25: unbounded float).

De-duplication uses the first 80 characters of chunk text as the key; the semantic pass's metadata is preferred for any chunk that appears in both lists.

#### `search_rfc(query, top_k, rfc_filter) → List[Dict]`

Fully hybrid search — both passes always run, no score threshold gate:

```
If _bm25_index is None: _build_bm25_index()   ← lazy rebuild after ingest

candidates = min(top_k * 2, rfc_count)       ← fetch 2× for better fusion pool

Pass 1 — Dense semantic retrieval
  └── ChromaDB cosine/HNSW query, n_results=candidates
  └── Optional where={"rfc_no": {"$in": rfc_filter}}

Pass 2 — Sparse BM25 retrieval
  └── _bm25_search_rfc(query, candidates, rfc_filter)

Fusion
  └── _rrf_merge(semantic_results, bm25_results, top_k)
```

**`rfc_filter`** is enforced independently in both passes: as a ChromaDB `where` clause for the dense pass and as a score-zeroing mask for the BM25 pass.

**Return schema (per hit):**

| Key | Type |
|---|---|
| `text` | `str` — chunk body |
| `rfc_no` | `int` |
| `rfc_title` | `str` |
| `section_no` | `str` |
| `section_title` | `str` |
| `score` | `float` — RRF fusion score (≈ 0.016–0.033 range) |

#### `clear_rfcs() → None`

```python
self._client.delete_collection(_RFC_COLLECTION)
self._rfc_col     = self._get_or_create(_RFC_COLLECTION)
self._bm25_index  = None
self._bm25_corpus = []
```

Drops the `sip_rfcs` collection from ChromaDB, recreates an empty one, and clears the in-memory BM25 index and corpus. This ensures `rfc_count() == 0` after the call, which triggers `_ensure_rfc_index()` in `app.py` to re-fetch and re-embed all RFCs on the next run. The BM25 index will be rebuilt lazily on the first `search_rfc` call after re-indexing completes.

---

### Trace Collection Methods

#### `add_trace_messages(messages: List[Dict]) → None`

Embeds each SIP message using its full raw text for best semantic coverage. SDP bodies, header values, and response codes are all included in the embedded string.

**Metadata stored per message:**

| Key | ChromaDB Type | Source |
|---|---|---|
| `msg_type` | `str` | `"request"` / `"response"` / `"rtp_stream"` |
| `method` | `str` | e.g. `"INVITE"`, `"BYE"` (empty for responses) |
| `response_code` | `int` | e.g. `488`, `0` for requests |
| `call_id` | `str` | Truncated to 64 chars (ChromaDB limit) |
| `cseq` | `str` | Truncated to 32 chars |
| `src_ip` | `str` | Truncated to 40 chars |
| `dst_ip` | `str` | Truncated to 40 chars |

**IDs** are assigned as `"trace_msg_0"`, `"trace_msg_1"`, … (index-based). Uses `upsert` so re-uploading a trace replaces existing entries.

#### `get_all_trace_messages() → List[Dict]`

Used exclusively by `_reconstruct_call_flow` in `tools.py`. Fetches every document and metadata entry in the trace collection without any filtering, returning them as a flat list of merged dicts.

#### `search_trace(query, top_k) → List[Dict]`

Pure semantic search — no keyword fallback (SIP messages contain enough natural text for embeddings to work well). Returns fields: `text`, `method`, `response_code`, `call_id`, `cseq`, `src_ip`, `dst_ip`, `score`.

---

### User Docs Collection Methods

#### `add_doc_chunks(chunks: List[Dict]) → None`

Upserts in batches of 500. **Input schema per chunk:**

| Key | Type | Description |
|---|---|---|
| `id` | `str` | `"{doc_id}_c{idx}_{uuid6}"` — globally unique |
| `text` | `str` | Chunk body (up to ~1500 chars) |
| `doc_id` | `str` | Stable document identifier, e.g. `"DOC-A1B2C3D4"` |
| `doc_name` | `str` | Original filename or URL path |
| `doc_type` | `str` | `"pdf"` / `"docx"` / `"html"` / `"txt"` |
| `chunk_idx` | `int` | 0-based index within the document |

#### `search_docs(query, top_k, doc_filter) → List[Dict]`

When `doc_filter` is provided (list of `doc_id` strings), adds `where={"doc_id": {"$in": doc_filter}}` to restrict the search to those documents. Returns fields: `text`, `doc_id`, `doc_name`, `doc_type`, `chunk_idx`, `score`.

#### `list_docs() → List[Dict]`

Fetches all metadata, uses `collections.Counter` to count chunks per `doc_id`, and builds one summary entry per document. **Return schema per doc:**

```python
{
    "doc_id":      str,
    "doc_name":    str,
    "doc_type":    str,
    "chunk_count": int,
}
```

Results are sorted alphabetically by `doc_name.lower()`.

#### `remove_doc(doc_id: str) → None`

```python
self._doc_col.delete(where={"doc_id": doc_id})
```

Deletes all chunks belonging to the given document from ChromaDB in a single filtered delete call.

---

## 4. ingest/rfc_fetcher.py — RFC Downloader

**Purpose:** Download RFC `.txt` files from `rfc-editor.org` with local disk caching and exponential-backoff retry.

### `fetch_rfc(rfc_no: int, force_refresh: bool = False) → str`

```
1. Ensure RFC_CACHE_DIR exists (os.makedirs)
2. cache_path = RFC_CACHE_DIR/rfc{rfc_no}.txt
3. If force_refresh=False AND cache_path exists → read and return from disk
4. Else fetch from https://www.rfc-editor.org/rfc/rfc{rfc_no}.txt
   ├── Attempt 1: requests.get(url, timeout=30)
   ├── On failure: wait 2^0 = 1s, retry
   ├── Attempt 2: wait 2^1 = 2s, retry
   └── Attempt 3: raise RuntimeError if still failing
5. Write response text to cache_path
6. Return text
```

**Cache behaviour:** The cache is permanent until `force_refresh=True` is passed. This means RFC text is downloaded once and reused across all app restarts. The `Re-index RFCs` button in `app.py` clears the ChromaDB collection but does NOT clear the file cache — re-indexing re-reads from disk, which is much faster than re-downloading.

### `fetch_all_rfcs(force_refresh: bool = False) → Dict[int, str]`

Iterates over `config.RFC_NUMBERS` and calls `fetch_rfc()` for each. If any single RFC fails (network error, 404), it logs an error and continues — the return dict simply omits that RFC number. This prevents a single bad RFC from blocking the entire index build.

**Returns:** `{rfc_no: full_text_string}` for all successfully fetched RFCs.

---

## 5. ingest/rfc_chunker.py — RFC Text Processor

**Purpose:** Transform raw RFC `.txt` content into structured, overlapping chunks ready for vector store insertion.

### Module-level Compiled Regexes

| Name | Pattern | Purpose |
|---|---|---|
| `_PAGE_BREAK` | `\n[^\n]{0,100}\[Page\s+\d+\]\s*\n...` | Remove RFC page-break artifacts (footer + optional next-page header) |
| `_SECTION_HDR` | `^(\d+(?:\.\d+)*\.?)\s{2,}(\S[^\n]*)` | Match numbered section headings: `"3.1.  Title"` |
| `_APPENDIX_HDR` | `^(Appendix\s+[A-Z]\.?\|[A-Z]\.)\s{2,}(...)` | Match appendix headings: `"Appendix A.  Title"` |

### `_clean_rfc_text(text: str) → str`

```
1. _PAGE_BREAK.sub('\n', text)   → remove page-break lines
2. \r\n → \n                     → normalise line endings
3. \n{3,} → \n\n                 → collapse excess blank lines
4. strip()
```

### `_split_into_sections(text: str) → List[Dict[str, str]]`

```
1. Find all _SECTION_HDR and _APPENDIX_HDR matches
2. Sort all matches by character offset
3. If no matches: return [{"section_no": "0", "section_title": "Full Document", "content": text}]
4. Extract preamble (text before first match) as section "0 — Preamble / Abstract"
5. For each match i:
   content = text[match[i].start : match[i+1].start]  (last section goes to end)
   section_no  = match.group(1).rstrip(".")   e.g. "8.1.2"
   section_title = match.group(2).strip()
```

**Return schema per section:**
```python
{"section_no": str, "section_title": str, "content": str}
```

### `_chunk_text(text: str, chunk_size=2000, overlap=300) → List[str]`

Paragraph-boundary-aware overlapping chunker:

```
If len(text) <= chunk_size: return [text]

start = 0
while start < len(text):
    end = min(start + chunk_size, len(text))

    if end < len(text):
        # Prefer breaking at \n\n in the latter half of the window
        boundary = text.rfind("\n\n", start + chunk_size//2, end)
        if boundary != -1: end = boundary

    chunks.append(text[start:end].strip())
    if end >= len(text): break
    start = end - overlap      ← 300-char overlap
```

This ensures that paragraph context is never split mid-sentence and that adjacent chunks share 300 characters of overlap to preserve retrieval continuity.

### `chunk_rfc(rfc_no: int, text: str) → List[Dict[str, Any]]`

The main public function. Composes the above helpers:

```
1. meta = RFC_META.get(rfc_no)
2. clean = _clean_rfc_text(text)
3. sections = _split_into_sections(clean)
4. For each section:
   sub_chunks = _chunk_text(section.content)
   For each sub_chunk (idx):
     chunks.append({
       "text":          sub_chunk,
       "rfc_no":        rfc_no,
       "rfc_title":     meta["title"],
       "section_no":    section.section_no,
       "section_title": section.section_title,
       "chunk_idx":     idx,
       "id":            f"rfc{rfc_no}_s{section_no.replace('.','_')}_c{idx}",
     })
```

The `id` format `rfc3261_s8_1_2_c0` is stable across re-index runs, making upserts safe and idempotent.

---

## 6. ingest/doc_ingest.py — User Document Ingestion

**Purpose:** Parse user-uploaded files or fetched URLs into text chunks for the `user_docs` ChromaDB collection.

### Module-level Constants

```python
_CHUNK_SIZE    = 1500  # chars per chunk (smaller than RFC chunks — user docs vary widely)
_CHUNK_OVERLAP = 200
_URL_HEADERS   = {"User-Agent": "Mozilla/5.0 ..."}  # Chrome UA to avoid bot blocking
```

### `_clean(text: str) → str`

Normalises line endings (`\r\n` → `\n`), collapses excess blank lines, collapses multiple spaces/tabs into single spaces.

### `_split_chunks(text: str) → List[str]`

Paragraph-aware chunker. Algorithm:

```
1. Split text on \n\n+ → list of paragraphs
2. Accumulate paragraphs into current chunk
3. When adding next paragraph would exceed _CHUNK_SIZE:
   - Append current to chunks
   - Carry over last _CHUNK_OVERLAP chars as new current (overlap)
4. Hard-split any chunk still > _CHUNK_SIZE * 1.5 (single huge paragraph)
```

### `_to_chunk_dicts(texts, doc_id, doc_name, doc_type) → List[Dict]`

Creates the canonical chunk dict format for VectorStore:

```python
{
    "id":        f"{doc_id}_c{i}_{uuid.uuid4().hex[:6]}",  # unique per chunk
    "text":      text_string,
    "doc_id":    doc_id,
    "doc_name":  doc_name,
    "doc_type":  doc_type,
    "chunk_idx": i,
}
```

The 6-char UUID suffix ensures IDs are globally unique even if the same file is uploaded twice with different `doc_id` values (prevents accidental ChromaDB upsert collisions).

### Format-specific Parsers

#### `_parse_pdf(content: bytes, doc_id, doc_name) → List[Dict]`

```python
reader = PdfReader(io.BytesIO(content))
pages = [_clean(page.extract_text() or "") for page in reader.pages]
full = "\n\n".join(non_empty_pages)
chunks = _split_chunks(full)
return _to_chunk_dicts(chunks, doc_id, doc_name, "pdf")
```

Uses `pypdf.PdfReader`. Pages are joined with double newlines to preserve paragraph structure. Empty pages (scanned-image PDFs with no text layer) are filtered out.

#### `_parse_docx(content: bytes, doc_id, doc_name) → List[Dict]`

```python
doc = Document(io.BytesIO(content))
paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
full = _clean("\n\n".join(paras))
```

Uses `python-docx`. Each `Paragraph` object's text is extracted; empty paragraphs (blank lines in Word) are dropped.

#### `_parse_html(content: bytes, doc_id, doc_name) → List[Dict]`

```python
soup = BeautifulSoup(content, "lxml")
for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
    tag.decompose()
full = _clean(soup.get_text(separator="\n"))
```

Strips boilerplate HTML elements before extracting text, avoiding noise from navigation menus, cookie banners, etc.

#### `_parse_txt(content: bytes, doc_id, doc_name) → List[Dict]`

Decodes as UTF-8 with `errors="ignore"` (gracefully handles non-UTF-8 characters), cleans, and chunks. Used for `.txt`, `.md`, `.log`, `.csv`, and any unknown extension.

### `ingest_file(content, filename, doc_id=None) → Tuple[List[Dict], str]`

**Signature:**
```python
ingest_file(
    content:  bytes,          # raw file bytes
    filename: str,            # original name (used for ext detection + display)
    doc_id:   Optional[str],  # auto-generated as "DOC-{8 hex chars}" if None
) -> (chunks: List[Dict], doc_id: str)
```

Extension detection: `filename.rsplit(".", 1)[-1].lower()`. Routes to the correct parser. Raises `ValueError` for `.doc` files (legacy Word format — instructs user to convert to `.docx`).

### `ingest_url(url, doc_id=None) → Tuple[List[Dict], str]`

```
1. requests.get(url, timeout=30, headers=_URL_HEADERS)
2. r.raise_for_status()
3. doc_name = netloc + path from parsed URL
4. If "pdf" in Content-Type or URL ends with .pdf:
     → _parse_pdf(r.content, ...)
   Else:
     → _parse_html(r.content, ...)
5. Return (chunks, doc_id)
```

`doc_name` is derived from the URL path rather than using the full URL, giving a cleaner display name in the UI (e.g., `"tools.ietf.org/html/rfc3261"` instead of `"https://tools.ietf.org/html/rfc3261"`).

---

## 7. ingest/parsers/text_parser.py — SIP Text Trace Parser

**Purpose:** Extract structured SIP message dicts from plain-text trace files (sipp logs, sngrep exports, copy-pasted SIP captures).

### Module-level Compiled Regexes

```python
_SIP_METHODS = "INVITE|REGISTER|BYE|ACK|CANCEL|OPTIONS|SUBSCRIBE|NOTIFY|..."

_REQUEST_LINE  = re.compile(rf'^({_SIP_METHODS})\s+\S+\s+SIP/2\.0', re.IGNORECASE)
_RESPONSE_LINE = re.compile(r'^SIP/2\.0\s+(\d{3})\s+(.*)', re.IGNORECASE)
_MSG_BOUNDARY  = re.compile(
    rf'(?=^(?:{_SIP_METHODS})\s|\bSIP/2\.0\s+\d{{3}}\b)',
    re.MULTILINE | re.IGNORECASE,
)
```

`_MSG_BOUNDARY` uses a zero-width lookahead so that `re.split()` does not consume any text — each segment starts with the full first line of the SIP message.

### `_parse_headers(header_block: str) → Dict[str, str]`

Splits on `":"`, lowercases the key, strips whitespace. Last-value-wins for duplicate header names (correct per RFC 3261 for most headers). Returns `{"call-id": "...", "cseq": "...", "via": "...", ...}`.

### `build_message(raw: str) → Dict[str, Any]`

Parses a single complete SIP message string. This is the core parsing function, also called by `pcap_parser.py`.

```
1. Split on first blank line (\n\r?\n) → header_block, body
2. Match first line against _REQUEST_LINE or _RESPONSE_LINE
3. Build base dict with: raw, first_line, body, src_ip="", dst_ip="", src_port=0, dst_port=0
4. Set type = "request" | "response" | "unknown"
5. Set method, response_code, reason from regex groups
6. _parse_headers on remaining lines
7. Extract compact header forms (RFC 3261 §7.3.3):
   call_id = headers["call-id"] or headers["i"]
   cseq    = headers["cseq"]
   from    = headers["from"]   or headers["f"]
   to      = headers["to"]     or headers["t"]
   via     = headers["via"]    or headers["v"]
   contact = headers["contact"] or headers["m"]
```

**Return schema:**

```python
{
    "raw":           str,   # full original message text
    "type":          str,   # "request" | "response" | "unknown"
    "method":        str,   # "INVITE", "BYE", etc. (empty for responses)
    "response_code": int,   # 0 for requests, 401/488/... for responses
    "reason":        str,   # reason phrase for responses
    "call_id":       str,
    "cseq":          str,
    "from":          str,
    "to":            str,
    "via":           str,
    "contact":       str,
    "headers":       dict,  # full lowercase-key header dict
    "body":          str,   # SDP or other message body
    "src_ip":        str,   # "" — filled by PCAP parser when available
    "dst_ip":        str,
    "src_port":      int,
    "dst_port":      int,
}
```

### `parse_text_trace(content: str) → List[Dict[str, Any]]`

```
1. _MSG_BOUNDARY.split(content) → list of raw message segments
2. For each segment:
   a. Strip whitespace; skip empty
   b. Check first line matches _REQUEST_LINE or _RESPONSE_LINE
   c. Call build_message(segment)
   d. Append to messages
3. Return messages
```

---

## 8. ingest/parsers/html_parser.py — SIP HTML Trace Parser

**Purpose:** Extract SIP message text from HTML trace files (Wireshark "File → Export as HTML", sngrep HTML exports, custom HTML captures).

### `_extract_sip_blocks(soup: BeautifulSoup) → List[str]`

Four-strategy cascade (stops at the first strategy that finds anything):

```
Strategy 1: <pre> tags
  → get_text(separator="\n")
  → filter: contains SIP/2.0, INVITE, REGISTER, BYE, or ACK
  → Used for: Wireshark HTML export, sngrep HTML

Strategy 2: <td> cells in tables
  → filter: contains SIP signal AND len > 80 chars
  → Used for: Wireshark packet-detail tables

Strategy 3: <div>, <p>, <span> elements
  → filter: contains SIP signal AND len > 80 chars
  → Used for: custom web-based SIP loggers

Strategy 4: Full page text (last resort)
  → soup.get_text(separator="\n")
  → Used when SIP content is not isolated in any structural element
```

### `parse_html_trace(content: str) → List[Dict[str, Any]]`

```
1. soup = BeautifulSoup(content, "lxml")
2. Decompose: <script>, <style>, <nav>, <head>, <footer>
3. blocks = _extract_sip_blocks(soup)
4. If no blocks: log warning, return []
5. combined = "\n\n".join(blocks)
6. Delegate to parse_text_trace(combined)
7. Return messages
```

The HTML parser is a thin adapter: it extracts raw SIP text from the HTML structure, then hands off to the identical text parser pipeline.

---

## 9. ingest/parsers/pcap_parser.py — PCAP Trace Parser

**Purpose:** Extract both SIP messages and RTP stream summaries from binary PCAP/PCAPNG capture files using Scapy.

### Module-level Constants

```python
_SIP_PORTS    = {5060, 5061}          # Well-known SIP ports for fast-path filtering
_SIP_PREFIXES = (b"SIP/2.0", b"INVITE ", b"REGISTER ", ...)  # 14 method prefixes
_RTP_PAYLOAD_TYPES = {0: "PCMU — G.711 μ-law", 8: "PCMA — G.711 A-law", ...}  # Static PT registry
_PT_CLOCK = {0: 8000, 8: 8000, 14: 90000, ...}  # Clock rates per payload type
```

### `_looks_like_sip(payload: bytes) → bool`

Checks if the first 20 bytes of a UDP/TCP payload start with any SIP method prefix or `b"SIP/2.0"`. Used as a quick heuristic for non-standard ports.

### `_try_parse_rtp(payload: bytes) → Optional[Dict]`

Interprets a UDP payload as an RTP packet per RFC 3550 header layout:

```
Byte 0: version (bits 7-6) must be 2, padding, extension, CC
Byte 1: marker bit (bit 7), payload type (bits 6-0)
Bytes 2-3: sequence number (big-endian uint16)
Bytes 4-7: timestamp (big-endian uint32)
Bytes 8-11: SSRC (big-endian uint32)
```

Returns `None` if payload < 12 bytes, version ≠ 2, or payload type 200–204 (RTCP).

### `_extract_rtp_streams(packets) → List[Dict]`

Processes all packets to produce **one summary dict per SSRC** (not per packet):

```
For each UDP + Raw packet:
  1. Skip SIP ports
  2. _try_parse_rtp(payload)
  3. Group by SSRC:
     First seen: create stream entry with initial seq, ts, pkt_count=1
     Subsequent: update last_seq, last_ts, pkt_count, marker_count

For each stream:
  1. Look up payload type name from _RTP_PAYLOAD_TYPES (or "Dynamic PT N")
  2. duration = (last_ts - first_ts) / clock_rate
     (handles 32-bit timestamp wrap-around with & 0xFFFFFFFF)
  3. Build human-readable summary string:
     "RTP Stream SSRC=0xABCD1234:\n  Payload Type : 0 (PCMU — G.711 μ-law)\n  ..."
  4. Return as message dict with type="rtp_stream", method="RTP"
```

Storing one summary per SSRC keeps the vector store compact — a 10-minute call generates ~30,000 RTP packets but only 2–4 streams.

### `parse_pcap_trace(file_path: str) → List[Dict]`

Main public function:

```
1. Import scapy (raises RuntimeError if not installed)
2. rdpcap(file_path) → packets list
3. For each packet with Raw layer:
   a. Get UDP or TCP transport
   b. Skip non-SIP ports unless _looks_like_sip(payload)
   c. Decode payload as UTF-8
   d. Match first line against _REQUEST_LINE or _RESPONSE_LINE
   e. build_message(text) + overlay src_ip, dst_ip, src_port, dst_port from IP/IPv6 layer
4. Append SIP messages to messages list
5. rtp_entries = _extract_rtp_streams(packets)
6. messages.extend(rtp_entries)
7. Return combined list
```

Both SIP and RTP entries are stored in the same trace collection, enabling the agent to answer questions about both signaling and media in a unified semantic search.

---

## 10. agent/tools.py — Tool Definitions & Implementations

**Purpose:** Dual-purpose module — defines the JSON schema for each tool (sent to the LLM) and the Python implementation of each tool (executed when the LLM calls it).

### `TOOL_DEFINITIONS: List[Dict]`

A list of 6 dicts in OpenAI/Groq function-calling format. Each entry has:

```
{
  "type": "function",
  "function": {
    "name":        str,
    "description": str,   # tells the LLM when and how to use this tool
    "parameters": {
      "type": "object",
      "properties": {...},
      "required": [...]
    }
  }
}
```

| Tool | Required Params | Optional Params |
|---|---|---|
| `search_rfc` | `query: str` | `rfc_filter: int[]` |
| `search_trace` | `query: str` | — |
| `reconstruct_call_flow` | — | `call_id_filter: str` |
| `diagnose_sip_error` | `response_code: int` | `context: str` |
| `cross_reference` | `observation: str` | `topic: str` |
| `search_docs` | `query: str` | `doc_filter: str[]` |

### `execute_tool(tool_name, tool_args, vector_store) → Any`

Central dispatch function. Routes calls to the correct implementation:

```python
dispatch = {
    "search_rfc":              _search_rfc,
    "search_trace":            _search_trace,
    "reconstruct_call_flow":   _reconstruct_call_flow,
    "diagnose_sip_error":      _diagnose_sip_error,
    "cross_reference":         _cross_reference,
    "search_docs":             _search_docs,
}
fn = dispatch.get(tool_name)
```

Wraps the call in `try/except` and returns `{"error": str(exc)}` on failure so the LLM receives structured error information rather than an unhandled exception.

### Tool Implementations

#### `_search_rfc(args, vs) → Dict`

```
query      = args["query"]
rfc_filter = args.get("rfc_filter") or None
hits       = vs.search_rfc(query, top_k=5, rfc_filter=rfc_filter)
```

**Return on success:**
```python
{
  "query": query,
  "results": [
    {
      "source":    "RFC 3261 — SIP: Session Initiation Protocol",
      "section":   "§8.1.1 Generating the Request",
      "relevance": 0.87,
      "content":   "... (first 1200 chars of chunk text) ...",
    },
    # ... up to 5 results
  ]
}
```

Per-result content is capped at 1200 chars to keep the LLM context window manageable.

#### `_search_trace(args, vs) → Dict`

Returns early with a helpful message if `vs.trace_count() == 0`. When results exist:

```python
{
  "query": query,
  "trace_results": [
    {
      "label":     "INVITE",       # method or response_code string
      "call_id":   "...",
      "cseq":      "1 INVITE",
      "src":       "192.168.1.10:", # IP:port
      "dst":       "10.0.0.1:5060",
      "relevance": 0.82,
      "message":   "... (first 900 chars) ...",
    }
  ]
}
```

#### `_reconstruct_call_flow(args, vs) → Dict`

Groups all trace messages by `call_id`, sorts each group by CSeq number:

```python
def _cseq_num(m):
    return int(str(m.get("cseq", "0")).split()[0])  # "1 INVITE" → 1
```

Produces step strings like `"192.168.1.10  →  10.0.0.1 : INVITE  [CSeq: 1 INVITE]"`.

**Return schema:**
```python
{
  "total_messages": int,
  "dialogs": [
    {
      "call_id":       str,
      "message_count": int,
      "flow":          ["src → dst : METHOD [CSeq: ...]", ...],
    }
  ]
}
```

#### `_diagnose_sip_error(args, vs) → Dict`

Constructs a targeted query: `"SIP {code} response code definition meaning behavior {context}"`. Adds RFC 5630/5922 to the filter for authentication-related codes (401, 407, 403, 421, 494):

```python
rfc_filter = [3261]
if code in (401, 407, 403, 421, 494):
    rfc_filter += [5630, 5922]
hits = vs.search_rfc(query, top_k=4, rfc_filter=rfc_filter)
```

#### `_cross_reference(args, vs) → Dict`

Combines optional topic hint with observation: `f"{topic}: {observation}"`. Searches all RFCs (no filter) since RFC-compliance questions may span multiple documents.

#### `_search_docs(args, vs) → Dict`

Returns `{"message": "No user documents..."}` if `vs.doc_count() == 0`. Otherwise:

```python
{
  "query": query,
  "results": [
    {
      "source":    "my_document.pdf (chunk 3)",
      "doc_id":    "DOC-A1B2C3D4",
      "doc_type":  "pdf",
      "relevance": 0.79,
      "content":   "... (first 1200 chars) ...",
    }
  ]
}
```

---

## 11. agent/orchestrator.py — LangGraph Agent Engine

**Purpose:** Compile the LangGraph StateGraph, manage LLM calls with fallback, execute the reasoning loop, and expose a clean `run()` API to `app.py`.

### State Schema: `AgentState`

```python
class AgentState(TypedDict):
    messages:          Annotated[List[BaseMessage], add_messages]
    groq_rate_limited: bool
```

`add_messages` is a LangGraph reducer that deduplicates messages by ID instead of blindly appending. This prevents duplicate tool result messages when the graph loops.

### Pydantic Input Schemas

Six Pydantic models provide type validation and structured argument parsing for LangGraph's ToolNode:

| Schema Class | Fields |
|---|---|
| `_SearchRFCInput` | `query: str`, `rfc_filter: Optional[List[int]]` |
| `_SearchTraceInput` | `query: str` |
| `_ReconstructCallFlowInput` | `call_id_filter: Optional[str]` |
| `_DiagnoseSIPErrorInput` | `response_code: int`, `context: Optional[str]` |
| `_CrossReferenceInput` | `observation: str`, `topic: Optional[str]` |
| `_SearchDocsInput` | `query: str`, `doc_filter: Optional[List[str]]` |

These schemas are passed as `args_schema` to `StructuredTool.from_function()`, enabling ToolNode to validate and parse the JSON arguments the LLM produces.

### `_make_lc_tools(vector_store) → List[StructuredTool]`

Creates 6 LangChain `StructuredTool` instances. Each wraps the corresponding `execute_tool()` call and returns `json.dumps(result)` (ToolNode requires string return values).

The functions are defined as closures inside `_make_lc_tools`, capturing `vector_store` from the enclosing scope. This avoids global state while keeping tool implementations stateless.

### `_is_rate_limit(exc: Exception) → bool`

Returns `True` if the exception message contains `"429"`, `"rate_limit"`, `"rate limit"`, or `"ratelimit"` (case-insensitive). Used to distinguish Groq 429 errors from other failures.

### `_build_graph(vector_store) → CompiledGraph`

```
1. lc_tools  = _make_lc_tools(vector_store)
2. tool_node = ToolNode(lc_tools)           ← handles tool execution automatically

3. Four LLM variants:
   groq_with_tools  = ChatGroq(...).bind_tools(lc_tools)   ← primary
   groq_plain       = ChatGroq(...)                         ← forced-final fallback
   ollama_with_tools= ChatOllama(...).bind_tools(lc_tools)  ← rate-limit fallback
   ollama_plain     = ChatOllama(...)                       ← rate-limit forced-final

4. agent_node function (closure):
   a. Count AI turns already in state
   b. is_final = (turns >= MAX_ITERATIONS=14)
   c. If is_final: inject "provide your final answer now" message
   d. Choose primary/fallback based on is_final and groq_rate_limited
   e. Try Groq → on 429: try Ollama → on other error: retry Groq plain

5. graph = StateGraph(AgentState)
   graph.add_node("agent", agent_node)
   graph.add_node("tools", tool_node)
   graph.add_edge(START, "agent")
   graph.add_conditional_edges("agent", tools_condition)  ← → "tools" or END
   graph.add_edge("tools", "agent")
   return graph.compile()
```

**LLM selection logic in `agent_node`:**

```
                is_final=False          is_final=True
               ┌───────────────────┐   ┌──────────────────┐
No rate limit  │ groq_with_tools   │   │ groq_plain        │
               └───────────────────┘   └──────────────────┘
               ┌───────────────────┐   ┌──────────────────┐
Rate limited   │ ollama_with_tools │   │ ollama_plain      │
               └───────────────────┘   └──────────────────┘
```

### `_TOOL_LEAK` Regex and `_sanitize_answer(text)`

```python
_TOOL_LEAK = re.compile(
    r"\b(?:search_rfc|search_trace|reconstruct_call_flow"
    r"|diagnose_sip_error|cross_reference|search_docs)\b"
)
```

`_sanitize_answer` applies this regex line-by-line:
- Entire bullet/numbered list items containing a tool name are dropped.
- Prose lines have only the offending sentences removed; non-offending sentences are kept.
- Consecutive blank lines > 2 are collapsed.

This is a deterministic post-processing step that acts as a safety net against LLM non-compliance with the "never mention tool names" instruction.

### Class: `AgentOrchestrator`

#### `__init__(self, vector_store)`

```python
self._vs    = vector_store
self._graph = _build_graph(vector_store)
```

The graph is compiled once on construction. All calls to `run()` reuse the same compiled graph.

#### `run(self, query, trace_active=False, docs_info=None) → Dict`

**Signature:**
```python
run(
    query:        str,
    trace_active: bool = False,             # True when a trace is loaded in the UI
    docs_info:    Optional[List[Dict]] = None,  # from vs.list_docs()
) -> {
    "answer":            str,
    "reasoning_trace":   List[Dict],        # [{tool, args, result_preview}]
    "call_flow":         Optional[Dict],    # reconstruct_call_flow result if called
    "groq_rate_limited": bool,
    "ollama_model":      str,               # OLLAMA_MODEL name if fallback was used
}
```

**Execution flow:**

```
1. Build trace_status string (injected into SystemMessage):
   - If trace_active and trace_count > 0: MANDATORY trace workflow instructions
   - Else: "No trace loaded. Answer from RFC knowledge base only."

2. Build doc_status string:
   - If docs_info: list each doc (name, doc_id, type, chunk count)
   - Else: "No user documents are currently uploaded."

3. graph.invoke({
     "messages": [
       SystemMessage(SYSTEM_PROMPT + trace_status + doc_status),
       HumanMessage(query),
     ],
     "groq_rate_limited": False,
   }, config={"recursion_limit": MAX_ITERATIONS * 2 + 5})

4. Parse reasoning_trace from ToolMessages:
   - Build tc_lookup {tool_call_id → {name, args}} from AIMessages
   - For each ToolMessage: append {tool, args, result_preview[:500]}
   - If tool == "reconstruct_call_flow": parse JSON → call_flow_result

5. _sanitize_answer(last_message.content)

6. Return result dict
```

The `recursion_limit` guard (`MAX_ITERATIONS * 2 + 5 = 33`) is the LangGraph hard stop. It fires before the `MAX_ITERATIONS` AI-turn check inside `agent_node`, but the `agent_node` check is hit first in normal operation.

#### `get_graph_png(path)` / `get_graph_mermaid()`

Utility methods to export the LangGraph topology as a PNG or Mermaid diagram string. Used for documentation and debugging.

---

## 12. agent/prompts.py — System Prompt

**Purpose:** A single module-level string constant `SYSTEM_PROMPT` (~215 lines) that defines the agent's identity, knowledge, tool usage policy, response formatting rules, and output hygiene constraints.

### Structure of SYSTEM_PROMPT

| Section | Purpose |
|---|---|
| **Identity** | Declares expertise in 23 specific RFCs, lists each RFC by number and title |
| **Tool Catalogue** | Explains each of the 6 tools — when to use, what it returns |
| **Behavior Guidelines** | Mandatory tool-call-first policy; rfc_filter requirements; trace workflow |
| **Trace Diagnosis Playbook** | Per-error-code step-by-step playbooks (488, 401/407, 503, 486/480, general 4xx/5xx) |
| **Response Formatting** | Mandatory `###` headings, `**bold**` rules, `<u>underline</u>` rules, bullet vs numbered list rules |
| **Inline Citations** | `*(RFC XXXX, §Y.Y)*` format, doc citation `*(doc name, chunk N)*` |
| **Follow-up Questions** | When and how to ask (only for genuinely ambiguous questions) |
| **Output Hygiene** | STRICT rules: never mention tool names; never write "Diagnostic Approach"; never end with open invitation |

### Key Behavioral Constraints

1. **Always call at least one tool** before writing a final answer — never answer from training knowledge alone.
2. **rfc_filter is mandatory** when the user cites a specific RFC number.
3. **Acronym expansion** — short acronyms like `"pcfg"` must be expanded to `"pcfg potential configuration"` before searching.
4. **Trace workflow** — `reconstruct_call_flow` must be called first, then `search_trace` one or more times, before writing any trace analysis.
5. **Tool name invisibility** — tool names must never appear in the final response.

The `SYSTEM_PROMPT` string is concatenated at runtime in `orchestrator.run()` with `trace_status` and `doc_status` dynamic sections. The combined string forms the `SystemMessage` passed to the LLM.

---

## 13. app.py — Streamlit UI Layer

**Purpose:** The Streamlit entry point. Manages session state, renders the sidebar (RFC knowledge base status, trace uploader, document library), the chat interface, and drives the `AgentOrchestrator`.

### Module-level Data

#### `RFC_CATEGORIES`
A dict mapping category name → `{color, bg, text, rfcs: {rfc_no: title}}`. Used to render the RFC knowledge base card grid in the sidebar with colour-coded category pills.

#### `EXAMPLE_QUESTIONS`
List of 6 `(emoji, question_text)` tuples. Rendered as clickable suggestion buttons when the chat history is empty.

#### `AUTO_ANALYSIS_PROMPT`
A large multi-line string (9-section structured prompt) injected automatically when a trace is uploaded and the user clicks "Auto-Analyse Trace". Forces the LLM to produce a comprehensive, structured diagnostic report.

### Cached Resources

#### `@st.cache_resource def _get_vector_store() → VectorStore`

Creates a single `VectorStore` instance shared across all Streamlit reruns in the same server process. `@st.cache_resource` is sticky — it survives page reruns but is cleared by `_get_vector_store.clear()`.

**Hot-reload caveat:** Streamlit hot-reloads `app.py` on file save but keeps `sys.modules` cached for other modules. If `vector_store.py` is edited and saved, the cached `VectorStore` instance is still the old class. The `clear_rfcs()` fallback in the Re-index button (`try/except AttributeError`) handles this by accessing `vs._client` directly.

#### `@st.cache_resource def _get_orchestrator(vs) → AgentOrchestrator`

Creates a single `AgentOrchestrator` instance per `VectorStore` instance. The `vs` parameter serves as a cache key — if `_get_vector_store.clear()` is called, the orchestrator cache is invalidated on the next call since `vs` identity changes.

### `_inject_css() → None`

Injects ~250 lines of CSS via `st.markdown(..., unsafe_allow_html=True)`. Key styling rules:

| Element | Style |
|---|---|
| App background | `#f7f8fc` (very light grey-blue) |
| Sidebar | `#ffffff` with `1px solid #e8e8f8` border |
| Chat input container | White with 2px rainbow gradient top stripe |
| Chat input pill | Gradient border: `#6366f1 → #06b6d4 → #8b5cf6` via `padding-box`/`border-box` trick |
| Submit button | Gradient: `#6366f1 → #06b6d4` |
| Buttons | White background, `#4f46e5` text, hover lifts with shadow |
| Scrollbar | 5px, indigo-tinted thumb |

### `_ensure_rfc_index(vs: VectorStore) → None`

Called on every app startup if `vs.rfc_count() == 0`. Runs the full RFC indexing pipeline with a 5-step numbered progress display inside a `st.status()` expander:

```
Step 1: Prepare RFC cache directory
Step 2: Download 25 RFCs (fetch_all_rfcs)
Step 3: Parse and chunk RFC text (chunk_rfc per RFC)
Step 4: Embed and index chunks (vs.add_rfc_chunks) ← slowest step; shows timing note
Step 5: Finalise — log total chunk count
```

On completion, the expander collapses (`expanded=False`) and shows a success message.

### `_render_sidebar(vs) → Tuple[bool, Optional[List[Dict]]]`

Renders the full sidebar and returns `(trace_active, docs_info)`.

**Sidebar sections:**

1. **App Header** — Title, subtitle, Re-index button with `try/except AttributeError` fallback for hot-reload safety.

2. **RFC Knowledge Base** — RFC chunk count chip. Category grid with per-RFC pills rendered from `RFC_CATEGORIES`.

3. `st.divider()`

4. **SIP Trace** — File uploader accepting `.txt`, `.html`, `.pcap`, `.pcapng`.
   - On upload: routes to `parse_text_trace`, `parse_html_trace`, or `parse_pcap_trace` based on extension.
   - Calls `vs.clear_trace()` then `vs.add_trace_messages(messages)`.
   - Shows success toast with message count.
   - Shows "Auto-Analyse Trace" button that injects `AUTO_ANALYSIS_PROMPT`.

5. `st.divider()`

6. **Document Library** — Header with live doc chunk count.
   - File uploader: accepts `.pdf`, `.docx`, `.html`, `.htm`, `.txt`, `.md`.
   - URL input + "Fetch URL" button in 3:1 column layout.
   - On ingest: calls `ingest_file()` or `ingest_url()`, validates `chunks` is non-empty (shows warning if zero), calls `vs.add_doc_chunks(chunks)`.
   - Per-document cards with doc name, type badge, chunk count, and "Remove" button.
   - "Clear all documents" button calls `vs.clear_docs()`.

### `_render_chat_area(vs, trace_active, docs_info) → None`

Manages the main chat panel:

```
1. Retrieve st.session_state["messages"] (list of {role, content} dicts)
2. If empty: render EXAMPLE_QUESTIONS as buttons; clicking one populates query
3. For each message in history:
   - st.chat_message("user", avatar="🧑‍💻")
   - st.chat_message("assistant", avatar="🤖")
     └── If message has "reasoning_trace": render collapsible expander
     └── If message has "call_flow": render structured call flow expander
     └── If message has "groq_rate_limited": show info banner
4. If pending query in session_state:
   - Show user message immediately
   - Show spinner while orchestrator.run() executes
   - Append response to session_state["messages"]
   - st.rerun() to re-render cleanly
5. st.chat_input("Ask a SIP or RTP question...") → stores to session_state
```

**Reasoning trace expander** (inside each assistant message):

```
🔍 Reasoning Trace (N tool calls)
  For each tool call:
    📌 Tool: search_rfc | Args: {...}
    ↳ Result preview (first 500 chars of JSON)
```

**Call flow expander:**

```
📞 Reconstructed Call Flow
  For each dialog:
    Call-ID: ...  (N messages)
      1. 192.168.1.10 → 10.0.0.1 : INVITE  [CSeq: 1 INVITE]
      2. 10.0.0.1 → 192.168.1.10 : 100  [CSeq: 1 INVITE]
      ...
```

### Session State Keys

| Key | Type | Description |
|---|---|---|
| `"messages"` | `List[Dict]` | Chat history: `[{role, content, reasoning_trace?, call_flow?, groq_rate_limited?}]` |
| `"pending_query"` | `Optional[str]` | Query waiting to be processed; set by example buttons or chat_input |
| `"trace_uploaded"` | `bool` | Whether a trace is currently loaded |
| `"auto_analyse"` | `bool` | Triggers AUTO_ANALYSIS_PROMPT injection on next run |

---

## 14. Data Schemas Reference

### ChromaDB Collection: `sip_rfcs`

```
Collection name : "sip_rfcs"
Distance metric : cosine (hnsw:space=cosine)
Embedding model : all-MiniLM-L6-v2 (384-dim)
Persistence     : permanent (not cleared on startup)

Per document:
  id:       "rfc3261_s8_1_2_c0"       (stable, upsert-safe)
  document: <chunk text, up to 2000 chars>
  metadata: {
    rfc_no:        int,
    rfc_title:     str,
    section_no:    str,   e.g. "8.1.2"
    section_title: str,   e.g. "Generating the Request"
    chunk_idx:     int,
  }
```

### ChromaDB Collection: `sip_trace`

```
Collection name : "sip_trace"
Distance metric : cosine
Persistence     : ephemeral (deleted + recreated on each VectorStore.__init__)

Per document:
  id:       "trace_msg_0", "trace_msg_1", ...
  document: <full raw SIP message text or RTP stream summary>
  metadata: {
    msg_type:      str,   "request" | "response" | "rtp_stream" | "unknown"
    method:        str,   e.g. "INVITE" (empty for responses/rtp)
    response_code: int,   e.g. 488, 0 for requests
    call_id:       str,   truncated to 64 chars
    cseq:          str,   truncated to 32 chars
    src_ip:        str,   truncated to 40 chars
    dst_ip:        str,   truncated to 40 chars
  }
```

### ChromaDB Collection: `user_docs`

```
Collection name : "user_docs"
Distance metric : cosine
Persistence     : permanent (survives app restarts)

Per document:
  id:       "DOC-A1B2C3D4_c3_f7a2c1"  (doc_id + chunk_idx + uuid6)
  document: <chunk text, up to ~1500 chars>
  metadata: {
    doc_id:    str,   e.g. "DOC-A1B2C3D4"
    doc_name:  str,   original filename or URL path
    doc_type:  str,   "pdf" | "docx" | "html" | "txt"
    chunk_idx: int,
  }
```

### Agent `run()` Return Dict

```python
{
  "answer":            str,      # sanitised markdown response
  "reasoning_trace":   [         # one entry per tool call made
    {
      "tool":           str,     # e.g. "search_rfc"
      "args":           dict,    # arguments as passed by the LLM
      "result_preview": str,     # first 500 chars of JSON result
    }
  ],
  "call_flow":  Optional[{       # present only if reconstruct_call_flow was called
    "total_messages": int,
    "dialogs": [
      {
        "call_id":       str,
        "message_count": int,
        "flow":          List[str],  # step strings
      }
    ]
  }],
  "groq_rate_limited": bool,
  "ollama_model":      str,      # non-empty only when Ollama was used
}
```

---

## 15. Inter-Module Call Flow Diagrams

### A. RFC Indexing Flow (first startup or Re-index)

```
app.py:_ensure_rfc_index(vs)
  │
  ├─► rfc_fetcher.fetch_all_rfcs()
  │     ├── for each RFC in RFC_NUMBERS:
  │     │     └── fetch_rfc(rfc_no)
  │     │           ├── Check rfc_cache/rfc{N}.txt (cache hit → read file)
  │     │           └── requests.get("https://rfc-editor.org/rfc/rfcN.txt")
  │     │                 └── write to cache; return text
  │     └── return {rfc_no: text, ...}
  │
  ├─► for each (rfc_no, text) in rfc_texts:
  │     rfc_chunker.chunk_rfc(rfc_no, text)
  │       ├── _clean_rfc_text(text)
  │       ├── _split_into_sections(clean)
  │       └── _chunk_text(section.content)  ← for each section
  │             └── return List[chunk_dict]
  │
  └─► vs.add_rfc_chunks(all_chunks)
        ├── batch into groups of 500
        └── self._rfc_col.upsert(ids, documents, metadatas)
```

### B. Trace Upload Flow

```
app.py:_render_sidebar → file_uploader callback
  │
  ├─ .txt  → parsers.text_parser.parse_text_trace(content_str)
  │               └── _MSG_BOUNDARY.split → build_message() × N
  │
  ├─ .html → parsers.html_parser.parse_html_trace(content_str)
  │               ├── BeautifulSoup → _extract_sip_blocks()
  │               └── parse_text_trace(combined_blocks)
  │
  └─ .pcap → parsers.pcap_parser.parse_pcap_trace(tmp_file_path)
                  ├── scapy.rdpcap(path)
                  ├── for each packet: build_message(sip_payload)
                  └── _extract_rtp_streams(packets) → stream summaries

  └─► vs.clear_trace()     ← drop old collection
  └─► vs.add_trace_messages(messages)
```

### C. Query Execution Flow (Agent ReAct Loop)

```
app.py → orchestrator.run(query, trace_active, docs_info)
  │
  ├── Build SystemMessage (SYSTEM_PROMPT + trace_status + doc_status)
  ├── Build HumanMessage(query)
  │
  └── graph.invoke({messages: [...], groq_rate_limited: False})
        │
        ├──[START]──► agent_node
        │               ├── ChatGroq.invoke(messages)  ← primary
        │               │     └── AIMessage with tool_calls=[{name, args, id}]
        │               │
        │               └── returns {"messages": [AIMessage]}
        │
        ├──[tools_condition]──► tool_node (has tool_calls? → "tools")
        │                         │
        │                         ├── StructuredTool.search_rfc(query, ...)
        │                         │     └── execute_tool("search_rfc", args, vs)
        │                         │           └── vs.search_rfc(query)
        │                         │                 ├── Pass 1: ChromaDB cosine/HNSW query
        │                         │                 ├── Pass 2: BM25Okapi.get_scores()
        │                         │                 └── _rrf_merge() → top_k results
        │                         │
        │                         └── returns ToolMessage(content=json_str, tool_call_id=...)
        │
        ├──[tools → agent]──► agent_node  ← next iteration
        │               └── processes tool results + decides next action
        │
        └── ... (loop until no tool_calls or MAX_ITERATIONS)
              │
              └──[tools_condition → END]
                    └── last AIMessage.content = final answer

  └── _sanitize_answer(raw_answer)
  └── parse reasoning_trace from ToolMessages
  └── return result dict
```

### D. Document Ingest Flow

```
app.py:_render_sidebar → file upload or URL submit
  │
  ├─ File upload:
  │   ingest_file(bytes, filename, doc_id=None)
  │     ├── auto-generate doc_id = "DOC-{8hex}"
  │     ├── detect extension
  │     ├── _parse_pdf / _parse_docx / _parse_html / _parse_txt
  │     │     └── _split_chunks(full_text) → _to_chunk_dicts(...)
  │     └── return (chunks, doc_id)
  │
  └─ URL fetch:
      ingest_url(url, doc_id=None)
        ├── requests.get(url, headers=_URL_HEADERS)
        ├── detect Content-Type
        ├── _parse_pdf or _parse_html
        └── return (chunks, doc_id)

  └── if not chunks: st.warning("No text extracted")
  └── vs.add_doc_chunks(chunks)
        └── self._doc_col.upsert(ids, documents, metadatas)
```

---

*End of Low Level Design Document*
