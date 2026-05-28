import re
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# SIP methods per RFC 3261 + extensions
_SIP_METHODS = (
    "INVITE|REGISTER|BYE|ACK|CANCEL|OPTIONS|SUBSCRIBE|NOTIFY|"
    "PUBLISH|MESSAGE|REFER|INFO|UPDATE|PRACK"
)

# Request-line: METHOD <request-uri> SIP/2.0
_REQUEST_LINE = re.compile(
    rf'^({_SIP_METHODS})\s+\S+\s+SIP/2\.0',
    re.IGNORECASE,
)
# Response-line: SIP/2.0 <code> <reason>
_RESPONSE_LINE = re.compile(r'^SIP/2\.0\s+(\d{{3}})\s+(.*)', re.IGNORECASE)
_RESPONSE_LINE = re.compile(r'^SIP/2\.0\s+(\d{3})\s+(.*)', re.IGNORECASE)

# Boundary pattern: a new SIP message starts here
_MSG_BOUNDARY = re.compile(
    rf'(?=^(?:{_SIP_METHODS})\s|\bSIP/2\.0\s+\d{{3}}\b)',
    re.MULTILINE | re.IGNORECASE,
)


def _parse_headers(header_block: str) -> Dict[str, str]:
    """Parse SIP header block into a lowercase-key dict (last value wins for duplicates)."""
    headers: Dict[str, str] = {}
    for line in header_block.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            headers[key.strip().lower()] = val.strip()
    return headers


def build_message(raw: str) -> Dict[str, Any]:
    """Parse a single raw SIP message string into a structured dict."""
    # Split headers from body at the first blank line
    parts = re.split(r'\n\r?\n', raw, maxsplit=1)
    header_block = parts[0]
    body = parts[1].strip() if len(parts) > 1 else ""

    lines = header_block.splitlines()
    first_line = lines[0].strip() if lines else ""

    msg: Dict[str, Any] = {
        "raw": raw,
        "first_line": first_line,
        "body": body,
        "src_ip": "",
        "dst_ip": "",
        "src_port": 0,
        "dst_port": 0,
    }

    req_m = _REQUEST_LINE.match(first_line)
    resp_m = _RESPONSE_LINE.match(first_line)

    if req_m:
        msg["type"] = "request"
        msg["method"] = req_m.group(1).upper()
        msg["response_code"] = 0
        msg["reason"] = ""
    elif resp_m:
        msg["type"] = "response"
        msg["method"] = ""
        msg["response_code"] = int(resp_m.group(1))
        msg["reason"] = resp_m.group(2).strip()
    else:
        msg["type"] = "unknown"
        msg["method"] = ""
        msg["response_code"] = 0
        msg["reason"] = ""

    headers = _parse_headers("\n".join(lines[1:]))
    # Support compact header forms (RFC 3261 Section 7.3.3)
    msg["call_id"] = headers.get("call-id") or headers.get("i", "")
    msg["cseq"] = headers.get("cseq", "")
    msg["from"] = headers.get("from") or headers.get("f", "")
    msg["to"] = headers.get("to") or headers.get("t", "")
    msg["via"] = headers.get("via") or headers.get("v", "")
    msg["contact"] = headers.get("contact") or headers.get("m", "")
    msg["headers"] = headers

    return msg


# Timestamp patterns commonly found in SIP trace files (sngrep, sipp, syslog, etc.)
_TS_PATTERN = re.compile(
    r'(?:'
    r'\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?'          # ISO full
    r'|\d{2}:\d{2}:\d{2}(?:[.,]\d+)?'                                        # HH:MM:SS[.ms]
    r'|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}'  # syslog
    r')',
    re.IGNORECASE,
)


def parse_text_trace(content: str) -> List[Dict[str, Any]]:
    """
    Extract all SIP messages from a plain-text trace file.
    Handles concatenated message dumps (e.g., sipp logs, sngrep exports).
    Also extracts timestamps from common formats in the surrounding text.
    """
    messages: List[Dict[str, Any]] = []

    # Use finditer so we can inspect the text *before* each SIP boundary
    boundaries = [m.start() for m in _MSG_BOUNDARY.finditer(content)]

    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(content)
        seg = content[start:end].strip()
        if not seg:
            continue
        first = seg.splitlines()[0].strip()
        if not (_REQUEST_LINE.match(first) or _RESPONSE_LINE.match(first)):
            continue
        try:
            msg = build_message(seg)
            # 1. Look for a timestamp in the 300 chars immediately before this message
            preceding = content[max(0, start - 300):start]
            ts_hits = list(_TS_PATTERN.finditer(preceding))
            if ts_hits:
                msg["timestamp"] = ts_hits[-1].group()
            # 2. Fall back to the SIP Date: header inside the message
            elif msg.get("headers", {}).get("date"):
                msg["timestamp"] = msg["headers"]["date"]
            messages.append(msg)
        except Exception as exc:
            logger.debug(f"Skipping segment (parse error): {exc}")

    logger.info(f"Text parser: found {len(messages)} SIP messages")
    return messages
