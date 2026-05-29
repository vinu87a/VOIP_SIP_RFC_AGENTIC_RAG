# High Level Design (HLD)
# SIP / RTP Agentic RAG Protocol Assistant

**Version:** 1.2  
**Date:** 2026-05-30  
**Stack:** Python 3.11 · LangGraph · Groq (Llama 4 Scout) · Ollama Cloud (gpt-oss:120b) · ChromaDB · Sentence-Transformers · Streamlit · TruLens 1.5.3

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

The **SIP / RTP Agentic RAG Protocol Assistant** is a domain-specific AI chatbot that provides deep technical analysis of Session Initiation Protocol (SIP) signaling, RTP media streams, SRTP security, and SDP negotiation. It grounds every answer in authoritative RFC specifications and — when a capture file is uploaded — in the user's own SIP trace.

The system is built on the **Retrieval-Augmented Generation (RAG)** pattern extended with an **agentic loop**: the language model is equipped with tools it can call autonomously to search the RFC knowledge base, inspect uploaded trace files, reconstruct call flows, diagnose error codes, and cross-reference observations against specifications.

---

## 2. System Goals and Scope

### Primary Goals

| Goal | Description |
|------|-------------|
| **RFC-grounded answers** | Every factual claim backed by retrieved RFC text — no hallucination of standards |
| **Trace-level analysis** | Accept .pcap, .txt, .html trace files and answer questions about actual captured traffic |
| **Agentic reasoning** | LLM decides which tools to call and in what order, not a fixed retrieval pipeline |
| **Resilient LLM routing** | Groq primary with Ollama Cloud fallback and automatic retry backoff |
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
│  │  • Observability scores       │   │  • Welcome hero screen            │  │
│  │  • SIP Trace uploader         │   │  • Agent reasoning expander       │  │
│  │  • Document Library           │   │  • Call-flow diagram expander     │  │
│  │  • RFC Knowledge Base list    │   │  • Backend badge (⚡/☁️ + timing) │  │
│  │  • Re-index / Clear buttons   │   │                                   │  │
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
│   │   agent node: ChatGroq (primary) / ChatOllama Cloud (fallback)      │  │
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

Single-page application entry point. Manages session state, sidebar rendering, the chat loop, and CSS injection. Each response shows a **backend badge** (e.g., `⚡ Groq · llama-4-scout-17b-16e-instruct` or `☁️ Ollama Cloud · gpt-oss:120b`) and elapsed time, derived from the `backend_used` field in the agent state.

### 4.2 Agent Orchestrator (`agent/orchestrator.py`)

Builds and runs a **LangGraph StateGraph** with two nodes:

- **`agent` node** — invokes the LLM. Primary: Groq. Fallback: Ollama Cloud with exponential backoff (5→10→20→30→60s) on rate-limit or error.
- **`tools` node** — executes all pending tool calls, appends `ToolMessage` results.

State schema includes `backend_used: str` (`"groq"` | `"ollama"`) so `app.py` can display the correct badge.

### 4.3 Tool Layer (`agent/tools.py`)

Six tools the LLM can invoke:

| Tool | Purpose |
|------|---------|
| `search_rfc` | BM25 + semantic hybrid search over RFC knowledge base, fused with RRF |
| `search_trace` | Semantic search over uploaded SIP trace |
| `reconstruct_call_flow` | Rebuild ordered dialog from trace by Call-ID / CSeq |
| `diagnose_sip_error` | Look up RFC definition of a SIP response code |
| `cross_reference` | Given a trace observation, find governing RFC rule |
| `search_docs` | Semantic search over user-uploaded documents |

### 4.4 Prompt System (`agent/prompts.py`)

A single large `SYSTEM_PROMPT` string injected as the first `SystemMessage`. Defines the agent's persona, tool usage policy, trace diagnosis playbooks, response formatting rules, and output hygiene constraints (tool name suppression, banned phrases).

### 4.5 Vector Store (`store/vector_store.py`)

Thin wrapper around three **ChromaDB** collections using cosine-similarity with `all-MiniLM-L6-v2` embeddings. RFC search additionally maintains an in-memory **BM25Okapi** index and fuses both retrieval signals using **Reciprocal Rank Fusion (RRF)**.

### 4.6 Ingest Pipeline (`ingest/`)

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

### 4.8 Configuration (`config.py`)

| Setting | Value | Purpose |
|---------|-------|---------|
| `GROQ_MODEL` | `meta-llama/llama-4-scout-17b-16e-instruct` | Primary agent LLM |
| `GROQ_EVAL_MODEL` | `llama-3.1-8b-instant` | Primary eval scoring LLM |
| `OLLAMA_CLOUD_MODEL` | `gpt-oss:120b` | Fallback for agent and eval |
| `OLLAMA_CLOUD_URL` | `https://ollama.com` | Ollama Cloud endpoint |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Local embedding model |
| `CHUNK_SIZE` | 2000 chars | RFC chunk size |
| `TOP_K` | 6 | Default search results per query |

### 4.9 Observability Layer (`observability/trulens_setup.py`)

RAG quality evaluation using TruLens 1.5.3. Runs entirely post-hoc.

**Key design choices:**
- `SIPAssistantApp` is a **pure recording shell** — does not run the agent.
- `GroqOllamaProvider` uses a **tool-calling approach** for eval scoring: defines a `submit_score` tool with `"type": "integer"` in its JSON schema and forces `tool_choice` to guarantee the model returns an integer. This eliminates regex parsing failures on empty or prose responses.
- **Groundedness** is evaluated in a **single API call** (whole answer vs. context), not per-sentence. This reduces eval calls from ~17 to ~8 per query.
- `FeedbackMode.DEFERRED` — feedback rows written to SQLite as pending, processed by a `start_evaluator()` background thread.

---

## 5. Knowledge Base Architecture

```
                    KNOWLEDGE BASE BUILD (first run / re-index)
                    ──────────────────────────────────────────
                              
  rfc-editor.org                   rfc_cache/           ChromaDB
  ──────────────                   ──────────           ────────
  RFC 3261 .txt ──► fetch_rfc() ──► rfc3261.txt ──►  chunk_rfc()
  ... (25 RFCs)                                            │
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
```

---

## 6. Agent Pipeline Architecture

```
  User Query
       │
       ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  LANGGRAPH STATEGRAPH                                          │
  │                                                                │
  │  State: {messages: [...], groq_rate_limited: bool,            │
  │          backend_used: "groq" | "ollama"}                     │
  │                                                                │
  │  Turn 1 (agent node):                                         │
  │    Groq with tool_choice="required"                           │
  │    → tool_call {name: "search_rfc", args: {...}}              │
  │                                                                │
  │  Turn 1 (tools node):                                         │
  │    execute search_rfc → BM25 + semantic + RRF fusion          │
  │    → ToolMessage with RFC chunks                              │
  │                                                                │
  │  Turn 2 (agent node):                                         │
  │    [messages + ToolMessage]                                    │
  │    → text answer (no tool_calls) → END                        │
  └────────────────────────────────────────────────────────────────┘
       │
       ▼
  _sanitize_answer(raw)  ← strip leaked tool names
       │
       ▼
  Return {answer, reasoning_trace, call_flow, backend_used}
```

---

## 7. Data Flow Diagrams

### 7.1 User Question (No Trace)

```
  User ──► chat_input ──► session_state.pending_query
                                    │
                      AgentOrchestrator.run(query)
                                    │
                             LangGraph loop
                                    │
                         ┌──────────┴──────────┐
                         │                     │
                  search_rfc()           diagnose_sip_error()
                         │                     │
                  ChromaDB + BM25        ChromaDB + BM25
                         └──────────┬──────────┘
                                    │
                            LLM synthesizes answer
                                    │
                         app.py renders response
                         with backend badge + timing
```

### 7.2 Trace Upload Flow

```
  User uploads file ──► on_change callback ──► trace_needs_processing = True
                                │
                     _parse_upload(file)
                     ┌──────────┴──────────┐
                     │          │          │
                  .txt       .html      .pcap
                     └──────────┴──────────┘
                                │
                     vs.clear_trace()
                     vs.add_trace_messages(msgs)
                                │
                     pending_auto_analysis = True
```

---

## 8. LLM Strategy and Fallback Design

```
  Agent Query (Turn 0)
      │
      ▼
  ┌─────────────────────────────────────┐
  │  Groq (groq_with_tools_required)    │  ← turn 0: RFC lookup mandatory
  │  tool_choice="required"             │
  └─────────────────────────────────────┘
      │                    │
   Success              Failure / 429
      │                    │
      ▼              wait 5s → retry Groq
  tools node              │
      │               Still failing?
      ▼                    │
  Turn 1+                  ▼
  Groq (tools)    Ollama Cloud (gpt-oss:120b)
                  with retry backoff 5→10→20→30→60s
```

**LLM variants per turn:**

| Variant | When used |
|---|---|
| `groq_with_tools_required` | Turn 0 — forces at least one RFC lookup |
| `groq_with_tools` | Turn 1+ — tools optional |
| `groq_plain` | Final turn (MAX_ITERATIONS=14) |
| `ollama_cloud_with_tools` | Any non-final turn when Groq fails |
| `ollama_cloud_plain` | Final turn when Groq fails |

**TruLens eval LLM routing (independent of agent):**

| Layer | Model | Notes |
|---|---|---|
| Primary | Groq `llama-3.1-8b-instant` | Tool-calling forces integer output via `submit_score` tool |
| Fallback | Ollama Cloud `gpt-oss:120b` | Used when Groq fails |
| Last resort | Default score `0.5` | Only if both fail |

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
  │
  ├── trulens_eval.db             ← TruLens evaluation SQLite database
  │
  └── .env                        ← GROQ_API_KEY, OLLAMA_CLOUD_API_KEY, model overrides
```

---

## 10. Technology Stack

| Layer | Technology | Role |
|-------|-----------|------|
| Frontend | Streamlit ≥1.36 | Web UI, session state, sidebar |
| Agent Framework | LangGraph ≥0.2 | StateGraph, ToolNode, tools_condition |
| LLM Binding | LangChain Core ≥0.3 | BaseMessage, StructuredTool, ChatModel interface |
| Primary LLM | Groq Cloud API | Fast inference — Llama 4 Scout 17B |
| Fallback LLM | Ollama Cloud | gpt-oss:120b hosted inference |
| Vector DB | ChromaDB ≥0.5 | Persistent cosine-similarity store |
| Embeddings | Sentence-Transformers ≥2.7 | all-MiniLM-L6-v2, local CPU |
| Sparse Retrieval | rank-bm25 ≥0.2.2 | BM25Okapi index over RFC corpus |
| PDF Parsing | pypdf ≥3.0 | Page-by-page text extraction |
| PCAP Parsing | Scapy ≥2.5 | Packet decoding, RTP analysis |
| RAG Evaluation | TruLens 1.5.3 | RAG Triad (Answer/Context/Groundedness) |
| Eval LLM Client | openai SDK ≥1.0 | Groq + Ollama Cloud calls for feedback scoring |

---

## 11. Key Design Decisions

### 11.1 Hybrid Search (BM25 + Semantic + RRF)

Short technical abbreviations like `pcfg`, `srtp`, `ssrc` produce low-quality embeddings. The system runs two independent retrieval passes on every query and fuses them with **Reciprocal Rank Fusion (Cormack et al., 2009)**. Neither raw cosine-similarity nor BM25 scores are used directly — only rank position matters, making fusion robust to incompatible score scales.

### 11.2 Tool-Calling for Eval Scoring

Instead of asking the eval LLM to output a number as free text (which fails on empty/prose responses), the provider defines a `submit_score` tool with `"type": "integer"` in its JSON schema and forces `tool_choice` to guarantee the model invokes it. This eliminates all `ParseError` failures from regex-based score extraction.

### 11.3 Single-Call Groundedness

Groundedness is evaluated in one API call (whole answer vs. full context) rather than per-sentence. This reduces eval calls from ~17 to ~8 per query, which fits within free-tier rate limits and reduces latency without meaningfully reducing score accuracy for coherent protocol answers.

### 11.4 Deterministic Tool-Name Sanitiser

The agent occasionally leaks internal tool names (`search_rfc`, etc.) into responses. A deterministic regex post-processor (`_sanitize_answer`) drops any bullet or prose sentence containing a tool name, independent of LLM compliance.

### 11.5 `backend_used` State Field

The LangGraph state includes a `backend_used` string (`"groq"` | `"ollama"`) that is updated on every agent node invocation. `app.py` reads this field from `final_state` and renders the correct backend badge with model name in the timing pill, giving users visibility into which LLM answered.

### 11.6 Ephemeral Trace, Permanent RFC + Docs

`sip_trace` is deleted and recreated on every `VectorStore.__init__()` — traces are session-specific and must not pollute subsequent sessions. User docs and RFCs are explicitly managed by the user.

---

## 12. Limitations and Known Constraints

| Limitation | Impact | Mitigation |
|-----------|--------|-----------|
| Groq rate limits (HTTP 429) | Queries slow on burst usage | Ollama Cloud fallback with retry backoff |
| Ollama Cloud `gpt-oss:120b` latency | Slower than Groq on complex queries | Only activated on Groq failure |
| JS-rendered URLs | BeautifulSoup cannot extract text from SPAs | Warning shown to user |
| No real-time capture | Cannot analyse live traffic | Upload-only model |
| Scapy requires root on some OS | PCAP parsing may fail | Error message shown |
| MiniLM-L6-v2 limited vocabulary | Poor embeddings for novel acronyms | BM25 sparse retrieval + RRF fusion |
| Single-user architecture | No auth / user isolation | Designed for local use |
| TruLens 1.5.3 OTEL incompatibility | v2.x OTEL mode breaks Lens selectors | `TRULENS_OTEL_TRACING=false` env var |

---

## 13. Observability Architecture

### 13.1 RAG Triad

| Metric | Input A | Input B | Interpretation |
|---|---|---|---|
| Answer Relevance | User question | Agent answer | Is the answer on-topic? |
| Context Relevance | User question | Each RFC chunk retrieved | Did retrieval find the right RFC sections? |
| Groundedness | All RFC chunks (joined) | Agent answer | Is the answer backed by retrieved text? |

All scores on **0.0–1.0** scale (higher = better).

### 13.2 Recording Architecture

```
app.py (after AgentOrchestrator.run())
        │
        ▼
  Extract contexts from reasoning_trace (tool JSON → plain text)
        │
        ▼
  with TruCustomApp (DEFERRED mode):
    SIPAssistantApp.query(question, answer, contexts)
      └── _extract_contexts(contexts)  ← @instrument
        │
  Background evaluator thread
        │
  GroqOllamaProvider._create_chat_completion()
    ├── Primary: Groq llama-3.1-8b-instant (tool-calling submit_score)
    └── Fallback: Ollama Cloud gpt-oss:120b
        │
  trulens_eval.db → Streamlit sidebar scores
```

### 13.3 Provider Implementation

`GroqOllamaProvider` overrides three `LLMProvider` methods to bypass the `endpoint` requirement and use `_create_chat_completion` directly:

| Overridden method | Why |
|---|---|
| `generate_score()` | Bypasses `endpoint` assertion; parses integer from `submit_score` tool call |
| `generate_score_and_reasons()` | Same; returns `(normalised_score, {"reason": response})` |
| `groundedness_measure_with_cot_reasons()` | Single-call whole-answer scoring instead of per-sentence |
