"""
Fetches and ingests the IANA SIP Parameters registry into the RFC knowledge base.
Source: https://www.iana.org/assignments/sip-parameters/sip-parameters.xml

Each registry section becomes one or more searchable chunks stored alongside
the RFC content in the sip_rfcs ChromaDB collection.
"""

import logging
import uuid
from typing import List, Dict
from xml.etree import ElementTree as ET

import requests

logger = logging.getLogger(__name__)

IANA_URL = "https://www.iana.org/assignments/sip-parameters/sip-parameters.xml"
IANA_NS  = {"iana": "http://www.iana.org/assignments"}

# Registries to include, in order of importance for SIP analysis
_REGISTRY_PRIORITY = [
    ("sip-parameters-7",  "SIP Response Codes"),
    ("sip-parameters-2",  "SIP Header Fields"),
    ("sip-parameters-12", "SIP Header Field Parameters"),
    ("sip-parameters-6",  "SIP Methods"),
    ("sip-parameters-4",  "SIP Option Tags"),
    ("sip-parameters-5",  "SIP Warning Codes"),
    ("sip-parameters-11", "SIP/SIPS URI Parameters"),
    ("sip-parameters-3",  "SIP Reason Protocols"),
    ("sip-parameters-8",  "SIP Privacy Header Values"),
    ("sip-parameters-9",  "SIP Security Mechanism Names"),
    ("sip-transport",     "SIP Transport"),
    ("sip-parameters-13", "SIP URI Purposes"),
    ("sip-parameters-66", "SIP Info Packages"),
    ("sip-parameters-69", "SIP Reason Codes"),
    ("sip-parameters-73", "SIP Priority Header Values"),
]


def _record_to_text(record, ns: dict) -> str:
    """Flatten one <record> element to a single readable line."""
    parts = []
    for child in record:
        tag = child.tag.split("}")[-1]  # strip namespace
        if child.text and child.text.strip():
            val = " ".join(child.text.split())  # normalise whitespace
            parts.append(f"{tag}: {val}")
    return "  |  ".join(parts)


def _registry_to_chunks(
    root,
    reg_id: str,
    reg_title: str,
    doc_id: str,
    chunk_size: int = 1800,
) -> List[Dict]:
    """Convert one IANA registry into 1–N chunks."""
    reg = root.find(f".//iana:registry[@id='{reg_id}']", IANA_NS)
    if reg is None:
        logger.warning("Registry '%s' not found in IANA XML", reg_id)
        return []

    # Collect registry-level description / notes
    title_el = reg.find("iana:title", IANA_NS)
    title    = title_el.text.strip() if title_el is not None else reg_title
    note_els = reg.findall("iana:note", IANA_NS)
    notes    = " ".join(
        " ".join(n.text.split()) for n in note_els if n.text and n.text.strip()
    )

    header = f"IANA Registry: {title}\n"
    if notes:
        header += f"Note: {notes}\n"
    header += "\nEntries:\n"

    records = reg.findall("iana:record", IANA_NS)
    lines   = [_record_to_text(r, IANA_NS) for r in records if _record_to_text(r, IANA_NS)]

    # Split into chunks that fit within chunk_size
    chunks:  List[Dict] = []
    current  = header
    chunk_idx = 0

    for line in lines:
        candidate = current + line + "\n"
        if len(candidate) > chunk_size and len(current) > len(header):
            chunks.append(_make_chunk(current.strip(), doc_id, reg_title, chunk_idx))
            chunk_idx += 1
            current = header + line + "\n"
        else:
            current = candidate

    if current.strip() and current.strip() != header.strip():
        chunks.append(_make_chunk(current.strip(), doc_id, reg_title, chunk_idx))

    return chunks


def _make_chunk(text: str, doc_id: str, section_title: str, idx: int) -> Dict:
    return {
        "id":            f"{doc_id}_chunk_{idx}_{uuid.uuid4().hex[:6]}",
        "text":          text,
        "rfc_no":        doc_id,
        "rfc_title":     "IANA SIP Parameters Registry",
        "section_no":    str(idx),
        "section_title": section_title,
        "chunk_idx":     idx,
    }


def fetch_iana_sip_chunks() -> List[Dict]:
    """Download the IANA SIP parameters XML and return all chunks."""
    logger.info("Fetching IANA SIP parameters from %s", IANA_URL)
    r = requests.get(IANA_URL, timeout=20)
    r.raise_for_status()

    root = ET.fromstring(r.content)
    all_chunks: List[Dict] = []

    for reg_id, reg_title in _REGISTRY_PRIORITY:
        chunks = _registry_to_chunks(root, reg_id, reg_title, doc_id="IANA-SIP")
        logger.info("  %-45s  %d chunks", reg_title, len(chunks))
        all_chunks.extend(chunks)

    logger.info("IANA ingest complete: %d total chunks", len(all_chunks))
    return all_chunks
