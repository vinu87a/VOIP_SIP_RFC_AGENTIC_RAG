import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ── Tool schema definitions (Groq / OpenAI tool-use format) ──────────────────

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_rfc",
            "description": (
                "Semantically search the RFC knowledge base covering 23 RFCs: "
                "Core SIP — 3261, 3263, 3264; "
                "Media/Transport — 3550, 4566, 5939; "
                "Call Control — 3262, 3311, 3515, 6665, 3891, 4028; "
                "Security/Identity — 4474, 7340, 8760; "
                "Media Security — 3711, 5630, 5922, 5923, 4572, 6904; "
                "Examples — 3665, 3428. "
                "Use for any question about protocol rules, header definitions, procedures, "
                "error codes, or normative requirements. "
                "Optionally restrict the search to specific RFC numbers with rfc_filter."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Precise natural-language query describing what to look up, "
                            "e.g. 'Via header routing rules', "
                            "'SRTP master key derivation procedure', "
                            "'SDP offer answer re-INVITE'."
                        ),
                    },
                    "rfc_filter": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": (
                            "Optional list of RFC numbers to restrict the search, "
                            "e.g. [3261, 3665]. Omit to search all RFCs."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_trace",
            "description": (
                "Semantically search the user-uploaded trace. "
                "The trace may contain SIP messages (INVITE, BYE, etc.) AND RTP stream summaries "
                "(codec, SSRC, packet count, duration, direction). "
                "Use queries like 'RTP streams', 'G.711 codec', 'SSRC', 'media packets' to find RTP entries. "
                "Only available when a trace file has been uploaded."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "What to look for in the trace, "
                            "e.g. 'INVITE messages', '401 Unauthorized responses', "
                            "'BYE from alice', 'missing Contact header'."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reconstruct_call_flow",
            "description": (
                "Reconstruct the ordered SIP call flow from the uploaded trace. "
                "Groups messages by Call-ID and sorts by CSeq number. "
                "Use to understand the full dialog sequence "
                "(INVITE → 100 Trying → 180 Ringing → 200 OK → ACK → BYE → 200 OK)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "call_id_filter": {
                        "type": "string",
                        "description": (
                            "Optional: filter to a specific Call-ID string. "
                            "Omit to reconstruct all dialogs in the trace."
                        ),
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_sip_error",
            "description": (
                "Retrieve the RFC definition, meaning, and common causes of a SIP response code. "
                "Use whenever a 4xx, 5xx, or 6xx code appears in the trace or the user's query."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "response_code": {
                        "type": "integer",
                        "description": "SIP response code to diagnose, e.g. 401, 403, 486, 503.",
                    },
                    "context": {
                        "type": "string",
                        "description": (
                            "Optional additional context, "
                            "e.g. 'received after REGISTER', 'preceded by WWW-Authenticate header'."
                        ),
                    },
                },
                "required": ["response_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cross_reference",
            "description": (
                "Given an observation from the SIP trace, retrieve the governing RFC rule "
                "to determine whether the behavior is RFC-compliant. "
                "Use to explain why something is or is not correct per the specification."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "observation": {
                        "type": "string",
                        "description": (
                            "What was observed in the trace, "
                            "e.g. 'INVITE sent without a Contact header', "
                            "'re-INVITE during early dialog', "
                            "'SRTP packet received with wrong SSRC'."
                        ),
                    },
                    "topic": {
                        "type": "string",
                        "description": (
                            "Optional topic hint to narrow the RFC search, "
                            "e.g. 'authentication', 'SRTP key management', 'connection reuse'."
                        ),
                    },
                },
                "required": ["observation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_docs",
            "description": (
                "Semantically search user-uploaded documents (PDFs, DOCX, HTML pages, "
                "text files, or fetched URLs). Use when the user asks about content in "
                "their uploaded documents or references a document they have provided. "
                "Only available when one or more documents have been uploaded via the sidebar."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Natural-language search query, "
                            "e.g. 'SRTP key exchange procedure', 'codec negotiation rules'."
                        ),
                    },
                    "doc_filter": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional list of doc_id strings to restrict the search "
                            "to specific documents. Omit to search all uploaded documents."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
]


# ── Tool implementations ──────────────────────────────────────────────────────

def execute_tool(tool_name: str, tool_args: Dict[str, Any], vector_store: Any) -> Any:
    """Dispatch a tool call to its implementation and return the result dict."""
    dispatch = {
        "search_rfc": _search_rfc,
        "search_trace": _search_trace,
        "reconstruct_call_flow": _reconstruct_call_flow,
        "diagnose_sip_error": _diagnose_sip_error,
        "cross_reference": _cross_reference,
        "search_docs": _search_docs,
    }
    fn = dispatch.get(tool_name)
    if fn is None:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        return fn(tool_args, vector_store)
    except Exception as exc:
        logger.error(f"Tool '{tool_name}' raised: {exc}", exc_info=True)
        return {"error": str(exc)}


def _search_rfc(args: Dict, vs) -> Dict:
    query = args["query"]
    rfc_filter = args.get("rfc_filter") or None
    top_k = 5

    hits = vs.search_rfc(query, top_k=top_k, rfc_filter=rfc_filter)
    if not hits:
        return {"message": "No relevant RFC content found for this query.", "results": []}

    return {
        "query": query,
        "results": [
            {
                "source": f"RFC {h['rfc_no']} — {h['rfc_title']}",
                "section": f"§{h['section_no']} {h['section_title']}",
                "relevance": h["score"],
                # Cap per-result length to keep context window manageable
                "content": h["text"][:1200],
            }
            for h in hits
        ],
    }


def _search_trace(args: Dict, vs) -> Dict:
    if vs.trace_count() == 0:
        return {
            "message": (
                "No SIP trace is currently loaded. "
                "Ask the user to upload a .txt, .html, or .pcap trace file."
            )
        }
    query = args["query"]
    top_k = 5

    hits = vs.search_trace(query, top_k=top_k)
    if not hits:
        return {"message": "No matching messages found in the trace.", "results": []}

    return {
        "query": query,
        "trace_results": [
            {
                "label": h["method"] or str(h["response_code"]),
                "call_id": h["call_id"],
                "cseq": h["cseq"],
                "src": f"{h['src_ip']}:{h.get('src_port', '')}" if h.get("src_ip") else "",
                "dst": f"{h['dst_ip']}:{h.get('dst_port', '')}" if h.get("dst_ip") else "",
                "relevance": h["score"],
                "message": h["text"][:900],
            }
            for h in hits
        ],
    }


def _reconstruct_call_flow(args: Dict, vs) -> Dict:
    if vs.trace_count() == 0:
        return {"message": "No SIP trace is currently loaded."}

    call_id_filter: str = args.get("call_id_filter", "")
    all_msgs = vs.get_all_trace_messages()

    if call_id_filter:
        all_msgs = [m for m in all_msgs if call_id_filter in m.get("call_id", "")]

    # Group by Call-ID
    dialogs: Dict[str, List] = {}
    for msg in all_msgs:
        cid = msg.get("call_id") or "unknown"
        dialogs.setdefault(cid, []).append(msg)

    def _cseq_num(m: Dict) -> int:
        try:
            return int(str(m.get("cseq", "0")).split()[0])
        except (ValueError, IndexError):
            return 0

    flows = []
    for cid, msgs in dialogs.items():
        sorted_msgs = sorted(msgs, key=_cseq_num)
        steps = []
        for m in sorted_msgs:
            label = m.get("method") or str(m.get("response_code", "?"))
            src = m.get("src_ip", "UA-A")
            dst = m.get("dst_ip", "UA-B")
            cseq = m.get("cseq", "")
            steps.append(f"{src}  →  {dst} : {label}  [CSeq: {cseq}]")
        flows.append(
            {
                "call_id": cid,
                "message_count": len(msgs),
                "flow": steps,
            }
        )

    return {"total_messages": len(all_msgs), "dialogs": flows}


def _diagnose_sip_error(args: Dict, vs) -> Dict:
    code = int(args["response_code"])
    context = args.get("context", "")
    query = f"SIP {code} response code definition meaning behavior"
    if context:
        query += f" {context}"

    # RFC 3261 is primary; add security RFCs for 4xx auth codes
    rfc_filter = [3261]
    if code in (401, 407, 403, 421, 494):
        rfc_filter += [5630, 5922]

    hits = vs.search_rfc(query, top_k=4, rfc_filter=rfc_filter)
    return {
        "response_code": code,
        "rfc_definitions": [
            {
                "source": f"RFC {h['rfc_no']}",
                "section": f"§{h['section_no']} {h['section_title']}",
                "content": h["text"][:900],
            }
            for h in hits
        ],
    }


def _cross_reference(args: Dict, vs) -> Dict:
    observation = args["observation"]
    topic = args.get("topic", "")
    query = f"{topic}: {observation}" if topic else observation

    hits = vs.search_rfc(query, top_k=5)
    return {
        "observation": observation,
        "governing_rules": [
            {
                "source": f"RFC {h['rfc_no']} — {h['rfc_title']}",
                "section": f"§{h['section_no']} {h['section_title']}",
                "relevance": h["score"],
                "rule": h["text"][:900],
            }
            for h in hits
        ],
    }


def _search_docs(args: Dict, vs) -> Dict:
    if vs.doc_count() == 0:
        return {
            "message": (
                "No user documents are currently loaded. "
                "Ask the user to upload a PDF, DOCX, HTML, text file, or URL using the sidebar."
            )
        }
    query = args["query"]
    doc_filter = args.get("doc_filter") or None
    top_k = 5

    hits = vs.search_docs(query, top_k=top_k, doc_filter=doc_filter)
    if not hits:
        return {"message": "No relevant content found in user documents.", "results": []}

    return {
        "query": query,
        "results": [
            {
                "source": f"{h['doc_name']} (chunk {h['chunk_idx']})",
                "doc_id": h["doc_id"],
                "doc_type": h["doc_type"],
                "relevance": h["score"],
                "content": h["text"][:1200],
            }
            for h in hits
        ],
    }
