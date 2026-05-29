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
14. [observability/trulens_setup.py — RAG Observability](#14-observabilitytrulens_setuppy--rag-observability)
15. [Data Schemas Reference](#15-data-schemas-reference)
16. [Inter-Module Call Flow Diagrams](#16-inter-module-call-flow-diagrams)

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
├── observability/
│   ├── __init__.py
│   └── trulens_setup.py            ← TruLens RAG Triad evaluation (Groq→Ollama Cloud provider)
│
├── trulens_eval.db                  ← TruLens evaluation SQLite DB (git-ignored)
└── app.py                           ← Streamlit application (UI, sidebar, chat loop)
```

**Dependency flow (imports only go downward):**

```
app.py
  ├── agent/orchestrator.py
  │     ├── agent/tools.py
  │     │     └── store/vector_store.py
  │     ├── agent/prompts.py
  │     └── config.py
  ├── ingest/rfc_fetcher.py
  ├── ingest/rfc_chunker.py
  ├── ingest/doc_ingest.py
  ├── ingest/parsers/text_parser.py
  ├── ingest/parsers/html_parser.py
  ├── ingest/parsers/pcap_parser.py
  └── observability/trulens_setup.py
        └── config.py
```

---

## 2. config.py — Global Configuration

**Purpose:** Single source of truth for all tuneable parameters, API keys, model names, paths, RFC numbers, and per-RFC metadata.

### Constants Reference

| Constant | Type | Value / Source | Purpose |
|---|---|---|---|
| `GROQ_API_KEY` | `str` | `env: GROQ_API_KEY` | Groq Cloud authentication |
| `GROQ_MODEL` | `str` | `"meta-llama/llama-4-scout-17b-16e-instruct"` | Primary agent LLM |
| `GROQ_EVAL_MODEL` | `str` | `"llama-3.1-8b-instant"` | Primary eval scoring LLM (lighter model) |
| `OLLAMA_CLOUD_API_KEY` | `str` | `env: OLLAMA_CLOUD_API_KEY` | Ollama Cloud bearer token |
| `OLLAMA_CLOUD_URL` | `str` | `"https://ollama.com"` | Ollama Cloud base endpoint |
| `OLLAMA_CLOUD_MODEL` | `str` | `env: OLLAMA_CLOUD_MODEL` (default `"gpt-oss:120b"`) | Fallback LLM for agent and eval |
| `EMBEDDING_MODEL` | `str` | `"all-MiniLM-L6-v2"` | SentenceTransformers model for ChromaDB |
| `CHROMA_PERSIST_DIR` | `str` | `<project_root>/chroma_db` | ChromaDB on-disk storage path |
| `RFC_CACHE_DIR` | `str` | `<project_root>/rfc_cache` | Local cache for downloaded RFC `.txt` files |
| `RFC_NUMBERS` | `List[int]` | 25 integers | RFCs to index |
| `CHUNK_SIZE` | `int` | `2000` | Max characters per RFC chunk (~500 tokens) |
| `CHUNK_OVERLAP` | `int` | `300` | Overlap between consecutive RFC chunks |
| `TOP_K` | `int` | `6` | Default number of search results returned |

---

## 3. store/vector_store.py — ChromaDB Wrapper

**Purpose:** Encapsulates all ChromaDB interactions. The rest of the system never calls ChromaDB directly.

### Class: `VectorStore`

```
VectorStore
├── __init__()
├── _get_or_create(name) → Collection
│
├── BM25 Index (in-memory, RFC corpus only)
│   ├── _tokenize(text) → List[str]
│   ├── _build_bm25_index()
│   ├── _bm25_search_rfc(query, top_k, rfc_filter) → List[Dict]
│   └── _rrf_merge(semantic, bm25, top_k, k) → List[Dict]
│
├── RFC Collection API
│   ├── rfc_count() → int
│   ├── add_rfc_chunks(chunks)
│   ├── search_rfc(query, top_k, rfc_filter) → List[Dict]
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

### Key Implementation Details

**BM25 + RRF Fusion:**
- `_build_bm25_index()` — pulls all RFC chunks from ChromaDB, tokenises with `re.findall(r'[a-zA-Z0-9]+', text.lower())`, builds `BM25Okapi`. Rebuilt lazily after ingest.
- `_rrf_merge()` — uses rank positions only (not raw scores). `RRF_score = Σ 1/(60 + rank)`. `k=60` from Cormack et al., 2009.

**Trace collection is ephemeral:** Deleted and recreated on every `__init__()` to prevent stale trace data from persisting across sessions.

**Upsert semantics:** All `add_*` methods use `upsert` (not `add`) so re-indexing is idempotent.

---

## 4. ingest/rfc_fetcher.py — RFC Downloader

### `fetch_rfc(rfc_no, force_refresh=False) → str`

```
1. Check rfc_cache/rfc{N}.txt — return from disk if exists and not force_refresh
2. requests.get("https://www.rfc-editor.org/rfc/rfc{N}.txt", timeout=30)
   Retry up to 3 times with exponential backoff (1s, 2s)
3. Write to cache; return text
```

### `fetch_all_rfcs(force_refresh=False) → Dict[int, str]`

Iterates `RFC_NUMBERS`. On single-RFC failure: logs error and continues. Returns only successfully fetched RFCs.

---

## 5. ingest/rfc_chunker.py — RFC Text Processor

### Pipeline

```
_clean_rfc_text(text)
  └── remove page-break artifacts, normalise line endings, collapse blank lines

_split_into_sections(text)
  └── regex match numbered section headings → list of {section_no, section_title, content}

_chunk_text(section.content)
  └── paragraph-boundary-aware overlapping chunker
      break at \n\n in latter half of window; 300-char overlap

chunk_rfc(rfc_no, text) → List[Dict]
  └── composes all three; IDs: "rfc3261_s8_1_2_c0" (stable, upsert-safe)
```

---

## 6. ingest/doc_ingest.py — User Document Ingestion

### Parsers

| Format | Library | Notes |
|---|---|---|
| PDF | `pypdf.PdfReader` | Page-by-page; filters empty pages |
| DOCX | `python-docx` | Paragraph extraction; drops blanks |
| HTML | `BeautifulSoup4` | Strips `<script>`, `<style>`, `<nav>` etc. |
| TXT/MD | raw UTF-8 | `errors="ignore"` |
| URL | `requests.get` | Content-type detection → pdf or html parser |

Chunk IDs include a 6-char UUID suffix for global uniqueness across re-uploads.

---

## 7. ingest/parsers/text_parser.py — SIP Text Trace Parser

### Key Functions

**`build_message(raw: str) → Dict`** — core parsing function:
- Splits on first blank line → header_block + body
- Matches first line against `_REQUEST_LINE` or `_RESPONSE_LINE`
- Extracts compact header forms (RFC 3261 §7.3.3): `i`→`call-id`, `f`→`from`, `t`→`to`, `v`→`via`, `m`→`contact`

**`parse_text_trace(content: str) → List[Dict]`** — uses zero-width lookahead `_MSG_BOUNDARY` to split without consuming text.

---

## 8. ingest/parsers/html_parser.py — SIP HTML Trace Parser

Four-strategy cascade for block extraction: `<pre>` tags → `<td>` cells → `<div>/<p>/<span>` → full page text. Delegates to `parse_text_trace` after extraction.

---

## 9. ingest/parsers/pcap_parser.py — PCAP Trace Parser

Uses Scapy `rdpcap`. For each packet:
- SIP messages: detect via port 5060/5061 or `_looks_like_sip()` heuristic; delegate to `build_message()`; overlay IP addresses
- RTP streams: `_try_parse_rtp()` checks RTP header (version=2, PT 0–127 excluding 200–204 RTCP); grouped by SSRC into one summary dict per stream

---

## 10. agent/tools.py — Tool Definitions & Implementations

### `TOOL_DEFINITIONS`

List of 6 dicts in OpenAI/Groq function-calling format.

| Tool | Required | Optional |
|---|---|---|
| `search_rfc` | `query: str` | `rfc_filter: int[]` |
| `search_trace` | `query: str` | — |
| `reconstruct_call_flow` | — | `call_id_filter: str` |
| `diagnose_sip_error` | `response_code: int` | `context: str` |
| `cross_reference` | `observation: str` | `topic: str` |
| `search_docs` | `query: str` | `doc_filter: str[]` |

### `execute_tool(tool_name, tool_args, vector_store) → Any`

Central dispatch; wraps calls in `try/except` and returns `{"error": str(exc)}` on failure so the LLM receives structured error information.

**`_diagnose_sip_error`** adds RFC 5630/5922 to `rfc_filter` for auth codes (401, 407, 403, 421, 494).

---

## 11. agent/orchestrator.py — LangGraph Agent Engine

### State Schema

```python
class AgentState(TypedDict):
    messages:          Annotated[List[BaseMessage], add_messages]
    groq_rate_limited: bool   # kept for backwards compatibility
    backend_used:      str    # "groq" | "ollama" — drives the timing badge in app.py
```

`add_messages` deduplicates messages by ID instead of blindly appending.

### LLM Instances

```
Primary: Groq
  groq_with_tools_required  ← turn 0 (tool_choice="required")
  groq_with_tools           ← turn 1+
  groq_plain                ← final turn / is_final=True

Fallback: Ollama Cloud (gpt-oss:120b)
  ollama_cloud_with_tools   ← any non-final turn
  ollama_cloud_plain        ← final turn
  client_kwargs = {"headers": {"Authorization": f"Bearer {OLLAMA_CLOUD_API_KEY}"}}
```

### `_invoke_with_retry(llm, messages, label) → AIMessage`

Retries on 429/rate-limit with delays from `_RETRY_DELAYS = [5, 10, 20, 30, 60]` seconds. Non-429 exceptions propagate immediately.

### `agent_node` Logic

```
agent_turns = count(AIMessages in state)
is_final    = agent_turns >= MAX_ITERATIONS (14)

1. Try Groq (_invoke_with_retry)
   → success: backend_used = "groq"

2. On Groq failure → Try Ollama Cloud (_invoke_with_retry)
   → success: backend_used = "ollama"

3. On all failure → return AIMessage("Agent error: ...")

Turn-0 enforcement: if response has no tool_calls,
  synthesize search_rfc AIMessage to guarantee RFC grounding
```

### `_sanitize_answer(text)`

Regex `_TOOL_LEAK` drops any bullet item or prose sentence containing a tool name. Collapses excessive blank lines.

### `run()` Return Dict

```python
{
  "answer":            str,
  "reasoning_trace":   List[{tool, args, result_preview, result_content}],
  "call_flow":         Optional[Dict],
  "groq_rate_limited": bool,
  "backend_used":      str,   # "groq" | "ollama"
}
```

---

## 12. agent/prompts.py — System Prompt

`SYSTEM_PROMPT` (~215 lines) defines:

| Section | Purpose |
|---|---|
| Identity | Declares expertise in 25 RFCs |
| Tool Catalogue | When/how to use each tool |
| Behavior Guidelines | Mandatory tool-call-first; rfc_filter requirements |
| Trace Diagnosis Playbook | Per-error-code step-by-step procedures (488, 401/407, 503, 486/480) |
| Response Formatting | `###` headings, `**bold**`, inline citations `*(RFC XXXX, §Y.Y)*` |
| Output Hygiene | Never mention tool names; no "Diagnostic Approach" section |

At runtime, `orchestrator.run()` appends dynamic `## Trace Status` and `## User Documents` sections.

---

## 13. app.py — Streamlit UI Layer

### Backend Badge (Timing Pill)

Each assistant message renders a timing pill showing which LLM actually answered:

```python
backend_used = msg.get("backend_used", "groq")
if backend_used == "groq":
    icon, label = "⚡", f"Groq · {GROQ_MODEL.split('/')[-1]}"
else:
    icon, label = "☁️", f"Ollama Cloud · {OLLAMA_CLOUD_MODEL}"
```

This is derived from `backend_used` in the agent result dict, which is set on every agent node invocation.

### Cached Resources

| Function | Cache type | Notes |
|---|---|---|
| `_get_vector_store()` | `@st.cache_resource` | Single VectorStore; cleared by `clear()` on re-index |
| `_get_trulens_components()` | `@st.cache_resource` | Returns `(sip_app, tru_recorder, session)` |

### Session State Keys

| Key | Type | Description |
|---|---|---|
| `messages` | `List[Dict]` | Chat history with `backend_used`, `reasoning_trace`, `call_flow` |
| `pending_query` | `Optional[str]` | Query waiting to be processed |
| `trace_loaded` | `bool` | Whether a trace is currently loaded |
| `trace_filename` | `str` | Name of loaded trace file |
| `trace_msg_count` | `int` | Number of messages in loaded trace |

---

## 14. observability/trulens_setup.py — RAG Observability

### Module-level Constants

| Constant | Value | Purpose |
|---|---|---|
| `EVAL_MODEL_GROQ` | `"llama-3.1-8b-instant"` | Primary eval scoring model |
| `CONTEXT_TOOLS` | `{"search_rfc", ...}` | Tool names whose results count as context |
| `_DB_PATH` | `<project_root>/trulens_eval.db` | SQLite database |

### Class: `GroqOllamaProvider(LLMProvider)`

Extends `LLMProvider` directly — no dependency on `trulens-providers-openai`.

#### `_create_chat_completion()`

Uses **tool-calling** to force integer output:

```
score_tool = {
  "type": "function",
  "function": {
    "name": "submit_score",
    "parameters": {"properties": {"score": {"type": "integer"}}, "required": ["score"]}
  }
}

1. Try Groq (OpenAI client, Groq base URL)
   → tool_choice forces submit_score invocation
   → parse args["score"] → return str(score)

2. On Groq failure → Try Ollama Cloud
   → same tool-calling approach
   → bearer auth header: Authorization: Bearer {OLLAMA_CLOUD_API_KEY}

3. On both failure → return "_DEFAULT_" → _parse_score returns 0.5
```

#### `_parse_score(response, min_score_val, max_score_val) → float`

```python
if response == "_DEFAULT_": return 0.5
score = re_configured_rating(response, min_score_val, max_score_val)
return (score - min_score_val) / (max_score_val - min_score_val)
```

#### `groundedness_measure_with_cot_reasons(source, statement, ...) → (float, dict)`

**Single-call implementation** (not per-sentence):

```
1. Normalise source: join list of context chunks → single string (truncated to 3000 chars)
2. Truncate statement to 1000 chars
3. Single generate_score() call with whole-answer scoring prompt
4. Return (score, {"reason": "Groundedness score: {score:.2f}"})
```

This replaces the original per-sentence loop that made ~10 API calls per query.

### Class: `SIPAssistantApp`

Pure recording shell with two `@instrument`-decorated methods:

```python
@instrument
def _extract_contexts(self, contexts: list) -> list:
    return contexts  # TruLens captures rets[:] as context selector

@instrument
def query(self, question: str, answer: str, contexts: list) -> str:
    self._extract_contexts(contexts)
    return answer   # TruLens records input=question, output=answer
```

### `setup_trulens()` — Feedback Definitions

```python
f_answer_relevance = Feedback(provider.relevance)
    .on(Select.RecordCalls.query.args.question)
    .on(Select.RecordCalls.query.rets)

f_context_relevance = Feedback(provider.context_relevance)
    .on(Select.RecordCalls.query.args.question)
    .on(Select.RecordCalls._extract_contexts.rets[:])   # each chunk
    .aggregate(np.mean)

f_groundedness = Feedback(provider.groundedness_measure_with_cot_reasons)
    .on(Select.RecordCalls._extract_contexts.rets[:].collect())  # all chunks
    .on(Select.RecordCalls.query.rets)
```

**Total eval API calls per query: ~8** (1 answer relevance + 6 context relevance + 1 groundedness)

---

## 15. Data Schemas Reference

### ChromaDB Collections

| Collection | Persistence | Key metadata fields |
|---|---|---|
| `sip_rfcs` | Permanent | `rfc_no`, `rfc_title`, `section_no`, `section_title`, `chunk_idx` |
| `sip_trace` | Ephemeral | `msg_type`, `method`, `response_code`, `call_id`, `cseq`, `src_ip`, `dst_ip` |
| `user_docs` | Permanent | `doc_id`, `doc_name`, `doc_type`, `chunk_idx` |

### Agent `run()` Return Dict

```python
{
  "answer":            str,      # sanitised markdown response
  "reasoning_trace":   List[{
    "tool":            str,
    "args":            dict,
    "result_preview":  str,      # first 500 chars of JSON result
    "result_content":  str,      # full JSON result (for TruLens context extraction)
  }],
  "call_flow":  Optional[{
    "total_messages": int,
    "dialogs": List[{"call_id": str, "message_count": int, "flow": List[str]}]
  }],
  "groq_rate_limited": bool,
  "backend_used":      str,      # "groq" | "ollama"
}
```

### TruLens DB Schema

```
trulens_records    — one row per agent query (record_id, app_id, ts, record_json)
trulens_feedbacks  — one row per metric per query (name, status, result 0.0–1.0, error)
trulens_feedback_defs — serialised Feedback objects
```

---

## 16. Inter-Module Call Flow Diagrams

### A. Query Execution (Agent ReAct Loop)

```
app.py → orchestrator.run(query, trace_active, docs_info)
  │
  ├── Build SystemMessage (SYSTEM_PROMPT + trace_status + doc_status)
  ├── Build HumanMessage(query)
  │
  └── graph.invoke({messages: [...], groq_rate_limited: False, backend_used: "groq"})
        │
        ├──[START]──► agent_node
        │               ├── _invoke_with_retry(groq_with_tools_required, msgs, "Groq")
        │               │     └── AIMessage with tool_calls=[{name, args, id}]
        │               │         backend_used = "groq"
        │               │
        │               └── on failure → _invoke_with_retry(ollama_cloud_with_tools, ...)
        │                                 backend_used = "ollama"
        │
        ├──[tools_condition]──► tool_node
        │                         ├── search_rfc → vs.search_rfc()
        │                         │     ├── ChromaDB cosine/HNSW query
        │                         │     ├── BM25Okapi.get_scores()
        │                         │     └── _rrf_merge() → top_k results
        │                         └── ToolMessage(content=json_str)
        │
        ├──[tools → agent]──► agent_node (next iteration)
        │
        └── ... until no tool_calls or MAX_ITERATIONS
              └── _sanitize_answer(raw_answer)
              └── return {answer, reasoning_trace, backend_used, ...}
```

### B. TruLens Eval Flow

```
app.py (after run())
  │
  ├── Extract contexts: reasoning_trace tool JSON → plain text strings
  │
  └── with tru_recorder:
        sip_app.query(question, answer, contexts)
          └── @instrument records → trulens_eval.db (DEFERRED)

Background evaluator thread:
  │
  └── GroqOllamaProvider._create_chat_completion()
        ├── Groq (llama-3.1-8b-instant, submit_score tool)
        │     → HTTP POST https://api.groq.com/openai/v1/chat/completions
        │     → extract tool_call args["score"]
        │
        └── On failure → Ollama Cloud (gpt-oss:120b, submit_score tool)
              → HTTP POST https://ollama.com/v1/chat/completions
              → Authorization: Bearer {OLLAMA_CLOUD_API_KEY}

  Scores written to trulens_feedbacks → sidebar leaderboard refreshes
```

---

*End of Low Level Design Document*
