"""
Fetches and ingests the Wikipedia 'List of SIP response codes' page.
Source: https://en.wikipedia.org/wiki/List_of_SIP_response_codes

Adds full descriptions, RFC section references, vendor-specific codes,
and deprecation context — content the IANA registry (code + short name only)
does not provide.
"""

import logging
import uuid
from typing import List, Dict

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_SIP_response_codes"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# Sections that contain response code definitions
_CODE_SECTIONS = [
    "1xx",
    "2xx",
    "3xx",
    "4xx",
    "5xx",
    "6xx",
]


def _clean(text: str) -> str:
    """Normalise whitespace and strip citation markers like [1], [2]."""
    import re
    text = re.sub(r"\[\s*\d+\s*\]", "", text)   # remove [1], [23], etc.
    text = " ".join(text.split())
    return text.strip()


def _extract_sections(soup: BeautifulSoup) -> Dict[str, List[str]]:
    """
    Walk the page DOM and collect (code, description) pairs per section.
    Returns { "1xx—Provisional Responses": ["100 Trying\n<desc>", ...], ... }
    """
    sections: Dict[str, List[str]] = {}
    current_section: str | None = None
    current_code: str | None = None
    current_desc_lines: List[str] = []

    def flush():
        if current_section and current_code:
            entry = current_code
            if current_desc_lines:
                entry += "\n" + " ".join(current_desc_lines)
            sections.setdefault(current_section, []).append(entry)

    for tag in soup.find_all(["h2", "dt", "dd", "p"]):
        if tag.name == "h2":
            flush()
            current_code = None
            current_desc_lines = []
            text = tag.get_text(strip=True)
            if any(f"{x}" in text for x in _CODE_SECTIONS):
                current_section = text
            else:
                current_section = None
            continue

        if current_section is None:
            continue

        text = _clean(tag.get_text(" ", strip=True))
        if not text:
            continue

        if tag.name == "dt":
            # New response code entry
            flush()
            current_code = text
            current_desc_lines = []
        elif tag.name in ("dd", "p") and current_code:
            if text and text not in (current_code,):
                current_desc_lines.append(text)

    flush()
    return sections


def _sections_to_chunks(
    sections: Dict[str, List[str]],
    doc_id: str = "WIKI-SIP-CODES",
    chunk_size: int = 1800,
) -> List[Dict]:
    chunks: List[Dict] = []

    for section_title, entries in sections.items():
        header      = f"Wikipedia SIP Response Codes — {section_title}\n\n"
        current     = header
        chunk_idx   = 0

        for entry in entries:
            block     = entry + "\n\n"
            candidate = current + block
            if len(candidate) > chunk_size and len(current) > len(header):
                chunks.append({
                    "id":            f"{doc_id}_{section_title[:6]}_{chunk_idx}_{uuid.uuid4().hex[:6]}",
                    "text":          current.strip(),
                    "rfc_no":        doc_id,
                    "rfc_title":     "Wikipedia: List of SIP Response Codes",
                    "section_no":    str(chunk_idx),
                    "section_title": section_title,
                    "chunk_idx":     chunk_idx,
                })
                chunk_idx += 1
                current = header + block
            else:
                current = candidate

        if current.strip() and current.strip() != header.strip():
            chunks.append({
                "id":            f"{doc_id}_{section_title[:6]}_{chunk_idx}_{uuid.uuid4().hex[:6]}",
                "text":          current.strip(),
                "rfc_no":        doc_id,
                "rfc_title":     "Wikipedia: List of SIP Response Codes",
                "section_no":    str(chunk_idx),
                "section_title": section_title,
                "chunk_idx":     chunk_idx,
            })

    return chunks


def fetch_wikipedia_sip_chunks() -> List[Dict]:
    """Download Wikipedia SIP response codes page and return all chunks."""
    logger.info("Fetching Wikipedia SIP response codes from %s", WIKI_URL)
    r = requests.get(WIKI_URL, timeout=20, headers=_HEADERS)
    r.raise_for_status()

    soup     = BeautifulSoup(r.content, "lxml")
    sections = _extract_sections(soup)

    total_entries = sum(len(v) for v in sections.values())
    logger.info("Extracted %d response code entries across %d sections",
                total_entries, len(sections))

    chunks = _sections_to_chunks(sections)

    for sec, items in sections.items():
        sec_chunks = [c for c in chunks if c["section_title"] == sec]
        logger.info("  %-45s  %3d entries  ->  %d chunks", sec, len(items), len(sec_chunks))

    logger.info("Wikipedia ingest complete: %d total chunks", len(chunks))
    return chunks
