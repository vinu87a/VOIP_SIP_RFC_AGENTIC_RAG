import os
import logging
import tempfile
import time

import streamlit as st
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")

st.set_page_config(
    page_title="SIP / RTP Protocol Assistant",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── RFC metadata with categories ─────────────────────────────────────────────

RFC_CATEGORIES = {
    "Core SIP": {
        "color": "#4f46e5", "bg": "rgba(99,102,241,0.1)", "text": "#4f46e5",
        "rfcs": {3261: "SIP Core Protocol", 3263: "SIP Server Location (DNS/SRV/NAPTR)", 3264: "SDP Offer/Answer Model"},
    },
    "Media & Transport": {
        "color": "#059669", "bg": "rgba(16,185,129,0.1)", "text": "#059669",
        "rfcs": {3550: "RTP Real-Time Transport", 4566: "SDP: Session Description Protocol", 5939: "SDP Capability Negotiation"},
    },
    "Call Control": {
        "color": "#0891b2", "bg": "rgba(6,182,212,0.1)", "text": "#0891b2",
        "rfcs": {3262: "Reliability of Provisional Responses (PRACK)", 3311: "SIP UPDATE Method",
                 3515: "SIP REFER Method (Call Transfer)", 6665: "SIP-Specific Event Notification",
                 3891: "SIP Replaces Header (Attended Transfer)", 4028: "Session Timers in SIP"},
    },
    "Security & Identity": {
        "color": "#d97706", "bg": "rgba(245,158,11,0.1)", "text": "#d97706",
        "rfcs": {4474: "Authenticated Identity Management in SIP",
                 7340: "STIR/SHAKEN — Secure Telephone Identity", 8760: "SIP Digest Access Authentication",
                 3711: "SRTP Secure RTP", 5630: "TLS Usage in SIP", 5922: "Domain Certificates in SIP",
                 5923: "Connection Reuse in SIP", 4572: "TLS for Media (SDP)", 6904: "SRTP Header Ext. Encryption"},
    },
    "Examples & Messaging": {
        "color": "#7c3aed", "bg": "rgba(139,92,246,0.1)", "text": "#7c3aed",
        "rfcs": {3665: "SIP Basic Call Flow Examples", 3428: "SIP Instant Messaging"},
    },
}

EXAMPLE_QUESTIONS = [
    ("📡", "What is the role of the Via header in SIP routing?"),
    ("🔐", "Explain SRTP key derivation per RFC 3711"),
    ("🤝", "How does the SDP offer/answer model work?"),
    ("📋", "What are the mandatory headers in a SIP INVITE?"),
    ("🔄", "Explain SIP dialog vs SIP transaction"),
    ("🔗", "How does connection reuse work in RFC 5923?"),
]

AUTO_ANALYSIS_PROMPT = """You are an expert SIP/VoIP protocol analyst with deep knowledge of RFC 3261,
RFC 4566 (SDP), RFC 3264 (offer/answer), RFC 3550 (RTP), RFC 3711 (SRTP),
RFC 8760 (SIP digest auth), RFC 4028 (session timers), and RFC 3262 (PRACK),
and real-world carrier and enterprise SBC deployments.

The SIP trace is already loaded. Analyse it using the trace tools and produce
a structured report.

━━━ TOOL WORKFLOW (mandatory — follow this order) ━━━
1. Call reconstruct_call_flow first. The ASCII ladder diagram is rendered
   automatically in the UI expander — you do NOT need to reproduce it in your
   text response. For Section 2 in your text, write only a brief plain-text
   summary of the message sequence (e.g. "INVITE → 100 Trying → 488 → ACK")
   and note any missing or unexpected steps. The actual diagram is in the
   expander.
2. For each section below, use targeted search_trace calls to retrieve the
   specific messages you need (INVITE, error responses, SDP bodies, auth
   headers, RTP stream summaries, etc.).
3. For every [CRITICAL] or [WARNING] finding, call search_rfc with an
   appropriate rfc_filter to retrieve the governing rule. Cite it inline as
   *(RFC XXXX, §Y.Y)* immediately after the finding.
4. Use cross_reference when you need to determine whether a specific observed
   behaviour is RFC-compliant.
Never describe what you plan to search — call the tools and report findings directly.

━━━ ADAPTIVE SECTION RULE ━━━
Only include a section if the trace contains relevant data for it. Do NOT
produce placeholder text such as "no data available" or "not present in trace"
— omit sections that don't apply entirely. Keep the report tight and relevant.

━━━ SECTIONS TO COVER (if present in the trace) ━━━
  1. Trace overview — detected scenario type, Call-ID(s), endpoints, message
     count, timestamp range, call direction
  2. Call flow diagram — write the message sequence as a single line
     (e.g. INVITE → 100 Trying → 488 Not Acceptable Here → ACK) and note
     the scenario. The full ASCII ladder is rendered in the UI expander;
     do NOT attempt to reproduce it in your text response.
  3. Codec & SDP negotiation — offered vs answered codecs, SDP body diffs,
     dynamic payload type mapping, a=rtpmap / a=fmtp verification
  4. RTP / media analysis — media IP/port, SSRC, ptime, directionality,
     sequence-number gaps (packet loss), timestamp irregularities (jitter),
     payload type match vs SDP answer, RTCP if present
  5. Failure & error analysis — exact message where call broke, error code,
     responsible party (caller / callee / proxy / SBC), root cause
  6. Timer & retransmit — 100 Trying latency, T1/T2 behaviour,
     retransmission storms, session timer (Session-Expires / Min-SE),
     re-INVITE / UPDATE refresh presence, OPTIONS keep-alives
  7. DTMF analysis — RFC 4733 telephone-event, payload type 101, SIP INFO
  8. TLS / transport — TLS version, cipher suite, SIPS URI usage,
     certificate issues, TCP RST after ClientHello
  9. Header anomalies — missing, malformed, or non-RFC-compliant headers;
     Max-Forwards value; Contact URI integrity
  10. Routing & topology — Via chain, Record-Route insertion vs Route usage
      in mid-dialog requests, unexpected intermediaries
  11. Authentication flow — 401/407 challenge–response, nonce/cnonce/qop/nc
      field validation, realm mismatches, incomplete handshakes
  12. NAT / ICE / STUN — Contact vs Via address mismatch, rport, ICE
      candidates (host/srflx/relay), SRTP key exchange method
      (SDES a=crypto vs DTLS-SRTP a=fingerprint / a=setup)
  13. Early media / 183 — 180 Ringing vs 183 Session Progress, SDP in 1xx,
      PRACK (100rel) usage

━━━ SCENARIO CONTEXT ━━━
First detect the scenario type from the trace (call failure, successful call,
registration failure, authentication loop, codec mismatch, NAT issue, etc.)
and state it clearly at the start of Section 1. Then prioritise sections:
- Call failure      → lead with §5 Failure & error analysis
- Codec mismatch    → lead with §3 Codec & SDP negotiation
- Auth loop         → lead with §11 Authentication flow
- NAT issue         → lead with §12 NAT / ICE / STUN
- Successful call   → lead with §2 Call flow, then §3 Codec, then §4 RTP

━━━ INLINE COMMENTARY RULES ━━━
- Flag anything that deviates from RFC expectations.
- Note protocol violations, header mismatches, timing anomalies, security concerns.
- Severity labels:
    [CRITICAL]  call-breaking issue
    [WARNING]   degraded experience or non-compliant behaviour
    [INFO]      observation, not harmful
- Every [CRITICAL] and [WARNING] must end with an inline RFC citation:
  *(RFC XXXX, §Y.Y)*
- Quote the exact SIP header or SDP line from the trace for every finding
  (inline code block).

━━━ OUTPUT FORMAT ━━━
Numbered sections with clear ### headings. Sub-bullets for detail. SIP header
and SDP excerpts quoted in code blocks. Use **bold** for method names,
response codes, header names, and exact values extracted from the trace.

━━━ COMMENTS & RECOMMENDATIONS ━━━
End with a "Comments & Recommendations" section as a concise bullet list.
Each bullet: one-line observation followed by one-line fix.
Split into two groups:
  Critical  — call-breaking issues, must fix immediately
  Advisory  — best-practice improvements
"""


# ── CSS injection ─────────────────────────────────────────────────────────────

def _inject_css():
    st.markdown("""
<style>
/* ── Base & background ──────────────────────────────────────────────────────── */
[data-testid="stAppViewContainer"] {
    background: #f7f8fc !important;
    min-height: 100vh;
}
[data-testid="stHeader"] {
    background: rgba(247,248,252,0.95) !important;
    border-bottom: 1px solid rgba(99,102,241,0.1) !important;
    backdrop-filter: blur(8px) !important;
}
.main .block-container {
    padding-top: 1.5rem;
    padding-bottom: 4rem;
}

/* ── Sidebar ────────────────────────────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background: #ffffff !important;
    border-right: 1px solid #e8e8f8 !important;
    box-shadow: 2px 0 12px rgba(99,102,241,0.06) !important;
}
[data-testid="stSidebar"] > div:first-child {
    padding-top: 1rem;
}

/* ── Scrollbar ──────────────────────────────────────────────────────────────── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: #f1f1f1; }
::-webkit-scrollbar-thumb { background: rgba(99,102,241,0.35); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: rgba(99,102,241,0.6); }

/* ── Dividers ───────────────────────────────────────────────────────────────── */
hr { border-color: #e8e8f0 !important; margin: 0.6rem 0 !important; }

/* ── Buttons ────────────────────────────────────────────────────────────────── */
.stButton > button {
    border-radius: 10px !important;
    font-weight: 600 !important;
    font-size: 0.82rem !important;
    transition: all 0.2s ease !important;
    border: 1px solid #e0e0f0 !important;
    background: #ffffff !important;
    color: #4f46e5 !important;
    box-shadow: 0 1px 4px rgba(99,102,241,0.08) !important;
}
.stButton > button:hover {
    background: rgba(99,102,241,0.07) !important;
    border-color: rgba(99,102,241,0.4) !important;
    color: #4338ca !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 14px rgba(99,102,241,0.15) !important;
}
.stButton > button:active { transform: translateY(0) !important; }

/* ── File uploader ──────────────────────────────────────────────────────────── */
[data-testid="stFileUploaderDropzone"] {
    background: rgba(99,102,241,0.03) !important;
    border: 2px dashed rgba(99,102,241,0.3) !important;
    border-radius: 12px !important;
    transition: all 0.2s ease !important;
}
[data-testid="stFileUploaderDropzone"]:hover {
    border-color: rgba(99,102,241,0.6) !important;
    background: rgba(99,102,241,0.06) !important;
}

/* ── Text inputs ────────────────────────────────────────────────────────────── */
.stTextInput > div > div > input {
    background: #ffffff !important;
    border: 1px solid #ddd8f8 !important;
    border-radius: 10px !important;
    color: #1e293b !important;
    font-size: 0.85rem !important;
    box-shadow: 0 1px 3px rgba(99,102,241,0.06) !important;
}
.stTextInput > div > div > input:focus {
    border-color: #6366f1 !important;
    box-shadow: 0 0 0 3px rgba(99,102,241,0.12) !important;
}
.stTextInput > div > div > input::placeholder { color: #a0aec0 !important; }

/* ── Chat input container (sticky bottom bar) ──────────────────────────────── */
[data-testid="stChatInputContainer"] {
    background: rgba(255,255,255,0.96) !important;
    border-top: none !important;
    backdrop-filter: blur(20px) !important;
    -webkit-backdrop-filter: blur(20px) !important;
    padding: 14px 20px 16px !important;
    position: relative !important;
}
[data-testid="stChatInputContainer"]::before {
    content: '' !important;
    display: block !important;
    position: absolute !important;
    top: 0; left: 0; right: 0 !important;
    height: 2px !important;
    background: linear-gradient(90deg, #6366f1 0%, #06b6d4 50%, #8b5cf6 100%) !important;
}

/* ── The input wrapper pill ─────────────────────────────────────────────────── */
[data-testid="stChatInput"] {
    background:
        linear-gradient(#ffffff, #ffffff) padding-box,
        linear-gradient(135deg, #6366f1 0%, #06b6d4 55%, #8b5cf6 100%) border-box !important;
    border: 2px solid transparent !important;
    border-radius: 999px !important;
    box-shadow: 0 4px 22px rgba(99,102,241,0.14), 0 1px 4px rgba(0,0,0,0.06) !important;
    transition: box-shadow 0.25s ease !important;
    overflow: hidden !important;
}
[data-testid="stChatInput"]:focus-within {
    box-shadow:
        0 0 0 4px rgba(99,102,241,0.12),
        0 6px 28px rgba(99,102,241,0.22),
        0 1px 4px rgba(0,0,0,0.06) !important;
}

/* ── Textarea inside the pill ───────────────────────────────────────────────── */
[data-testid="stChatInput"] textarea {
    background: transparent !important;
    border: none !important;
    border-radius: 999px !important;
    color: #1e293b !important;
    font-size: 0.95rem !important;
    font-weight: 450 !important;
    padding: 13px 18px !important;
    box-shadow: none !important;
    caret-color: #6366f1 !important;
}
[data-testid="stChatInput"] textarea::placeholder {
    color: #a0aec0 !important;
    font-style: italic !important;
}
[data-testid="stChatInput"] textarea:focus {
    border: none !important;
    box-shadow: none !important;
    outline: none !important;
}

/* ── Submit / send button ───────────────────────────────────────────────────── */
[data-testid="stChatInputSubmitButton"] > button,
[data-testid="stChatInput"] button {
    background: linear-gradient(135deg, #6366f1, #06b6d4) !important;
    border: none !important;
    border-radius: 999px !important;
    color: #ffffff !important;
    box-shadow: 0 2px 10px rgba(99,102,241,0.4) !important;
    transition: all 0.2s ease !important;
    padding: 8px 14px !important;
    min-width: 42px !important;
    min-height: 36px !important;
    margin-right: 4px !important;
}
[data-testid="stChatInputSubmitButton"] > button:hover,
[data-testid="stChatInput"] button:hover {
    background: linear-gradient(135deg, #4f46e5, #0891b2) !important;
    box-shadow: 0 4px 16px rgba(99,102,241,0.5) !important;
    transform: scale(1.07) !important;
    color: #ffffff !important;
}

/* ── Chat messages ──────────────────────────────────────────────────────────── */
[data-testid="stChatMessage"] {
    border-radius: 14px !important;
    padding: 6px 8px !important;
    margin-bottom: 6px !important;
    border: 1px solid transparent !important;
}
[data-testid="stChatMessage"]:has(img[alt="user"]),
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
    background: rgba(6,182,212,0.05) !important;
    border-color: rgba(6,182,212,0.18) !important;
}
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-assistant"]) {
    background: #ffffff !important;
    border-color: rgba(99,102,241,0.14) !important;
    box-shadow: 0 2px 10px rgba(99,102,241,0.07) !important;
}

/* ── Expanders ──────────────────────────────────────────────────────────────── */
[data-testid="stExpander"] {
    border: 1px solid #e8e8f4 !important;
    border-radius: 12px !important;
    background: #fafafa !important;
    overflow: hidden !important;
    box-shadow: 0 1px 4px rgba(0,0,0,0.04) !important;
}
[data-testid="stExpander"] summary {
    border-radius: 11px !important;
    padding: 10px 14px !important;
    font-size: 0.85rem !important;
    font-weight: 600 !important;
    color: #4f46e5 !important;
}
[data-testid="stExpander"] summary:hover {
    background: rgba(99,102,241,0.05) !important;
}

/* ── Alert / info boxes ─────────────────────────────────────────────────────── */
.stAlert { border-radius: 12px !important; font-size: 0.85rem !important; }
[data-testid="stAlertContentInfo"] {
    background: #eff6ff !important;
    border: 1px solid #bfdbfe !important;
    border-radius: 12px !important;
    color: #1d4ed8 !important;
}
[data-testid="stAlertContentSuccess"] {
    background: #f0fdf4 !important;
    border: 1px solid #bbf7d0 !important;
    border-radius: 12px !important;
    color: #15803d !important;
}
[data-testid="stAlertContentWarning"] {
    background: #fffbeb !important;
    border: 1px solid #fde68a !important;
    border-radius: 12px !important;
    color: #b45309 !important;
}
[data-testid="stAlertContentError"] {
    background: #fef2f2 !important;
    border: 1px solid #fecaca !important;
    border-radius: 12px !important;
    color: #dc2626 !important;
}

/* ── Code blocks ────────────────────────────────────────────────────────────── */
.stCodeBlock {
    border-radius: 10px !important;
    border: 1px solid #e8e8f4 !important;
}

/* ── Markdown text ──────────────────────────────────────────────────────────── */
[data-testid="stMarkdownContainer"] p { color: #334155; line-height: 1.7; }
[data-testid="stMarkdownContainer"] h3 {
    color: #1e293b;
    font-weight: 700;
    border-bottom: 2px solid rgba(99,102,241,0.2);
    padding-bottom: 4px;
    margin-top: 1.2rem;
}
[data-testid="stMarkdownContainer"] strong { color: #0f172a; }
[data-testid="stMarkdownContainer"] li { color: #334155; }
[data-testid="stMarkdownContainer"] code {
    background: rgba(99,102,241,0.09) !important;
    color: #4f46e5 !important;
    border-radius: 5px !important;
    padding: 1px 6px !important;
    font-size: 0.85em !important;
    border: 1px solid rgba(99,102,241,0.15) !important;
}
[data-testid="stMarkdownContainer"] blockquote {
    border-left: 3px solid #6366f1 !important;
    background: rgba(99,102,241,0.05) !important;
    padding: 8px 14px !important;
    border-radius: 0 10px 10px 0 !important;
    color: #4f46e5 !important;
}

/* ── Captions ───────────────────────────────────────────────────────────────── */
.stCaption { color: #94a3b8 !important; font-size: 0.76rem !important; }

/* ── Streamlit default text overrides ───────────────────────────────────────── */
.stMarkdown, p, span, label, div { color: inherit; }

/* ── Custom component classes (injected via st.markdown) ─────────────────────── */
.app-brand {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 4px 12px;
    margin-bottom: 4px;
    border-bottom: 1px solid #f0eefc;
}
.brand-icon { font-size: 1.8rem; line-height: 1; }
.brand-text-main {
    font-size: 1.05rem;
    font-weight: 800;
    background: linear-gradient(135deg, #6366f1, #0891b2);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    letter-spacing: -0.01em;
    display: block;
}
.brand-text-sub {
    font-size: 0.68rem;
    color: #94a3b8;
    font-weight: 500;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    display: block;
    margin-top: -1px;
}
.section-pill {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 5px 11px;
    border-radius: 999px;
    font-size: 0.73rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    margin: 14px 0 8px 0;
    width: fit-content;
}
.sp-trace { background: rgba(16,185,129,0.1);  color: #059669; border: 1px solid rgba(16,185,129,0.3); }
.sp-rfc   { background: rgba(99,102,241,0.1);  color: #4f46e5; border: 1px solid rgba(99,102,241,0.3); }
.sp-docs  { background: rgba(6,182,212,0.1);   color: #0891b2; border: 1px solid rgba(6,182,212,0.3); }
.trace-active-card {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 10px 12px;
    background: #f0fdf4;
    border: 1px solid #bbf7d0;
    border-radius: 12px;
    margin: 6px 0;
}
.pulse-dot {
    width: 9px;
    height: 9px;
    background: #10b981;
    border-radius: 50%;
    margin-top: 3px;
    flex-shrink: 0;
    animation: glow-pulse 2s ease-in-out infinite;
}
@keyframes glow-pulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(16,185,129,0.4); }
    50%       { box-shadow: 0 0 0 5px rgba(16,185,129,0); }
}
.trace-active-name { color: #15803d; font-size: 0.8rem; font-weight: 700; display: block; }
.trace-active-meta { color: #6b7280; font-size: 0.72rem; display: block; margin-top: 1px; }
.rfc-category-label {
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 3px 9px;
    border-radius: 999px;
    display: inline-block;
    margin: 10px 0 5px 0;
}
.rfc-row {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 4px 0;
    border-bottom: 1px solid #f1f0fb;
    font-size: 0.76rem;
    color: #475569;
}
.rfc-row:last-child { border-bottom: none; }
.rfc-num-badge {
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 0.7rem;
    font-weight: 700;
    color: #4f46e5;
    background: rgba(99,102,241,0.1);
    padding: 1px 6px;
    border-radius: 5px;
    min-width: 34px;
    text-align: center;
    flex-shrink: 0;
    border: 1px solid rgba(99,102,241,0.2);
}
.stats-row {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin: 8px 0 6px;
}
.stat-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 0.71rem;
    font-weight: 600;
}
.sc-purple { background: rgba(99,102,241,0.1);  color: #4f46e5; border: 1px solid rgba(99,102,241,0.25); }
.sc-green  { background: rgba(16,185,129,0.1);  color: #059669; border: 1px solid rgba(16,185,129,0.25); }
.sc-cyan   { background: rgba(6,182,212,0.1);   color: #0891b2; border: 1px solid rgba(6,182,212,0.25); }
.doc-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 10px;
    background: #ffffff;
    border: 1px solid #e0f2fe;
    border-radius: 9px;
    margin-bottom: 5px;
    gap: 8px;
    box-shadow: 0 1px 4px rgba(6,182,212,0.08);
}
.doc-card-name { color: #0f172a; font-size: 0.78rem; font-weight: 600; word-break: break-all; }
.doc-card-meta { color: #64748b; font-size: 0.68rem; margin-top: 1px; }
.doc-type-pill {
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 0.64rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    background: rgba(6,182,212,0.12);
    color: #0891b2;
    flex-shrink: 0;
    border: 1px solid rgba(6,182,212,0.25);
}
.hero-wrap {
    text-align: center;
    padding: 3.5rem 2rem 2.5rem;
    background: linear-gradient(135deg, rgba(99,102,241,0.06) 0%, rgba(6,182,212,0.04) 50%, rgba(139,92,246,0.06) 100%);
    border: 1px solid rgba(99,102,241,0.15);
    border-radius: 24px;
    margin-bottom: 2rem;
    position: relative;
    overflow: hidden;
    box-shadow: 0 8px 32px rgba(99,102,241,0.08);
}
.hero-wrap::before {
    content: '';
    position: absolute;
    top: -50%;
    left: 50%;
    transform: translateX(-50%);
    width: 600px;
    height: 400px;
    background: radial-gradient(circle, rgba(99,102,241,0.08) 0%, transparent 70%);
    pointer-events: none;
}
.hero-title {
    font-size: 2.6rem;
    font-weight: 900;
    background: linear-gradient(135deg, #6366f1 0%, #0891b2 45%, #7c3aed 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    line-height: 1.15;
    letter-spacing: -0.03em;
    margin-bottom: 0.6rem;
}
.hero-sub {
    color: #64748b;
    font-size: 1rem;
    line-height: 1.7;
    max-width: 560px;
    margin: 0 auto 1.5rem;
}
.hero-badges {
    display: flex;
    justify-content: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 0.5rem;
}
.hb { padding: 5px 14px; border-radius: 999px; font-size: 0.76rem; font-weight: 600; }
.hb-purple { background: rgba(99,102,241,0.1);  color: #4f46e5; border: 1px solid rgba(99,102,241,0.25); }
.hb-cyan   { background: rgba(6,182,212,0.1);   color: #0891b2; border: 1px solid rgba(6,182,212,0.25); }
.hb-green  { background: rgba(16,185,129,0.1);  color: #059669; border: 1px solid rgba(16,185,129,0.25); }
.hb-amber  { background: rgba(245,158,11,0.1);  color: #d97706; border: 1px solid rgba(245,158,11,0.25); }
.eq-label {
    font-size: 0.78rem;
    font-weight: 700;
    color: #94a3b8;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin: 0.4rem 0 0.8rem;
}
.timing-pill {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 3px 11px;
    border-radius: 999px;
    font-size: 0.72rem;
    font-weight: 600;
    margin-top: 10px;
    background: rgba(99,102,241,0.08);
    border: 1px solid rgba(99,102,241,0.2);
    color: #4f46e5;
}
.timing-pill.ollama {
    background: rgba(245,158,11,0.08);
    border-color: rgba(245,158,11,0.25);
    color: #d97706;
}
.rl-banner {
    display: flex;
    align-items: center;
    gap: 9px;
    padding: 9px 14px;
    background: #fffbeb;
    border: 1px solid #fde68a;
    border-radius: 10px;
    color: #b45309;
    font-size: 0.81rem;
    margin-bottom: 10px;
    font-weight: 500;
}
</style>
""", unsafe_allow_html=True)


# ── Cached / init helpers ─────────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def _get_vector_store():
    from store.vector_store import VectorStore
    return VectorStore()


def _ensure_rfc_index(vs) -> None:
    if vs.rfc_count() > 0:
        return

    from ingest.rfc_fetcher import fetch_all_rfcs
    from ingest.rfc_chunker import chunk_rfc
    from ingest.iana_sip_ingest import fetch_iana_sip_chunks

    with st.status("🔄 Building RFC knowledge base…", expanded=True) as status:

        # ── Step 1: Download RFCs ─────────────────────────────────────────────
        status.write("📥 **Step 1 / 5** — Downloading 23 RFCs from rfc-editor.org…")
        rfcs = fetch_all_rfcs()
        status.write(f"  ✅ Downloaded {len(rfcs)} RFCs")

        # ── Step 2: Chunk RFCs ────────────────────────────────────────────────
        status.write("✂️ **Step 2 / 5** — Chunking RFC text into searchable segments…")
        all_chunks = []
        for rfc_no, text in rfcs.items():
            status.write(f"  📄 Chunking RFC {rfc_no}…")
            all_chunks.extend(chunk_rfc(rfc_no, text))
        status.write(f"  ✅ {len(all_chunks):,} RFC chunks created")

        # ── Step 3: IANA registry ─────────────────────────────────────────────
        status.write("🌐 **Step 3 / 5** — Fetching IANA SIP Parameters registry…")
        try:
            iana_chunks = fetch_iana_sip_chunks()
            all_chunks.extend(iana_chunks)
            status.write(f"  ✅ IANA: {len(iana_chunks)} chunks added")
        except Exception as exc:
            status.write(f"  ⚠️ IANA fetch skipped: {exc}")

        # ── Step 4: Wikipedia + Glossary ──────────────────────────────────────
        status.write("📖 **Step 4 / 5** — Fetching Wikipedia SIP Response Codes…")
        try:
            from ingest.wikipedia_sip_ingest import fetch_wikipedia_sip_chunks
            wiki_chunks = fetch_wikipedia_sip_chunks()
            all_chunks.extend(wiki_chunks)
            status.write(f"  ✅ Wikipedia: {len(wiki_chunks)} chunks added")
        except Exception as exc:
            status.write(f"  ⚠️ Wikipedia fetch skipped: {exc}")

        status.write("📚 Indexing SIP/VoIP Terminology Glossary…")
        try:
            from ingest.sip_glossary_ingest import fetch_sip_glossary_chunks
            glossary_chunks = fetch_sip_glossary_chunks()
            all_chunks.extend(glossary_chunks)
            status.write(f"  ✅ Glossary: {len(glossary_chunks)} chunks added")
        except Exception as exc:
            status.write(f"  ⚠️ Glossary ingest skipped: {exc}")

        # ── Step 5: Embed and store ───────────────────────────────────────────
        status.write(f"🧠 **Step 5 / 5** — Embedding {len(all_chunks):,} chunks with all-MiniLM-L6-v2…")
        status.write("  _(this is the longest step — typically 2-3 minutes)_")
        vs.add_rfc_chunks(all_chunks)

        status.update(
            label=f"✅ Knowledge base ready — {len(all_chunks):,} chunks across 23 RFCs + IANA + Wikipedia + Glossary",
            state="complete",
            expanded=False,
        )


def _parse_upload(uploaded_file) -> list:
    from ingest.parsers.text_parser import parse_text_trace
    from ingest.parsers.html_parser import parse_html_trace
    from ingest.parsers.pcap_parser import parse_pcap_trace

    ext = os.path.splitext(uploaded_file.name)[1].lower()
    if ext in (".txt", ".log", ".sip"):
        return parse_text_trace(uploaded_file.read().decode("utf-8", errors="ignore"))
    if ext in (".html", ".htm"):
        return parse_html_trace(uploaded_file.read().decode("utf-8", errors="ignore"))
    if ext in (".pcap", ".pcapng", ".cap"):
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(uploaded_file.read())
            tmp_path = tmp.name
        try:
            return parse_pcap_trace(tmp_path)
        finally:
            os.unlink(tmp_path)
    st.error(f"Unsupported file type '{ext}'. Please upload .txt, .html, or .pcap")
    return []


# ── Sidebar ───────────────────────────────────────────────────────────────────

def _on_trace_upload_change():
    st.session_state.trace_needs_processing = True


def _on_doc_upload_change():
    st.session_state.doc_needs_processing = True


def _render_sidebar(vs):
    with st.sidebar:
        # Brand header
        st.markdown("""
<div class="app-brand">
  <span class="brand-icon">📡</span>
  <span>
    <span class="brand-text-main">SIP / RTP Assistant</span>
    <span class="brand-text-sub">Powered by Groq &amp; ChromaDB</span>
  </span>
</div>
""", unsafe_allow_html=True)

        # ── Trace upload ──────────────────────────────────────────────────────
        st.markdown('<div class="section-pill sp-trace">📎 SIP Trace</div>', unsafe_allow_html=True)

        uploaded = st.file_uploader(
            "Upload SIP trace",
            type=["txt", "log", "html", "htm", "pcap", "pcapng", "cap"],
            label_visibility="collapsed",
            key="trace_uploader",
            on_change=_on_trace_upload_change,
        )

        if st.session_state.get("trace_needs_processing"):
            st.session_state.trace_needs_processing = False

            if uploaded is None:
                if st.session_state.get("trace_loaded"):
                    vs.clear_trace()
                    st.session_state.trace_loaded = False
                    st.session_state.trace_filename = ""
                    st.session_state.trace_msg_count = 0
                    st.session_state.messages.append({
                        "role": "system_note",
                        "content": "🗑️ Trace file removed.",
                    })
            else:
                with st.spinner(f"Parsing {uploaded.name}…"):
                    try:
                        msgs = _parse_upload(uploaded)
                    except Exception as exc:
                        st.error(f"Failed to parse trace: {exc}")
                        msgs = []

                if msgs:
                    vs.clear_trace()
                    vs.add_trace_messages(msgs)
                    st.session_state.trace_loaded = True
                    st.session_state.trace_filename = uploaded.name
                    st.session_state.trace_msg_count = len(msgs)
                    sip_count = sum(1 for m in msgs if m.get("type") != "rtp_stream")
                    rtp_count = sum(1 for m in msgs if m.get("type") == "rtp_stream")
                    parts = [f"{sip_count} SIP msgs"]
                    if rtp_count:
                        parts.append(f"{rtp_count} RTP stream(s)")
                    st.session_state.messages.append({
                        "role": "system_note",
                        "content": (
                            f"📎 Trace uploaded: **{uploaded.name}** — "
                            f"{', '.join(parts)} indexed. Running 9-section diagnostic…"
                        ),
                    })
                    st.session_state.pending_auto_analysis = True
                else:
                    st.warning("No SIP messages found in the uploaded file.")

        if st.session_state.get("trace_loaded"):
            fname = st.session_state.trace_filename
            count = st.session_state.trace_msg_count
            st.markdown(f"""
<div class="trace-active-card">
  <div class="pulse-dot"></div>
  <div>
    <span class="trace-active-name">✓ {fname}</span>
    <span class="trace-active-meta">{count} messages indexed &nbsp;·&nbsp; trace analysis active</span>
  </div>
</div>
""", unsafe_allow_html=True)
            if st.button("🗑️ Clear trace", use_container_width=True):
                vs.clear_trace()
                st.session_state.trace_loaded = False
                st.session_state.trace_filename = ""
                st.session_state.trace_msg_count = 0
                st.session_state.messages.append({"role": "system_note", "content": "🗑️ Trace cleared."})
                st.rerun()
        else:
            st.caption("No trace loaded · upload a .txt / .pcap / .html file")

        # ── RFC knowledge base ────────────────────────────────────────────────
        st.markdown('<div class="section-pill sp-rfc">📚 RFC Knowledge Base</div>', unsafe_allow_html=True)

        rfc_chunk_count = vs.rfc_count()
        st.markdown(f"""
<div class="stats-row">
  <span class="stat-chip sc-purple">📄 {rfc_chunk_count:,} RFC chunks</span>
  <span class="stat-chip sc-green">🏛️ 23 RFCs</span>
  <span class="stat-chip sc-cyan">🔡 MiniLM-L6</span>
</div>
""", unsafe_allow_html=True)

        for cat_name, cat in RFC_CATEGORIES.items():
            st.markdown(
                f'<div class="rfc-category-label" style="background:{cat["bg"]};color:{cat["text"]}">'
                f'{cat_name}</div>',
                unsafe_allow_html=True,
            )
            rows_html = "".join(
                f'<div class="rfc-row">'
                f'<span class="rfc-num-badge">{rfc_no}</span>'
                f'<span>{title}</span>'
                f'</div>'
                for rfc_no, title in cat["rfcs"].items()
            )
            st.markdown(rows_html, unsafe_allow_html=True)

        if st.button("🔄 Re-index RFCs", use_container_width=True):
            # Wipe the RFC collection so _ensure_rfc_index triggers on rerun.
            # Fallback to direct client call when the cached instance predates the method.
            try:
                vs.clear_rfcs()
            except AttributeError:
                try:
                    vs._client.delete_collection("sip_rfcs")
                except Exception:
                    pass
            _get_vector_store.clear()
            st.rerun()

        # ── Document library ──────────────────────────────────────────────────
        doc_chunk_count = vs.doc_count()
        doc_count_label = f"{doc_chunk_count:,} chunks" if doc_chunk_count else "empty"
        st.markdown(
            f'<div class="section-pill sp-docs">📄 Document Library'
            f'<span style="margin-left:8px;font-weight:500;opacity:0.75;font-size:0.68rem">'
            f'{doc_count_label}</span></div>',
            unsafe_allow_html=True,
        )
        st.caption("PDF · DOCX · HTML · TXT · URL")

        uploaded_doc = st.file_uploader(
            "Upload document",
            type=["pdf", "docx", "html", "htm", "txt", "md", "log", "csv"],
            label_visibility="collapsed",
            key="doc_uploader",
            on_change=_on_doc_upload_change,
        )

        if st.session_state.get("doc_needs_processing"):
            st.session_state.doc_needs_processing = False
            if uploaded_doc is not None:
                with st.spinner(f"Ingesting {uploaded_doc.name}…"):
                    try:
                        from ingest.doc_ingest import ingest_file
                        chunks, doc_id = ingest_file(uploaded_doc.read(), uploaded_doc.name)
                        if not chunks:
                            st.warning(
                                f"No text could be extracted from **{uploaded_doc.name}**. "
                                "The file may be empty or image-only."
                            )
                        else:
                            vs.add_doc_chunks(chunks)
                            st.session_state.messages.append({
                                "role": "system_note",
                                "content": (
                                    f"📄 Document ingested: **{uploaded_doc.name}** — "
                                    f"**{len(chunks)} chunks** added to Document Library."
                                ),
                            })
                            st.rerun()
                    except Exception as exc:
                        st.error(f"Failed to ingest document: {exc}")

        url_col, btn_col = st.columns([3, 1])
        url_input = url_col.text_input(
            "URL", placeholder="https://…", key="doc_url_input", label_visibility="collapsed"
        )
        if btn_col.button("Fetch", key="doc_url_fetch"):
            if url_input.strip():
                with st.spinner(f"Fetching {url_input.strip()}…"):
                    try:
                        from ingest.doc_ingest import ingest_url
                        chunks, doc_id = ingest_url(url_input.strip())
                        if not chunks:
                            st.warning(
                                "The URL was fetched but no text could be extracted. "
                                "The page may be JavaScript-rendered or empty."
                            )
                        else:
                            vs.add_doc_chunks(chunks)
                            st.session_state.messages.append({
                                "role": "system_note",
                                "content": (
                                    f"🌐 URL ingested: **{url_input.strip()}** — "
                                    f"**{len(chunks)} chunks** added to Document Library."
                                ),
                            })
                            st.rerun()
                    except Exception as exc:
                        st.error(f"Failed to fetch URL: {exc}")
            else:
                st.warning("Please enter a URL first.")

        docs = vs.list_docs()
        if docs:
            for doc in docs:
                col1, col2 = st.columns([5, 1])
                col1.markdown(
                    f'<div class="doc-card">'
                    f'<div><div class="doc-card-name">{doc["doc_name"]}</div>'
                    f'<div class="doc-card-meta">{doc["chunk_count"]} chunks</div></div>'
                    f'<span class="doc-type-pill">{doc["doc_type"].upper()}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if col2.button("✕", key=f"rm_doc_{doc['doc_id']}", help="Remove document"):
                    vs.remove_doc(doc["doc_id"])
                    st.session_state.messages.append({
                        "role": "system_note",
                        "content": f"🗑️ Document removed: **{doc['doc_name']}**",
                    })
                    st.rerun()
            if st.button("🗑️ Clear all documents", use_container_width=True, key="clear_all_docs"):
                vs.clear_docs()
                st.session_state.messages.append({
                    "role": "system_note",
                    "content": "🗑️ All user documents cleared.",
                })
                st.rerun()
        else:
            st.caption("No documents uploaded yet.")

        # ── Clear chat ────────────────────────────────────────────────────────
        st.divider()
        if st.button("🧹 Clear chat history", use_container_width=True):
            st.session_state.messages = []
            st.rerun()


# ── Chat rendering helpers ────────────────────────────────────────────────────

def _render_assistant_message(msg: dict):
    if msg.get("groq_rate_limited"):
        st.markdown(
            f'<div class="rl-banner">⚠️ Groq rate limit (429) — response generated by '
            f'Ollama ({msg.get("ollama_model", "gemma4:e4b")})</div>',
            unsafe_allow_html=True,
        )

    st.markdown(msg["content"], unsafe_allow_html=True)

    trace = msg.get("reasoning_trace", [])
    if trace:
        with st.expander(
            f"🔍 Agent reasoning — {len(trace)} tool call{'s' if len(trace) != 1 else ''}",
            expanded=False,
        ):
            for i, step in enumerate(trace, 1):
                st.markdown(f"**Step {i} · `{step['tool']}`**")
                display_args = {k: v for k, v in step["args"].items() if k != "top_k"}
                st.json(display_args, expanded=False)
                st.caption(f"Result preview: {step['result_preview'][:300]}")
                if i < len(trace):
                    st.divider()

    cf = msg.get("call_flow")
    if cf and cf.get("dialogs"):
        with st.expander(
            f"📊 Call flow — {cf['total_messages']} messages",
            expanded=True,
        ):
            for dialog in cf["dialogs"]:
                st.markdown(f"**Call-ID:** `{dialog['call_id']}`  ({dialog['message_count']} msgs)")
                st.code("\n".join(dialog["flow"]), language="")

    elapsed = msg.get("elapsed_seconds")
    if elapsed is not None:
        is_ollama = msg.get("groq_rate_limited")
        backend = f"Ollama · {msg.get('ollama_model', 'gemma4:e4b')}" if is_ollama else "Groq"
        icon = "🦙" if is_ollama else "⚡"
        cls = "timing-pill ollama" if is_ollama else "timing-pill"
        st.markdown(
            f'<div class="{cls}">{icon} {elapsed}s &nbsp;·&nbsp; {backend}</div>',
            unsafe_allow_html=True,
        )


# ── Welcome screen ────────────────────────────────────────────────────────────

def _render_welcome():
    st.markdown("""
<div class="hero-wrap">
  <div class="hero-title">📡 SIP / RTP Protocol Assistant</div>
  <div class="hero-sub">
    Deep-dive analysis of SIP signaling, RTP media, SRTP security, and SDP negotiation —
    grounded in 23 RFC specifications and your uploaded traces.
  </div>
  <div class="hero-badges">
    <span class="hb hb-purple">🏛️ 23 RFCs indexed</span>
    <span class="hb hb-cyan">🔐 SRTP &amp; TLS</span>
    <span class="hb hb-green">📡 Trace analysis</span>
    <span class="hb hb-amber">⚡ Groq LLM</span>
  </div>
</div>
<div class="eq-label">✦ Try asking</div>
""", unsafe_allow_html=True)

    cols = st.columns(2)
    for i, (icon, q) in enumerate(EXAMPLE_QUESTIONS):
        if cols[i % 2].button(f"{icon}  {q}", use_container_width=True, key=f"eg_{i}"):
            st.session_state.messages.append({"role": "user", "content": q})
            st.session_state.pending_query = q
            st.rerun()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    _inject_css()

    if not os.getenv("GROQ_API_KEY"):
        st.error(
            "**GROQ_API_KEY is not set.**  \n"
            "Create a `.env` file (copy `.env.example`) and add your Groq API key, "
            "then restart the app."
        )
        st.stop()

    try:
        vs = _get_vector_store()
        _ensure_rfc_index(vs)
    except Exception as exc:
        st.error(f"Failed to initialise RFC index: {exc}")
        st.stop()

    # ── Session state init ────────────────────────────────────────────────────
    defaults = {
        "messages": [],
        "pending_query": None,
        "pending_auto_analysis": False,
        "trace_loaded": False,
        "trace_filename": "",
        "trace_msg_count": 0,
        "trace_needs_processing": False,
        "doc_needs_processing": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    _render_sidebar(vs)

    # ── Chat input ────────────────────────────────────────────────────────────
    user_input = st.chat_input("Ask about SIP, RTP, SRTP, SDP, or your uploaded trace…")
    if user_input and user_input.strip():
        st.session_state.messages.append({"role": "user", "content": user_input.strip()})
        st.session_state.pending_query = user_input.strip()

    # ── Welcome screen ────────────────────────────────────────────────────────
    if not st.session_state.messages:
        _render_welcome()

    # ── Render full chat history ──────────────────────────────────────────────
    for msg in st.session_state.messages:
        if msg["role"] == "user":
            with st.chat_message("user", avatar="🧑‍💻"):
                st.markdown(msg["content"])
        elif msg["role"] == "assistant":
            with st.chat_message("assistant", avatar="🤖"):
                _render_assistant_message(msg)
        elif msg["role"] == "system_note":
            st.info(msg["content"])

    # ── Promote pending_auto_analysis → pending_query ─────────────────────────
    if st.session_state.get("pending_auto_analysis") and not st.session_state.get("pending_query"):
        st.session_state.pending_auto_analysis = False
        st.session_state.pending_query = AUTO_ANALYSIS_PROMPT
        st.session_state.auto_analysis_pending = True

    # ── Run agent for any pending query ──────────────────────────────────────
    if st.session_state.get("pending_query"):
        query = st.session_state.pending_query
        is_auto = st.session_state.pop("auto_analysis_pending", False)
        st.session_state.pending_query = None

        spinner_label = "Analysing trace…" if is_auto else "Thinking…"

        with st.chat_message("assistant", avatar="🤖"):
            with st.spinner(spinner_label):
                from agent.orchestrator import AgentOrchestrator
                try:
                    _t0 = time.perf_counter()
                    docs_info = vs.list_docs() if vs.doc_count() > 0 else None
                    result = AgentOrchestrator(vs).run(
                        query,
                        trace_active=st.session_state.get("trace_loaded", False),
                        docs_info=docs_info,
                    )
                    _elapsed = time.perf_counter() - _t0
                except Exception as exc:
                    error_msg = f"Agent error: {exc}"
                    st.session_state.messages.append(
                        {"role": "assistant", "content": error_msg,
                         "reasoning_trace": [], "call_flow": None}
                    )
                    st.error(error_msg)
                    st.stop()

        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": result["answer"],
                "reasoning_trace": result.get("reasoning_trace", []),
                "call_flow": result.get("call_flow"),
                "groq_rate_limited": result.get("groq_rate_limited", False),
                "ollama_model": result.get("ollama_model", "gemma4:e4b"),
                "elapsed_seconds": round(_elapsed, 1),
            }
        )
        st.rerun()


if __name__ == "__main__":
    main()
