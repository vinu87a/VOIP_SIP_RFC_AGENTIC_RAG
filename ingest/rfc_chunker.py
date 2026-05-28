import re
import logging
from typing import Any, Dict, List

from config import CHUNK_SIZE, CHUNK_OVERLAP, RFC_META

logger = logging.getLogger(__name__)

# Matches RFC page-break artifacts:  "Rosenberg, et al.    Standards Track   [Page 12]"
_PAGE_BREAK = re.compile(
    r'\n[^\n]{0,100}\[Page\s+\d+\]\s*\n'
    r'(?:[ \t]*\n)*'                         # blank lines after footer
    r'(?:[^\n]{0,100}RFC\s+\d+[^\n]*\n)?',  # optional next-page header
    re.MULTILINE,
)

# Section header: "3.1.  Title Text" or "10.  Title"
_SECTION_HDR = re.compile(
    r'^(\d+(?:\.\d+)*\.?)\s{2,}(\S[^\n]*)',
    re.MULTILINE,
)

# Appendix headers: "Appendix A.  Title" or "A.  Title"
_APPENDIX_HDR = re.compile(
    r'^(Appendix\s+[A-Z]\.?|[A-Z]\.)\s{2,}(\S[^\n]*)',
    re.MULTILINE,
)


def _clean_rfc_text(text: str) -> str:
    text = _PAGE_BREAK.sub('\n', text)
    text = re.sub(r'\r\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _split_into_sections(text: str) -> List[Dict[str, str]]:
    """
    Split RFC body into logical sections using numbered headings.
    Falls back to treating the whole text as one section if no headers found.
    """
    matches = list(_SECTION_HDR.finditer(text)) + list(_APPENDIX_HDR.finditer(text))
    matches.sort(key=lambda m: m.start())

    if not matches:
        return [{"section_no": "0", "section_title": "Full Document", "content": text}]

    sections: List[Dict[str, str]] = []

    # Preamble: everything before the first section header
    preamble = text[: matches[0].start()].strip()
    if preamble:
        sections.append(
            {"section_no": "0", "section_title": "Preamble / Abstract", "content": preamble}
        )

    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        sections.append(
            {
                "section_no": m.group(1).rstrip("."),
                "section_title": m.group(2).strip(),
                "content": content,
            }
        )

    return sections


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    Split text into overlapping character-level chunks.
    Prefers breaking at paragraph boundaries (\n\n) to preserve coherence.
    """
    if len(text) <= chunk_size:
        return [text]

    chunks: List[str] = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))

        # Prefer breaking at a paragraph boundary in the latter half of the window
        if end < len(text):
            boundary = text.rfind("\n\n", start + chunk_size // 2, end)
            if boundary != -1:
                end = boundary

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break
        start = end - overlap

    return chunks


def chunk_rfc(rfc_no: int, text: str) -> List[Dict[str, Any]]:
    """
    Parse and chunk an RFC text file.
    Returns a list of chunk dicts ready for insertion into the vector store.
    """
    meta = RFC_META.get(rfc_no, {"title": f"RFC {rfc_no}", "topics": []})
    clean = _clean_rfc_text(text)
    sections = _split_into_sections(clean)

    chunks: List[Dict[str, Any]] = []
    for sec in sections:
        sub_chunks = _chunk_text(sec["content"])
        for idx, sub in enumerate(sub_chunks):
            chunks.append(
                {
                    "text": sub,
                    "rfc_no": rfc_no,
                    "rfc_title": meta["title"],
                    "section_no": sec["section_no"],
                    "section_title": sec["section_title"],
                    "chunk_idx": idx,
                    # Unique, stable document ID for ChromaDB upsert
                    "id": f"rfc{rfc_no}_s{sec['section_no'].replace('.', '_')}_c{idx}",
                }
            )

    logger.info(
        f"RFC {rfc_no} — {len(sections)} sections → {len(chunks)} chunks "
        f"(avg {len(clean) // max(len(chunks), 1)} chars each)"
    )
    return chunks
