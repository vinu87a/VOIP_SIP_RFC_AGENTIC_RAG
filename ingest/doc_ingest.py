"""
Universal document ingestion for user-uploaded files and web URLs.

Supported input types
---------------------
  .pdf          — pypdf text extraction (page-by-page)
  .docx         — python-docx paragraph extraction
  .doc          — legacy Word; raises ValueError asking user to convert to .docx
  .html / .htm  — BeautifulSoup text extraction (scripts/styles stripped)
  .txt / .md / .log / .csv  — plain UTF-8 text
  URL           — fetched with requests, parsed as HTML or PDF by content-type

Each source is split into overlapping character chunks (~1 500 chars, 200 overlap)
and returned as a list of dicts ready for VectorStore.add_doc_chunks().
"""

import io
import logging
import re
import uuid
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_CHUNK_SIZE    = 1500
_CHUNK_OVERLAP = 200

_URL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


# ── Text utilities ─────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    text = re.sub(r"\r\n|\r", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _split_chunks(text: str) -> List[str]:
    """
    Split *text* into overlapping chunks on paragraph boundaries.
    Falls back to hard splits when a single paragraph exceeds chunk_size.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    chunks: List[str] = []
    current = ""

    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) > _CHUNK_SIZE and current:
            chunks.append(current.strip())
            overlap = current[-_CHUNK_OVERLAP:] if len(current) > _CHUNK_OVERLAP else current
            current = f"{overlap}\n\n{para}"
        else:
            current = candidate

    if current.strip():
        chunks.append(current.strip())

    # Hard-split any chunk that's still too long (e.g. a giant single paragraph)
    final: List[str] = []
    for chunk in chunks:
        if len(chunk) <= _CHUNK_SIZE * 1.5:
            final.append(chunk)
        else:
            for i in range(0, len(chunk), _CHUNK_SIZE - _CHUNK_OVERLAP):
                final.append(chunk[i : i + _CHUNK_SIZE])
    return final


def _to_chunk_dicts(
    texts: List[str], doc_id: str, doc_name: str, doc_type: str
) -> List[Dict]:
    return [
        {
            "id":        f"{doc_id}_c{i}_{uuid.uuid4().hex[:6]}",
            "text":      t,
            "doc_id":    doc_id,
            "doc_name":  doc_name,
            "doc_type":  doc_type,
            "chunk_idx": i,
        }
        for i, t in enumerate(texts)
    ]


# ── Format-specific parsers ────────────────────────────────────────────────────

def _parse_pdf(content: bytes, doc_id: str, doc_name: str) -> List[Dict]:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(content))
    pages = [_clean(p.extract_text() or "") for p in reader.pages]
    full  = "\n\n".join(p for p in pages if p)
    chunks = _split_chunks(full)
    logger.info("PDF '%s': %d pages → %d chunks", doc_name, len(reader.pages), len(chunks))
    return _to_chunk_dicts(chunks, doc_id, doc_name, "pdf")


def _parse_docx(content: bytes, doc_id: str, doc_name: str) -> List[Dict]:
    from docx import Document
    doc  = Document(io.BytesIO(content))
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    full  = _clean("\n\n".join(paras))
    chunks = _split_chunks(full)
    logger.info("DOCX '%s': %d paragraphs → %d chunks", doc_name, len(paras), len(chunks))
    return _to_chunk_dicts(chunks, doc_id, doc_name, "docx")


def _parse_html(content: bytes, doc_id: str, doc_name: str) -> List[Dict]:
    soup = BeautifulSoup(content, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    full   = _clean(soup.get_text(separator="\n"))
    chunks = _split_chunks(full)
    logger.info("HTML '%s': %d chunks", doc_name, len(chunks))
    return _to_chunk_dicts(chunks, doc_id, doc_name, "html")


def _parse_txt(content: bytes, doc_id: str, doc_name: str) -> List[Dict]:
    full   = _clean(content.decode("utf-8", errors="ignore"))
    chunks = _split_chunks(full)
    logger.info("TXT '%s': %d chunks", doc_name, len(chunks))
    return _to_chunk_dicts(chunks, doc_id, doc_name, "txt")


# ── Public API ─────────────────────────────────────────────────────────────────

def ingest_file(
    content: bytes,
    filename: str,
    doc_id: Optional[str] = None,
) -> Tuple[List[Dict], str]:
    """
    Parse an uploaded file and return *(chunks, doc_id)*.

    Parameters
    ----------
    content  : raw bytes from the uploaded file
    filename : original file name (used for extension detection and display)
    doc_id   : stable identifier; auto-generated if omitted
    """
    if doc_id is None:
        doc_id = f"DOC-{uuid.uuid4().hex[:8].upper()}"

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "pdf":
        chunks = _parse_pdf(content, doc_id, filename)
    elif ext in ("html", "htm"):
        chunks = _parse_html(content, doc_id, filename)
    elif ext == "docx":
        chunks = _parse_docx(content, doc_id, filename)
    elif ext == "doc":
        raise ValueError(
            "Legacy .doc format is not supported. "
            "Please open the file in Word and save it as .docx, then re-upload."
        )
    else:
        # txt, md, log, csv, and any unknown extension → plain text
        chunks = _parse_txt(content, doc_id, filename)

    return chunks, doc_id


def ingest_url(
    url: str,
    doc_id: Optional[str] = None,
) -> Tuple[List[Dict], str]:
    """
    Fetch *url* and parse its content. Returns *(chunks, doc_id)*.

    PDF URLs are parsed with pypdf; everything else with BeautifulSoup.
    """
    if doc_id is None:
        doc_id = f"DOC-{uuid.uuid4().hex[:8].upper()}"

    logger.info("Fetching URL: %s", url)
    r = requests.get(url, timeout=30, headers=_URL_HEADERS)
    r.raise_for_status()

    parsed   = urlparse(url)
    doc_name = (parsed.netloc + parsed.path).strip("/") or url

    content_type = r.headers.get("Content-Type", "")
    if "pdf" in content_type or url.lower().endswith(".pdf"):
        chunks = _parse_pdf(r.content, doc_id, doc_name)
    else:
        chunks = _parse_html(r.content, doc_id, doc_name)

    logger.info("URL '%s': %d chunks, doc_id=%s", url, len(chunks), doc_id)
    return chunks, doc_id
