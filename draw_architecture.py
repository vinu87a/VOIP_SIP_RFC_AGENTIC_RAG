"""
Generates architecture_diagram.png for the Agentic RAG / SIP assistant.
Run: python3 draw_architecture.py
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch

plt.rcParams["font.family"] = "Arial"

# ── Colour palette ─────────────────────────────────────────────────────────────
C = {
    "user":   "#4A90D9",
    "ui":     "#5B8DD9",
    "ingest": "#E8A838",
    "store":  "#6DBF7E",
    "agent":  "#C46BE3",
    "tools":  "#E05C5C",
    "ext":    "#7ABFCC",
    "bg":     "#1A1A2E",
    "text":   "#E8E8E8",
    "arrow":  "#A0A0C0",
    "gold":   "#FFD700",
    "dim":    "#8888AA",
}

fig, ax = plt.subplots(figsize=(30, 21))
fig.patch.set_facecolor(C["bg"])
ax.set_facecolor(C["bg"])
ax.set_xlim(0, 30)
ax.set_ylim(0, 21)
ax.axis("off")


# ── Helpers ────────────────────────────────────────────────────────────────────
def panel(ax, x, y, w, h, color, title, fs=10.5):
    rect = FancyBboxPatch((x, y), w, h,
                          boxstyle="round,pad=0,rounding_size=0.35",
                          lw=2, edgecolor=color,
                          facecolor=color, alpha=0.07, zorder=1)
    ax.add_patch(rect)
    ax.text(x + 0.22, y + h - 0.28, title, ha="left", va="top",
            fontsize=fs, color=color, weight="bold", zorder=6)


def box(ax, x, y, w, h, color, label="", alpha=0.28, fs=8, radius=0.22):
    rect = FancyBboxPatch((x, y), w, h,
                          boxstyle=f"round,pad=0,rounding_size={radius}",
                          lw=1.4, edgecolor=color,
                          facecolor=color, alpha=alpha, zorder=2)
    ax.add_patch(rect)
    if label:
        ax.text(x + w / 2, y + h / 2, label, ha="center", va="center",
                fontsize=fs, color=C["text"], zorder=5, multialignment="center")


def arr(ax, x1, y1, x2, y2, color=None, label="", lw=1.4, rad=0.08):
    color = color or C["arrow"]
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=lw,
                                mutation_scale=10,
                                connectionstyle=f"arc3,rad={rad}"),
                zorder=4)
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        ax.text(mx, my, label, ha="center", va="center", fontsize=6.8,
                color=color, zorder=7,
                bbox=dict(boxstyle="round,pad=0.15", fc=C["bg"], ec="none", alpha=0.85))


def darr(ax, x1, y1, x2, y2, color=None, label="", lw=1.8, rad=0.0):
    color = color or C["gold"]
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="<|-|>", color=color, lw=lw,
                                mutation_scale=11,
                                connectionstyle=f"arc3,rad={rad}"),
                zorder=4)
    if label:
        mx, my = (x1 + x2) / 2 + 0.15, (y1 + y2) / 2
        ax.text(mx, my, label, ha="left", va="center", fontsize=7,
                color=color, zorder=7,
                bbox=dict(boxstyle="round,pad=0.15", fc=C["bg"], ec="none", alpha=0.85))


# ══════════════════════════════════════════════════════════════════════════════
#  NODES
# ══════════════════════════════════════════════════════════════════════════════

# ── Title ─────────────────────────────────────────────────────────────────────
ax.text(15.0, 20.55, "Agentic RAG  —  SIP / RTP Protocol Assistant  |  Architecture",
        ha="center", va="center", fontsize=15, color="white", weight="bold", zorder=10)

# ── USER ──────────────────────────────────────────────────────────────────────
ax.add_patch(plt.Circle((1.5, 18.6), 0.52, color=C["user"], alpha=0.9, zorder=5))
ax.text(1.5, 18.6, "U", ha="center", va="center",
        fontsize=16, color="white", weight="bold", zorder=6)
ax.text(1.5, 17.85, "User", ha="center", va="center",
        fontsize=9.5, color=C["user"], weight="bold", zorder=6)

# ── STREAMLIT UI ──────────────────────────────────────────────────────────────
panel(ax, 0.2, 11.0, 6.0, 6.6, C["ui"], "STREAMLIT UI  (app.py)")

box(ax, 0.5, 16.8, 5.4, 0.75, C["ui"],
    label="Chat Input  (st.chat_input)", fs=8.5)
box(ax, 0.5, 15.8, 5.4, 0.75, C["ui"],
    label="Chat History  (session_state.messages)", fs=8.5)
box(ax, 0.5, 14.55, 5.4, 1.0, C["ui"],
    label="File Uploader  (.txt  .html  .pcap)\non_change guard  ->  trace_needs_processing", fs=8)
box(ax, 0.5, 13.35, 5.4, 0.95, C["ui"],
    label="Auto-Analysis Trigger\npending_auto_analysis  ->  AUTO_ANALYSIS_PROMPT", fs=8)
box(ax, 0.5, 11.3, 5.4, 0.80, C["ui"],
    label="System Notes  (system_note role)", fs=8.5)

# ── INGESTION ─────────────────────────────────────────────────────────────────
panel(ax, 7.0, 11.0, 5.8, 9.3, C["ingest"], "INGESTION PIPELINE")

box(ax, 7.3, 18.9, 5.2, 0.85, C["ingest"],
    label="rfc_fetcher.py  |  Downloads 11 RFCs\n3-attempt retry  *  disk-cache: rfc_cache/", fs=7.8)
box(ax, 7.3, 17.8, 5.2, 0.85, C["ingest"],
    label="rfc_chunker.py  |  Section-aware splitting\n2000 char chunks  /  300 char overlap", fs=7.8)

# parsers inner border
box(ax, 7.15, 11.2, 5.5, 6.4, C["ingest"], alpha=0.06, radius=0.18)
ax.text(7.45, 17.47, "Trace Parsers", fontsize=8, color=C["ingest"],
        weight="bold", zorder=6)

box(ax, 7.3, 16.3, 5.2, 0.88, C["ingest"],
    label="text_parser.py   ->   .txt  .log  .sip", fs=8.2)
box(ax, 7.3, 15.15, 5.2, 0.9, C["ingest"],
    label="html_parser.py   ->   .html  .htm\nBeautifulSoup4  ->  delegates to text_parser", fs=7.8)
box(ax, 7.3, 13.35, 5.2, 1.55, C["ingest"],
    label="pcap_parser.py   ->   .pcap  .pcapng\n"
          "SIP  :  text layer  ->  message dicts\n"
          "RTP  :  binary header  ->  per-SSRC summaries\n"
          "RTCP excluded  (payload types 200-204)", fs=7.5)
box(ax, 7.3, 11.3, 5.2, 1.8, C["ingest"],
    label="Unified Message Dict\n"
          "{ method, response_code, call_id, cseq,\n"
          "  src_ip, dst_ip, src_port, dst_port,\n"
          "  raw, type }", fs=7.4, alpha=0.16)

# ── VECTOR STORE ──────────────────────────────────────────────────────────────
panel(ax, 13.7, 11.0, 5.8, 9.3, C["store"], "VECTOR STORE  (store/vector_store.py)")

box(ax, 14.0, 18.9, 5.2, 1.05, C["store"],
    label="Embedding Model\nall-MiniLM-L6-v2  (sentence-transformers)\nLocal inference  -  no API key needed", fs=8)

box(ax, 14.0, 16.1, 5.2, 2.55, C["store"],
    label="sip_rfcs  collection\n"
          "-----------------------------------\n"
          "PERSISTENT  (ChromaDB on disk)\n"
          "~2-3 k chunks  *  cosine similarity\n"
          "Metadata: rfc_no, section_no,\n"
          "          section_title\n"
          "Batch upsert: 500 docs / batch", fs=7.6)

box(ax, 14.0, 13.0, 5.2, 2.85, C["store"],
    label="sip_trace  collection\n"
          "-----------------------------------\n"
          "EPHEMERAL  (cleared per upload)\n"
          "SIP messages + RTP stream summaries\n"
          "Metadata: msg_type, method,\n"
          "          call_id, cseq,\n"
          "          src_ip, dst_ip", fs=7.6)

box(ax, 14.0, 11.3, 5.2, 1.5, C["store"],
    label="API surface\n"
          "search_rfc()  *  search_trace()\n"
          "get_all_trace_messages()\n"
          "clear_trace()  *  rfc_count()  *  trace_count()", fs=7.4, alpha=0.16)

# ── AGENT ─────────────────────────────────────────────────────────────────────
panel(ax, 20.3, 11.0, 9.5, 9.3, C["agent"], "AGENT  (agent/)")

box(ax, 20.6, 18.7, 4.3, 1.5, C["agent"],
    label="prompts.py  -  SYSTEM_PROMPT\n"
          "* Role definition + 11 RFC list\n"
          "* Behavior guidelines\n"
          "* Trace Diagnosis Playbook\n"
          "* Output hygiene rules", fs=7.6)

box(ax, 25.15, 18.7, 4.4, 1.5, C["agent"],
    label="Dynamic Trace Status\n(injected per run() call)\n"
          "* message count in trace\n"
          "* mandatory tool workflow\n"
          "* forbidden response patterns", fs=7.6)

box(ax, 20.6, 15.2, 9.0, 3.25, C["agent"],
    label="orchestrator.py  -  AgentOrchestrator\n"
          "----------------------------------------------------------------------\n"
          "Tool-use loop  (MAX_ITERATIONS = 14)\n"
          "  *  Sends messages + TOOL_DEFINITIONS to Groq\n"
          "  *  On tool_calls  ->  execute_tool()  ->  inject result  ->  loop\n"
          "  *  On finish_reason=stop  ->  return final answer\n"
          "  *  Tracks reasoning_trace[]  +  call_flow_result\n\n"
          "Fallback handling\n"
          "  *  tool_use_failed + XML body  ->  XML fallback parser\n"
          "  *  tool_use_failed + text body  ->  re-gen without tool_choice\n"
          "  *  Max iterations hit  ->  forced final answer (no tools)", fs=7.8)

# Tools sub-panel
box(ax, 20.45, 11.15, 9.2, 3.8, C["tools"], alpha=0.06, radius=0.2)
ax.text(20.75, 14.82, "TOOLS  (agent/tools.py)", fontsize=9,
        color=C["tools"], weight="bold", zorder=6)

tw, th = 4.3, 0.88
box(ax, 20.6, 13.72, tw, th, C["tools"],
    label="search_rfc\nSemantic search over RFC KB  *  top_k=5\noptional rfc_filter=[RFC numbers]", fs=7.5)
box(ax, 25.1, 13.72, tw, th, C["tools"],
    label="search_trace\nSemantic search over trace  *  top_k=5\nSIP messages + RTP stream entries", fs=7.5)
box(ax, 20.6, 12.63, tw, th, C["tools"],
    label="reconstruct_call_flow\nGroups msgs by Call-ID\nSorts by CSeq  ->  dialog ladder", fs=7.5)
box(ax, 25.1, 12.63, tw, th, C["tools"],
    label="diagnose_sip_error\nLooks up 4xx/5xx/6xx in RFC 3261\n+ RFC 5630/5922 for auth codes", fs=7.5)
box(ax, 22.85, 11.55, tw, th, C["tools"],
    label="cross_reference\nGiven observation  ->  finds\ngoverning RFC rule", fs=7.5)

# ── EXTERNAL SERVICES ─────────────────────────────────────────────────────────
panel(ax, 0.2, 0.4, 29.6, 10.2, C["ext"], "EXTERNAL SERVICES")

box(ax, 0.5, 0.7, 13.5, 9.5, C["ext"], alpha=0.18,
    label="GROQ API\n"
          "-----------------------------------------------------------------------\n"
          "Model  :  meta-llama/llama-4-scout-17b-16e-instruct\n"
          "Params :  tool_choice=auto  *  temperature=0.1  *  max_tokens=4096\n\n"
          "Request   :  messages[]  +  TOOL_DEFINITIONS (5 JSON schemas)\n\n"
          "Response A :  tool_calls[]  ->  AgentOrchestrator executes\n"
          "             each tool, injects result as role='tool' message,\n"
          "             and loops back to Groq\n\n"
          "Response B :  finish_reason='stop'\n"
          "             ->  final answer returned to Streamlit UI\n\n"
          "Error handling\n"
          "  tool_use_failed + XML body   ->  XML fallback parser in orchestrator\n"
          "  tool_use_failed + text body  ->  re-generate without tool_choice\n"
          "  other BadRequestError        ->  re-raised, shown in UI", fs=9)

box(ax, 14.9, 0.7, 14.7, 9.5, C["ingest"], alpha=0.18,
    label="RFC-EDITOR.ORG  -  RFC Source\n"
          "-----------------------------------------------------------------------\n"
          "RFC 3261  SIP: Session Initiation Protocol\n"
          "RFC 3550  RTP: A Transport Protocol for Real-Time Applications\n"
          "RFC 3711  The Secure Real-Time Transport Protocol (SRTP)\n"
          "RFC 3264  An Offer/Answer Model with SDP\n"
          "RFC 3665  SIP Basic Call Flow Examples\n"
          "RFC 3428  SIP Extension for Instant Messaging\n"
          "RFC 5630  The Use of SDES for TLS SIP Sessions\n"
          "RFC 5922  Domain Certificates in SIP\n"
          "RFC 5923  Connection Reuse in SIP\n"
          "RFC 4572  Connection-Oriented Media Transport over TLS in SDP\n"
          "RFC 6904  Encryption of Header Extensions in SRTP\n\n"
          "Fetched on first run  *  3-attempt retry with backoff\n"
          "Disk-cached to rfc_cache/  *  11 files total", fs=9)


# ══════════════════════════════════════════════════════════════════════════════
#  ARROWS
# ══════════════════════════════════════════════════════════════════════════════

# User -> Chat Input
arr(ax, 1.9, 18.3, 3.0, 17.55, C["ui"], "query")
# Chat History -> User
arr(ax, 2.5, 15.8, 1.9, 17.8, C["ui"], "renders\nanswer")
# User -> File Uploader
arr(ax, 2.0, 18.1, 3.0, 15.05, C["ui"], "upload\ntrace")

# File Uploader -> parsers
arr(ax, 6.2, 16.3, 7.3, 16.74, C["ingest"], ".txt/.log")
arr(ax, 6.2, 15.05, 7.3, 15.60, C["ingest"], ".html")
arr(ax, 6.2, 14.5, 7.3, 14.12, C["ingest"], ".pcap")

# rfc_fetcher -> rfc-editor (goes down to ext panel)
arr(ax, 9.9, 18.9, 15.5, 10.2, C["ingest"], "fetch on\nfirst run", rad=0.15)

# rfc_chunker -> sip_rfcs collection
arr(ax, 12.5, 18.22, 14.0, 17.38, C["store"], "chunk dicts")

# parsers -> sip_trace collection
arr(ax, 12.5, 16.74, 14.0, 16.1, C["store"], "SIP dicts")
arr(ax, 12.5, 15.60, 14.0, 15.4, C["store"], "SIP dicts")
arr(ax, 12.5, 14.12, 14.0, 14.4, C["store"], "SIP+RTP\ndicts")

# Embedding model -> collections (dashed thin)
arr(ax, 16.6, 18.9, 16.6, 18.65, C["store"], "", lw=1.0)

# Auto-analysis trigger -> Orchestrator
arr(ax, 5.9, 13.82, 20.6, 17.2, C["agent"], "AUTO_ANALYSIS_PROMPT\n-> pending_query", rad=-0.15)

# Chat input -> Orchestrator
arr(ax, 5.9, 17.17, 20.6, 18.1, C["agent"], "pending_query", rad=-0.08)

# System prompt + trace status -> Orchestrator
arr(ax, 22.75, 18.7, 22.75, 18.45, C["agent"], "", lw=1.2)
arr(ax, 27.35, 18.7, 27.35, 18.45, C["agent"], "", lw=1.2)

# Orchestrator <-> Groq (double arrow, prominent)
darr(ax, 20.6, 16.2, 14.0, 7.0, C["gold"],
     "messages + tool schemas\n(request)  /  tool_calls or stop  (response)", rad=0.0)

# Orchestrator -> Tools (downward)
arr(ax, 25.05, 15.2, 25.05, 14.6, C["tools"], "execute_tool()", lw=1.6)
arr(ax, 22.0, 15.2, 22.0, 14.6, C["tools"], "", lw=1.2)

# Tools -> sip_rfcs (search_rfc, diagnose, cross_ref)
arr(ax, 20.6, 14.16, 19.5, 17.3, C["store"], "RFC tools\n(search_rfc\ndiagnose\ncross_ref)", rad=0.1)

# Tools -> sip_trace (search_trace, reconstruct)
arr(ax, 20.6, 13.07, 19.5, 14.0, C["store"], "trace tools\n(search_trace\nreconstruct)", rad=0.05)

# Orchestrator -> Chat History (answer)
arr(ax, 20.6, 16.8, 5.9, 16.17, C["ui"],
    "answer + reasoning_trace + call_flow", rad=0.12)

# Auto-analysis: uploaded file -> pending_auto_analysis
arr(ax, 3.2, 13.35, 3.2, 14.22, C["ui"], "triggers", lw=1.0)

# ── Legend ────────────────────────────────────────────────────────────────────
items = [
    (C["ui"],     "Streamlit UI"),
    (C["ingest"], "Ingestion"),
    (C["store"],  "Vector Store"),
    (C["agent"],  "Agent / Orchestrator"),
    (C["tools"],  "Tools"),
    (C["ext"],    "External Services"),
    (C["gold"],   "Groq API (bidirectional)"),
    (C["arrow"],  "Data flow"),
]
lx, ly = 0.4, 0.15
for i, (color, label) in enumerate(items):
    ax.add_patch(mpatches.Rectangle((lx + i * 3.6, ly), 0.35, 0.17,
                                    color=color, alpha=0.85, zorder=8))
    ax.text(lx + i * 3.6 + 0.48, ly + 0.085, label,
            va="center", fontsize=7.5, color=C["dim"], zorder=9)

plt.tight_layout(pad=0.2)
out = "/Users/vinaysaitwal/Desktop/AI Agents/Agentic RAG/architecture_diagram.png"
plt.savefig(out, dpi=160, bbox_inches="tight", facecolor=C["bg"])
print(f"Saved: {out}")
