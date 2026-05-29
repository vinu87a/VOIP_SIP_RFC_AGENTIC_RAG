# High Level Design (HLD)
# SIP / RTP Agentic RAG Protocol Assistant

**Version:** 1.1  
**Date:** 2026-05-29  
**Stack:** Python 3.11 · LangGraph · Groq (Llama 4 Scout) · Ollama (Gemma 4) · ChromaDB · Sentence-Transformers · Streamlit · TruLens 1.5.3

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Goals and Scope](#2-system-goals-and-scope)
3. [High-Level Architecture](#3-high-level-architecture)
4. [Component Overview](#4-component-overview)
5. [Knowledge Base Architecture](#5-knowledge-base-architecture)
6. [Agent Pipeline Architecture](#6-agent-pipeline-architecture)
7. [Data Flow Diagrams](#7-data-flow-diagrams)
8. [LLM Strategy and Fallback Design](#8-llm-strategy-and-fallback-design)
9. [Persistence Model](#9-persistence-model)
10. [Technology Stack](#10-technology-stack)
11. [Key Design Decisions](#11-key-design-decisions)
12. [Limitations and Known Constraints](#12-limitations-and-known-constraints)
13. [Observability Architecture](#13-observability-architecture)

---

## 1. Executive Summary

The **SIP / RTP Agentic RAG Protocol Assistant** is a locally-runnable, domain-specific AI chatbot that provides deep technical analysis of Session Initiation Protocol (SIP) signaling, RTP media streams, SRTP security, and SDP negotiation. It grounds every answer in authoritative RFC specifications and — when a capture file is uploaded — in the user's own SIP trace.

The system is built on the **Retrieval-Augmented Generation (RAG)** pattern extended with an **agentic loop**: the language model is not just given retrieved chunks, it is equipped with tools it can call autonomously to search the RFC knowledge base, inspect uploaded trace files, reconstruct call flows, diagnose error codes, and cross-reference observations against specifications. The agent can chain multiple tool calls before writing its final answer.

---

## 2. System Goals and Scope

### Primary Goals

| Goal | Description |
|------|-------------|
| **RFC-grounded answers** | Every factual claim backed by retrieved RFC text — no hallucination of standards |
| **Trace-level analysis** | Accept .pcap, .txt, .html trace files and answer questions about actual captured traffic |
| **Agentic reasoning** | LLM decides which tools to call and in what order, not a fixed retrieval pipeline |
| **Local-first** | Embeddings, ChromaDB, and Ollama fallback run entirely on local machine |
| **Document library** | Users can ingest their own PDFs, DOCX, URLs, or text files alongside the RFC knowledge base |

### Out of Scope

- Real-time packet capture (no live interface sniffing)
- SIP proxy or B2BUA functionality
- Outbound SIP call generation
- Multi-user / multi-tenant deployment (single-user local tool)

---

## 3. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         STREAMLIT FRONTEND (app.py)                         │
│                                                                             │
│  ┌───────────────────────────────┐   ┌───────────────────────────────────┐  │
│  │        SIDEBAR                │   │         MAIN CHAT AREA            │  │
│  │  • Brand / navigation         │   │  • Chat history (user + agent)    │  │
│  │  • SIP Trace uploader         │   │  • Welcome hero screen            │  │
│  │  • RFC Knowledge Base list    │   │  • Agent reasoning expander       │  │
│  │  • Document Library           │   │  • Call-flow diagram expander     │  │
│  │  • Re-index / Clear buttons   │   │  • Timing + backend badge         │  │
│  └───────────────────────────────┘   └───────────────────────────────────┘  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │  query + context flags
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       AGENT ORCHESTRATOR (agent/orchestrator.py)            │
│                                                                             │
│   AgentOrchestrator.run(query, trace_active, docs_info)                    │
│                                                                             │
│   ┌──────────────────────────────────────────────────────────────────────┐  │
│   │                   LangGraph StateGraph                               │  │
│   │                                                                      │  │
│   │   START ──► [agent node] ──► (has tool calls?) ──► [tool node]      │  │
│   │                  ▲                  │ no                             │  │
│   │                  └──────────────────┘ ──► END                       │  │
│   │                                                                      │  │
│   │   agent node: ChatGroq / ChatOllama (bind_tools)                    │  │
│   │   tool node:  LangGraph ToolNode (StructuredTool wrappers)          │  │
│   └──────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │  tool calls
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          TOOL LAYER (agent/tools.py)                        │
│                                                                             │
│   search_rfc          search_trace       reconstruct_call_flow             │
│   diagnose_sip_error  cross_reference    search_docs                       │
└────────────┬──────────────────────────────────────┬────────────────────────┘
             │                                      │
             ▼                                      ▼
┌────────────────────────┐            ┌─────────────────────────────────────┐
│   VECTOR STORE         │            │   INGEST PIPELINE                   │
│   (store/vector_store) │            │                                     │
│                        │            │  RFC Fetcher ──► RFC Chunker        │
│  ChromaDB              │            │  IANA Ingest ──► SIP Glossary       │
│  PersistentClient      │            │  Doc Ingest (PDF/DOCX/HTML/URL)     │
│                        │            │  Trace Parsers (TXT/HTML/PCAP)      │
│  Collections:          │            └─────────────────────────────────────┘
│  • sip_rfcs (persist)  │
│  • sip_trace (ephemeral│
│  • user_docs (persist) │
└────────────────────────┘
             │
             ▼
┌────────────────────────┐
│  ChromaDB on Disk      │
│  chroma_db/            │
│  (cosine similarity,   │
│  all-MiniLM-L6-v2)     │
└────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                    OBSERVABILITY LAYER (observability/)                      │
│                                                                             │
│   Post-hoc TruLens recording — non-blocking, runs after each response      │
│                                                                             │
│   app.py ──► SIPAssistantApp.query(question, answer, contexts)             │
│                    │                                                        │
│             TruCustomApp (DEFERRED mode) ──► trulens_eval.db               │
│                    │                                                        │
│             Background evaluator (GroqOllamaProvider)                      │
│             • Answer Relevance  • Context Relevance  • Groundedness        │
│                    │                                                        │
│             Sidebar scores  +  TruLens Dashboard :8502                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Component Overview

### 4.1 Streamlit Frontend (`app.py`)

The single-page application entry point. Manages:
- **Session state** — chat history, trace status, doc processing flags
- **Sidebar rendering** — trace upload, RFC list, document library
- **Chat loop** — reads user input, calls the agent, renders responses
- **CSS injection** — full custom light-theme design system

### 4.2 Agent Orchestrator (`agent/orchestrator.py`)

The brain of the system. Builds and runs a **LangGraph StateGraph** with two nodes:

- **`agent` node** — invokes the LLM (Groq primary, Ollama fallback). The LLM reads the conversation history and either produces a final text answer or emits tool calls.
- **`tools` node** — executes all pending tool calls, appends `ToolMessage` results back to state.

The graph loops until the model produces no tool calls, then extracts the final answer.

### 4.3 Tool Layer (`agent/tools.py`)

Six tools the LLM can invoke. Each tool is defined twice:
1. As a **JSON schema** (`TOOL_DEFINITIONS`) for the Groq API format
2. As a **`StructuredTool`** in `orchestrator.py` for LangGraph's `ToolNode`

| Tool | Purpose |
|------|---------|
| `search_rfc` | BM25 + semantic hybrid search over RFC knowledge base, fused with RRF |
| `search_trace` | Semantic search over uploaded SIP trace |
| `reconstruct_call_flow` | Rebuild ordered dialog from trace by Call-ID / CSeq |
| `diagnose_sip_error` | Look up RFC definition of a SIP response code |
| `cross_reference` | Given a trace observation, find governing RFC rule |
| `search_docs` | Semantic search over user-uploaded documents |

### 4.4 Prompt System (`agent/prompts.py`)

A single large `SYSTEM_PROMPT` string injected as the first `SystemMessage`. It defines:
- The agent's persona and knowledge scope (25 RFCs listed)
- Tool descriptions and when to use each
- A **Trace Diagnosis Playbook** with step-by-step procedures for 488, 401/407, 503, 486/480
- **Response Formatting rules** (headings, bold, underline, citations, follow-up questions)
- **Output hygiene rules** (banned phrases, tool name suppression)

At runtime, `orchestrator.py` appends a dynamic `## Trace Status` and `## User Documents` section to the system prompt based on current session state.

### 4.5 Vector Store (`store/vector_store.py`)

Thin wrapper around three **ChromaDB** collections using cosine-similarity with `all-MiniLM-L6-v2` embeddings. RFC search additionally maintains an in-memory **BM25Okapi** index (built from the same corpus) and fuses both retrieval signals using **Reciprocal Rank Fusion (RRF)**:

| Collection | Persistence | Purpose |
|-----------|-------------|---------|
| `sip_rfcs` | Permanent | RFC text, IANA registry, Wikipedia SIP codes, SIP/VoIP glossary |
| `sip_trace` | Ephemeral (cleared on init) | Currently-uploaded SIP trace messages |
| `user_docs` | Permanent | User-uploaded documents (PDF, DOCX, HTML, TXT, URL) |

### 4.6 Ingest Pipeline (`ingest/`)

Responsible for populating the vector store. Five sub-modules:

| Module | Role |
|--------|------|
| `rfc_fetcher.py` | Downloads 25 RFCs from rfc-editor.org with disk caching |
| `rfc_chunker.py` | Parses RFC text into sections, chunks with overlap |
| `iana_sip_ingest.py` | Fetches IANA SIP Parameters registry |
| `sip_glossary_ingest.py` | Embeds a static 167-term SIP/VoIP glossary CSV |
| `doc_ingest.py` | Universal parser for PDF, DOCX, HTML, TXT, and URLs |

### 4.7 Trace Parsers (`ingest/parsers/`)

| Module | Input | Output |
|--------|-------|--------|
| `text_parser.py` | `.txt`, `.log`, `.sip` | List of parsed SIP message dicts |
| `html_parser.py` | `.html`, `.htm` | List of parsed SIP message dicts |
| `pcap_parser.py` | `.pcap`, `.pcapng` | SIP message dicts + RTP stream summaries |

### 4.9 Observability Layer (`observability/trulens_setup.py`)

Provides RAG quality evaluation using [TruLens](https://www.trulens.org/) 1.5.3. Runs entirely post-hoc — it never blocks the agent response.

**Key design choices:**
- `SIPAssistantApp` is a **pure recording shell** — it does not run the agent. `app.py` runs `AgentOrchestrator` as before, then hands the pre-computed `(question, answer, contexts)` to TruLens for recording.
- `GroqOllamaProvider` extends TruLens's `LLMProvider` directly (bypassing `trulens-providers-openai`) and overrides `generate_score`, `generate_score_and_reasons`, and `groundedness_measure_with_cot_reasons` to call `_create_chat_completion` directly, avoiding the `endpoint` requirement.
- `FeedbackMode.DEFERRED` — feedback rows are written to SQLite as pending, then processed by a `start_evaluator()` background thread. This eliminates any threading conflict with Streamlit.
- RFC context chunks are extracted from the agent's `reasoning_trace` JSON and cleaned to plain text before being passed to TruLens.

### 4.8 Configuration (`config.py`)

Central configuration file. Key settings:

| Setting | Value | Purpose |
|---------|-------|---------|
| `GROQ_MODEL` | `meta-llama/llama-4-scout-17b-16e-instruct` | Primary LLM |
| `OLLAMA_MODEL` | `gemma4:e4b` | Fallback LLM |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Local embedding model |
| `CHUNK_SIZE` | 2000 chars | RFC chunk size |
| `CHUNK_OVERLAP` | 300 chars | RFC chunk overlap |
| `TOP_K` | 6 | Default search results per query |
| `CHROMA_PERSIST_DIR` | `./chroma_db/` | ChromaDB on-disk storage |
| `RFC_CACHE_DIR` | `./rfc_cache/` | Downloaded RFC text cache |

---

## 5. Knowledge Base Architecture

```
                    KNOWLEDGE BASE BUILD (first run / re-index)
                    ──────────────────────────────────────────
                              
  rfc-editor.org                   rfc_cache/           ChromaDB
  ──────────────                   ──────────           ────────
  RFC 3261 .txt ──► fetch_rfc() ──► rfc3261.txt ──►  chunk_rfc()
  RFC 3550 .txt ──► fetch_rfc() ──► rfc3550.txt ──►  chunk_rfc()
  ... (25 RFCs) ──► fetch_rfc() ──► rfc????.txt ──►  chunk_rfc()
                                                           │
  iana.org ────────────────────────► fetch_iana_sip_chunks()
  wikipedia.org ───────────────────► fetch_wikipedia_sip_chunks()
  static CSV ──────────────────────► fetch_sip_glossary_chunks()
                                           │
                                           ▼
                                    all_chunks []
                                           │
                              add_rfc_chunks(all_chunks)
                                           │
                                    ┌──────▼──────┐
                                    │  sip_rfcs   │  ← ~1,800 chunks
                                    │  collection │    cosine similarity
                                    └─────────────┘    MiniLM-L6-v2
                              
  Chunk schema (sip_rfcs):
  ┌──────────────────────────────────────────────────────┐
  │ id            rfc3261_s3_1_c0 (RFC·section·chunk)    │
  │ text          raw section content (~2000 chars)       │
  │ metadata:                                             │
  │   rfc_no       3261                                   │
  │   rfc_title    "SIP: Session Initiation Protocol"     │
  │   section_no   "3.1"                                  │
  │   section_title "Overview of SIP Functionality"       │
  │   chunk_idx    0                                      │
  └──────────────────────────────────────────────────────┘
```

---

## 6. Agent Pipeline Architecture

```
  User Query: "What is pcfg as per RFC 5939?"
       │
       ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  LANGGRAPH STATEGRAPH                                          │
  │                                                                │
  │  State: {messages: [...], groq_rate_limited: bool}            │
  │                                                                │
  │  Turn 1 — agent node                                          │
  │  ┌─────────────────────────────────────────────────────────┐  │
  │  │  System: SYSTEM_PROMPT + trace_status + doc_status      │  │
  │  │  Human:  "What is pcfg as per RFC 5939?"                │  │
  │  │                                                         │  │
  │  │  LLM response: tool_call {                              │  │
  │  │    name: "search_rfc"                                   │  │
  │  │    args: {query: "pcfg potential configuration",        │  │
  │  │           rfc_filter: [5939]}                           │  │
  │  │  }                                                      │  │
  │  └─────────────────────────────────────────────────────────┘  │
  │                   │ tool_calls present → route to tools        │
  │                   ▼                                            │
  │  Turn 1 — tools node                                          │
  │  ┌─────────────────────────────────────────────────────────┐  │
  │  │  execute search_rfc(query="pcfg potential configuration",│  │
  │  │                     rfc_filter=[5939])                  │  │
  │  │                                                         │  │
  │  │  → Pass 1: Dense semantic search (cosine/HNSW)          │  │
  │  │  → Pass 2: BM25 sparse search (TF-IDF weighted)         │  │
  │  │  → RRF fusion: score = Σ 1/(60 + rank)                  │  │
  │  │                                                         │  │
  │  │  ToolMessage: {results: [{source: "RFC 5939 §3.5.1",    │  │
  │  │    content: "a=pcfg (potential configuration)..."}]}    │  │
  │  └─────────────────────────────────────────────────────────┘  │
  │                   │ loop back to agent                         │
  │                   ▼                                            │
  │  Turn 2 — agent node                                          │
  │  ┌─────────────────────────────────────────────────────────┐  │
  │  │  [previous messages + ToolMessage]                      │  │
  │  │                                                         │  │
  │  │  LLM response: text answer (no tool_calls)              │  │
  │  │  "### What is pcfg?\n\n**pcfg** (potential             │  │
  │  │   configuration) is defined in RFC 5939 §4..."          │  │
  │  └─────────────────────────────────────────────────────────┘  │
  │                   │ no tool_calls → route to END              │
  │                   ▼                                            │
  │               END                                             │
  └────────────────────────────────────────────────────────────────┘
       │
       ▼
  _sanitize_answer(raw)  ← strip any leaked tool names
       │
       ▼
  Return {answer, reasoning_trace, call_flow, groq_rate_limited, ollama_model}
```

---

## 7. Data Flow Diagrams

### 7.1 User Question (No Trace)

```
  User ──► [chat_input] ──► session_state.pending_query
                                      │
                        AgentOrchestrator.run(query)
                                      │
                               LangGraph loop
                                      │
                           ┌──────────┴──────────┐
                           │                     │
                    search_rfc()           diagnose_sip_error()
                           │                     │
                    VectorStore               VectorStore
                    .search_rfc()            .search_rfc()
                           │                     │
                    ChromaDB query         ChromaDB query
                    sip_rfcs collection    sip_rfcs collection
                           └──────────┬──────────┘
                                      │
                              LLM synthesizes answer
                                      │
                           app.py renders response
                           with citation pills + timing
```

### 7.2 Trace Upload Flow

```
  User uploads file ──► on_change callback ──► trace_needs_processing = True
                                │
                     _parse_upload(file)
                     ┌──────────┴──────────┐
                     │          │          │
                  .txt       .html      .pcap
                     │          │          │
               text_parser  html_parser  pcap_parser
                     │          │          │
                     └──────────┴──────────┘
                                │
                     List[Dict] messages
                                │
                     vs.clear_trace()
                     vs.add_trace_messages(msgs)
                           [sip_trace collection]
                                │
                     pending_auto_analysis = True
                                │
                     Agent runs 9-section
                     AUTO_ANALYSIS_PROMPT
```

### 7.3 Document Ingestion Flow

```
  File upload / URL input
         │
         ├── ingest_file(bytes, filename)    ← file upload path
         │        │
         │   extension check
         │   ├── .pdf  ──► _parse_pdf()   via pypdf
         │   ├── .docx ──► _parse_docx()  via python-docx
         │   ├── .html ──► _parse_html()  via BeautifulSoup
         │   └── .txt  ──► _parse_txt()   raw UTF-8
         │
         └── ingest_url(url)                 ← URL fetch path
                  │
              requests.get(url)
                  │
              content-type check
              ├── pdf  ──► _parse_pdf()
              └── html ──► _parse_html()
         
         │ both paths produce:
         ▼
  List[{id, text, doc_id, doc_name, doc_type, chunk_idx}]
         │
  vs.add_doc_chunks(chunks)
         │
  ChromaDB upsert ──► user_docs collection (PERMANENT)
         │
  st.rerun() ──► sidebar doc list refreshes
```

---

## 8. LLM Strategy and Fallback Design

```
  User Query (Turn 0)
      │
      ▼
  ┌────────────────────────────────────────────────┐
  │  groq_with_tools_required.invoke()             │  ← Turn 0: tool call MANDATORY
  │  (tool_choice="required")                      │
  └────────────────────────────────────────────────┘
      │                         │
   Success (tool call made)  Exception
      │                         │
      ▼                   Is 429?  ──YES──► ollama_with_tools.invoke()
  tools node executes             │
      │                        NO │
      ▼                           ▼
  Turn 1+ agent node       groq_with_tools.invoke()
  ┌───────────────────┐    (retry with tools, not required)
  │ groq_with_tools   │
  └───────────────────┘
      │                    │
   Success              Exception
      │              Is 429?  ──YES──► ollama_with_tools.invoke()
      ▼                    │
   Final turn         groq_plain.invoke() (non-429 error fallback)
  (is_final=True)
  ┌──────────────┐
  │ groq_plain   │  ← no tools; forces immediate answer
  └──────────────┘
```

**Why five LLM variants?**

| Variant | When used |
|---|---|
| `groq_with_tools_required` | Turn 0 — forces at least one RFC lookup before answering |
| `groq_with_tools` | Turn 1+ — agent calls tools or answers freely |
| `groq_plain` | Final turn (MAX_ITERATIONS) or non-429 error retry |
| `ollama_with_tools` | Any non-final turn when Groq returns 429 |
| `ollama_plain` | Final turn when Groq returns 429 |

**TruLens feedback LLM routing** (independent of the agent):
- Primary: Groq `llama-3.1-8b-instant` (lighter model for eval)
- Fallback: Ollama `gemma4:e4b` on HTTP 429

---

## 9. Persistence Model

```
  PROJECT ROOT/
  │
  ├── chroma_db/                  ← ChromaDB on-disk storage
  │   ├── sip_rfcs/               │  Permanent: ~1,800 RFC + IANA + Wiki + Glossary chunks
  │   └── user_docs/              │  Permanent: user-uploaded doc chunks
  │   (sip_trace rebuilt fresh    │  Ephemeral: cleared on VectorStore.__init__()
  │    every restart)             │
  │
  ├── rfc_cache/                  ← Downloaded RFC plain-text files
  │   ├── rfc3261.txt             │  Avoids re-downloading on re-index
  │   ├── rfc3550.txt             │
  │   └── ...                     │
  │
  ├── trulens_eval.db             ← TruLens evaluation SQLite database
  │   ├── trulens_records         │  one row per agent query
  │   ├── trulens_feedbacks       │  RAG Triad scores (Answer/Context/Groundedness)
  │   └── trulens_feedback_defs   │  feedback function definitions
  │
  └── .env                        ← GROQ_API_KEY, OLLAMA_BASE_URL, OLLAMA_MODEL

  Streamlit session_state (in-memory, lost on browser close):
  ├── messages          chat history
  ├── trace_loaded      bool
  ├── trace_filename    str
  ├── trace_msg_count   int
  ├── pending_query     str
  └── trulens_dashboard_launched  bool
```

---

## 10. Technology Stack

| Layer | Technology | Version | Role |
|-------|-----------|---------|------|
| Frontend | Streamlit | ≥1.36 | Web UI, session state, sidebar |
| Agent Framework | LangGraph | ≥0.2 | StateGraph, ToolNode, tools_condition |
| LLM Binding | LangChain Core | ≥0.3 | BaseMessage, StructuredTool, ChatModel interface |
| Primary LLM | Groq Cloud API | ≥0.9 | Fast inference — Llama 4 Scout 17B |
| Fallback LLM | Ollama (local) | — | Gemma 4 E4B, HTTP 429 fallback |
| Vector DB | ChromaDB | ≥0.5 | Persistent cosine-similarity store |
| Embeddings | Sentence-Transformers | ≥2.7 | all-MiniLM-L6-v2, local CPU |
| Sparse Retrieval | rank-bm25 | ≥0.2.2 | BM25Okapi index over RFC corpus |
| PDF Parsing | pypdf | ≥3.0 | Page-by-page text extraction |
| DOCX Parsing | python-docx | ≥1.0 | Paragraph extraction |
| HTML Parsing | BeautifulSoup4 | ≥4.12 | Tag stripping, text extraction |
| PCAP Parsing | Scapy | ≥2.5 | Packet decoding, RTP analysis |
| HTTP Client | requests | ≥2.31 | RFC download, URL fetch |
| RAG Evaluation | TruLens | 1.5.3 | RAG Triad (Answer/Context/Groundedness) |
| Eval LLM Client | openai (SDK) | ≥1.0 | Groq + Ollama API calls for feedback scoring |

---

## 11. Key Design Decisions

### 11.1 Hybrid Search (BM25 + Semantic + RRF)
Short technical abbreviations like `pcfg`, `acfg`, `srtp`, `ssrc` produce low-quality embeddings because `all-MiniLM-L6-v2` rarely saw them in training. Relying on semantic search alone causes poor recall for acronym-heavy queries.

The system runs **two independent retrieval passes on every query** and fuses them with **Reciprocal Rank Fusion (Cormack et al., 2009)**:

1. **Dense pass** — ChromaDB HNSW cosine-similarity search using `all-MiniLM-L6-v2` embeddings. Strong on natural-language questions and paraphrased queries.
2. **Sparse pass** — `BM25Okapi` (rank-bm25) over the full RFC corpus. Strong on exact term matches, acronyms, and header names like `Via`, `CSeq`, `RSeq`.

Each pass independently retrieves `top_k × 2` candidates. RRF merges them by rank position:

```
RRF_score(chunk) = Σ  1 / (k + rank(chunk))     k = 60
                   over each list containing chunk
```

Neither the raw cosine-similarity score nor the BM25 score is used directly — only rank position matters, making the fusion robust to the very different score scales of the two retrievers. When `rfc_filter` is specified, BM25 scores for non-matching RFCs are zeroed before ranking.

The BM25 index is built in-memory at startup (or lazily after ingest) by pulling all 1,806 RFC chunks from ChromaDB and tokenising with `re.findall(r'[a-zA-Z0-9]+', text.lower())`.

### 11.2 Deterministic Tool-Name Sanitiser
The Llama 4 Scout model occasionally mentions internal tool names (`search_rfc`, `cross_reference`, etc.) in its final answer text. Prompt instructions alone are insufficient. A deterministic regex post-processor (`_sanitize_answer`) is applied after every LLM response — it drops any bullet or prose sentence that contains a tool name, independent of what the model says.

### 11.3 Ephemeral Trace, Permanent RFC + Docs
The `sip_trace` collection is deleted and recreated on every `VectorStore.__init__()` because:
- Traces are session-specific; carrying forward a previous trace would pollute analysis
- ChromaDB's `PersistentClient` stores everything on disk, so without explicit clearing the old trace would persist across restarts

User docs and RFCs, by contrast, are explicitly managed — the user adds and removes them intentionally.

### 11.4 `@st.cache_resource` for VectorStore
A single `VectorStore` object is shared across all Streamlit reruns via `@st.cache_resource`. This avoids re-loading the sentence-transformer model and re-opening ChromaDB on every user interaction. The "Re-index RFCs" button calls `vs.clear_rfcs()` to delete the ChromaDB collection before clearing the Streamlit cache and forcing a fresh object creation on the next rerun.

### 11.5 MAX_ITERATIONS Guard
The LangGraph loop is capped at `MAX_ITERATIONS = 14` agent turns. When this limit is reached, a final `HumanMessage` is injected telling the model to produce its answer immediately without further tool calls. This prevents runaway agent loops that could exhaust the Groq rate limit.

---

## 12. Limitations and Known Constraints

| Limitation | Impact | Mitigation |
|-----------|--------|-----------|
| Groq rate limits (HTTP 429) | Queries fail during burst usage | Ollama local fallback |
| JS-rendered URLs | BeautifulSoup cannot extract text from SPAs | Warning shown to user |
| No real-time capture | Cannot analyse live traffic | Upload-only model |
| Scapy requires root on some OS | PCAP parsing may fail | Error message shown |
| MiniLM-L6-v2 limited vocabulary | Poor embeddings for novel acronyms | BM25 sparse retrieval + RRF fusion |
| Single-user architecture | No auth / user isolation | Designed for local use |
| Llama 4 Scout tool-name leakage | Mentions internal tool names in answers | `_sanitize_answer` post-processor |
| TruLens feedback latency | Scores appear 10–30s after the response | DEFERRED async evaluator decouples scoring from response |
| TruLens 1.5.3 OTEL incompatibility | v2.x OTEL mode breaks Lens selectors | Pinned to 1.5.3; `TRULENS_OTEL_TRACING=false` |

---

## 13. Observability Architecture

### 13.1 RAG Triad

| Metric | Input A | Input B | Interpretation |
|---|---|---|---|
| Answer Relevance | User question | Agent answer | Is the answer on-topic? |
| Context Relevance | User question | Each RFC chunk retrieved | Did the retrieval find the right RFC sections? |
| Groundedness | All RFC chunks (joined) | Agent answer | Is the answer backed by retrieved RFC text vs. hallucinated? |

All three scores are on a **0.0–1.0 scale** (higher = better).

### 13.2 Recording Architecture

```
app.py (after AgentOrchestrator.run())
        │
        ▼
  Extract contexts from reasoning_trace:
    JSON tool results → parse "results[].content" text
        │
        ▼
  with TruCustomApp (DEFERRED mode):
    SIPAssistantApp.query(question, answer, contexts)
      └── _extract_contexts(contexts)  ← @instrument, TruLens records rets
        │
  TruCustomApp.__exit__() → writes pending rows to trulens_eval.db
        │
  Background evaluator thread (started on TruSession init)
        │
  GroqOllamaProvider._create_chat_completion()
        │
  trulens_eval.db updated with scores
        │
  Streamlit sidebar reads get_leaderboard() → displays avg scores
```

### 13.3 Provider Implementation

`GroqOllamaProvider` extends `LLMProvider` directly (no dependency on `trulens-providers-openai`) and overrides three methods:

| Overridden method | Why |
|---|---|
| `generate_score()` | Parent asserts `endpoint is not None` — we bypass by calling `_create_chat_completion` directly |
| `generate_score_and_reasons()` | Same — plus extracts score with `re_configured_rating` |
| `groundedness_measure_with_cot_reasons()` | Uses `sent_tokenize` + per-sentence scoring with a single-score prompt to avoid `re_configured_rating` ambiguity from multi-score CoT blocks |

`_create_chat_completion()` tries Groq first; catches HTTP 429 and retries with Ollama.
