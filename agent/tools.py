import json
import re
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
                "Must be called whenever a trace is loaded. Builds the full ASCII "
                "ladder diagram (UAC / Proxy / UAS columns, RTP bars, DTMF markers) "
                "and triggers it to appear in a dedicated UI expander. Groups messages "
                "by Call-ID, sorts by CSeq. Use the result to understand the message "
                "sequence; summarise it as a compact arrow chain in your text response "
                "(e.g. INVITE → 100 Trying → 488 → ACK). Do NOT reproduce the ladder "
                "in your text."
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


# ── Ladder diagram helpers ────────────────────────────────────────────────────

_SIP_REASONS: Dict[int, str] = {
    100: "Trying", 180: "Ringing", 181: "Call Forwarded",
    182: "Queued", 183: "Session Progress",
    200: "OK", 202: "Accepted",
    301: "Moved Permanently", 302: "Moved Temporarily",
    400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
    404: "Not Found", 405: "Method Not Allowed",
    407: "Proxy Auth Required", 408: "Request Timeout",
    422: "Session Interval Too Small", 480: "Temporarily Unavailable",
    481: "Call Leg Does Not Exist", 482: "Loop Detected",
    486: "Busy Here", 487: "Request Terminated",
    488: "Not Acceptable Here", 491: "Request Pending",
    500: "Server Internal Error", 503: "Service Unavailable",
    504: "Server Timeout", 600: "Busy Everywhere", 603: "Decline",
}


def _assign_ep_labels(msgs: List[Dict], ips: List[str]) -> Dict[str, str]:
    """Label each unique IP as UAC, UAS, or Proxy based on INVITE direction."""
    invite_msgs = [m for m in msgs if m.get("method") == "INVITE"]
    uac_ip = invite_msgs[0].get("src_ip", "")  if invite_msgs else ""
    uas_ip = invite_msgs[-1].get("dst_ip", "") if invite_msgs else ""
    labels: Dict[str, str] = {}
    proxy_n = 1
    for ip in ips:
        if ip == uac_ip:
            labels[ip] = f"UAC\n{ip}"
        elif ip == uas_ip and ip != uac_ip:
            labels[ip] = f"UAS\n{ip}"
        else:
            tag = "Proxy" if proxy_n == 1 else f"Proxy-{proxy_n}"
            labels[ip] = f"{tag}\n{ip}"
            proxy_n += 1
    return labels


def _make_arrow_line(src_idx: int, dst_idx: int, label: str,
                     n_cols: int, col_w: int) -> str:
    """
    Build one row of the ASCII ladder.
    Idle columns show a centred '|'; the arrow spans from src to dst column.
    """
    total = n_cols * col_w
    buf   = list(" " * total)

    for i in range(n_cols):
        buf[i * col_w + col_w // 2] = "|"

    if src_idx == dst_idx:
        return "".join(buf)

    going_right = src_idx < dst_idx
    lc = min(src_idx, dst_idx) * col_w + col_w // 2
    rc = max(src_idx, dst_idx) * col_w + col_w // 2

    # Fill span with dashes (overwrites intermediate column pipes)
    for pos in range(lc + 1, rc):
        buf[pos] = "-"

    # Arrowhead
    if going_right:
        buf[rc - 1] = ">"
    else:
        buf[lc + 1] = "<"

    # Centre label inside the span with 2-char margins
    avail = rc - lc - 4
    lbl   = label[:avail] if avail > 0 else ""
    if lbl:
        mid   = (lc + rc) // 2
        start = max(lc + 2, mid - len(lbl) // 2)
        start = min(start, rc - 2 - len(lbl))
        for j, ch in enumerate(lbl):
            buf[start + j] = ch

    # Re-draw arrowhead (label may have overwritten it)
    if going_right:
        buf[rc - 1] = ">"
    else:
        buf[lc + 1] = "<"

    return "".join(buf)


def _make_rtp_bar(rtp_text: str, n_cols: int, col_w: int) -> str:
    """
    Bidirectional RTP bar spanning between the leftmost and rightmost column
    pipes — same width as the SIP message arrows.  Uses '~' fill with '<' '>'
    arrowheads to represent duplex media flow.  Label verbosity drops
    automatically so it always fits within the available span.
    """
    # Greedy match handles codec names that contain nested parentheses,
    # e.g. "PCMA — G.711 A-law (8 kHz)"
    ssrc_m  = re.search(r"SSRC=(0x[0-9A-Fa-f]+)",            rtp_text)
    codec_m = re.search(r"Payload Type\s*:\s*\d+\s*\((.+)\)", rtp_text)
    dur_m   = re.search(r"Duration\s*:\s*~?([0-9.]+)",        rtp_text)
    pkts_m  = re.search(r"Packets\s*:\s*(\d+)",               rtp_text)

    raw_codec = codec_m.group(1).strip() if codec_m else "RTP"
    # Use the short form after "—" when present (e.g. "G.711 A-law" not "PCMA — G.711 A-law")
    codec = raw_codec.split("—")[-1].strip() if "—" in raw_codec else raw_codec
    ssrc  = ssrc_m.group(1)       if ssrc_m else "?"
    dur   = f"{dur_m.group(1)}s"  if dur_m  else ""
    pkts  = pkts_m.group(1)       if pkts_m else ""

    total = n_cols * col_w
    buf   = list(" " * total)

    # Vertical pipes at all column centers
    for i in range(n_cols):
        buf[i * col_w + col_w // 2] = "|"

    lc    = col_w // 2                              # leftmost column centre
    rc    = (n_cols - 1) * col_w + col_w // 2       # rightmost column centre
    avail = rc - lc - 4                             # chars between arrowheads

    # Choose most verbose label that fits
    candidates = [
        f"{codec}  SSRC:{ssrc}  ~{dur}  {pkts}pkts",
        f"{codec}  SSRC:{ssrc}  ~{dur}",
        f"{codec}  SSRC:{ssrc}",
        codec,
    ]
    info = next((c for c in candidates if len(c) <= avail), codec[:avail])

    # Fill span with ~ (overwrites intermediate column pipes)
    for pos in range(lc + 1, rc):
        buf[pos] = "~"

    # Bidirectional arrowheads
    buf[lc + 1] = "<"
    buf[rc - 1] = ">"

    # Centre the label inside the span
    if info and avail > 0:
        mid   = (lc + rc) // 2
        start = max(lc + 2, mid - len(info) // 2)
        start = min(start, rc - 2 - len(info))
        for j, ch in enumerate(info):
            buf[start + j] = ch

    # Re-draw arrowheads (label may have overwritten them)
    buf[lc + 1] = "<"
    buf[rc - 1] = ">"

    return "".join(buf)


def _reconstruct_call_flow(args: Dict, vs) -> Dict:
    if vs.trace_count() == 0:
        return {"message": "No SIP trace is currently loaded."}

    call_id_filter: str = args.get("call_id_filter", "")
    all_msgs = vs.get_all_trace_messages()

    if call_id_filter:
        all_msgs = [m for m in all_msgs if call_id_filter in m.get("call_id", "")]

    # Separate RTP stream summaries from SIP signalling messages
    rtp_streams = [m for m in all_msgs if m.get("msg_type") == "rtp_stream"]
    sip_msgs    = [m for m in all_msgs if m.get("msg_type") != "rtp_stream"]

    # Group SIP messages by Call-ID
    dialogs: Dict[str, List] = {}
    for msg in sip_msgs:
        cid = msg.get("call_id") or "unknown"
        dialogs.setdefault(cid, []).append(msg)

    def _cseq_num(m: Dict) -> int:
        try:
            return int(str(m.get("cseq", "0")).split()[0])
        except (ValueError, IndexError):
            return 0

    def _is_dtmf(m: Dict) -> bool:
        text = (m.get("text") or "").lower()
        return (
            m.get("method") == "INFO"
            and ("dtmf" in text or "application/dtmf" in text or "signal=" in text)
        )

    flows = []
    for cid, msgs in dialogs.items():
        sorted_msgs = sorted(msgs, key=_cseq_num)

        # Collect unique IPs in first-appearance order
        seen_ips: List[str] = []
        for m in sorted_msgs:
            for key in ("src_ip", "dst_ip"):
                ip = m.get(key, "")
                if ip and ip not in seen_ips:
                    seen_ips.append(ip)
        if not seen_ips:
            seen_ips = ["UA-A", "UA-B"]

        ep_labels = _assign_ep_labels(sorted_msgs, seen_ips)
        n     = len(seen_ips)
        col_w = max(20, min(36, 80 // n))

        # ── Build ASCII ladder ─────────────────────────────────────────────────
        ladder: List[str] = []

        # Header: endpoint labels (2 lines each: role + IP)
        label_rows = [ep_labels.get(ip, ip).split("\n") for ip in seen_ips]
        max_hdr    = max(len(r) for r in label_rows)
        for row_i in range(max_hdr):
            parts = []
            for col_i in range(n):
                rows = label_rows[col_i]
                text = rows[row_i] if row_i < len(rows) else ""
                parts.append(f"{text:^{col_w}}")
            ladder.append("".join(parts))

        ladder.append("".join("|".center(col_w) for _ in range(n)))

        # Position of the last ACK — RTP bars are inserted after it
        ack_pos = None
        for i, m in enumerate(sorted_msgs):
            if m.get("method") == "ACK":
                ack_pos = i

        rtp_inserted = False

        for i, m in enumerate(sorted_msgs):
            src_ip = m.get("src_ip", "")
            dst_ip = m.get("dst_ip", "")
            method = m.get("method", "")
            code   = int(m.get("response_code") or 0)
            cseq   = m.get("cseq", "")

            if method:
                label = "DTMF INFO" if _is_dtmf(m) else method
            else:
                reason = _SIP_REASONS.get(code, "")
                label  = f"{code} {reason}" if reason else str(code)

            if cseq:
                label = f"{label} [{cseq.split()[0]}]"

            src_idx = seen_ips.index(src_ip) if src_ip in seen_ips else 0
            dst_idx = seen_ips.index(dst_ip) if dst_ip in seen_ips else n - 1

            ladder.append(_make_arrow_line(src_idx, dst_idx, label, n, col_w))

            # Insert RTP bars right after the final ACK (media is now flowing)
            if i == ack_pos and not rtp_inserted and rtp_streams:
                for rtp in rtp_streams:
                    ladder.append(_make_rtp_bar(rtp.get("text", ""), n, col_w))
                rtp_inserted = True

        if not rtp_inserted and rtp_streams:
            for rtp in rtp_streams:
                ladder.append(_make_rtp_bar(rtp.get("text", ""), n, col_w))

        # Pre-join the ladder into a single string so the LLM can paste it
        # directly into a fenced code block without parsing the list.
        ladder_str = "\n".join(ladder)

        flows.append({
            "call_id":       cid,
            "message_count": len(msgs),
            "flow":          ladder,       # kept for Streamlit UI rendering
            "ladder":        ladder_str,   # paste this verbatim into ``` block
        })

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
