import re
import logging
from typing import Any, Dict, List

from bs4 import BeautifulSoup

from .text_parser import parse_text_trace

logger = logging.getLogger(__name__)

_SIP_SIGNAL = re.compile(r'SIP/2\.0|INVITE\s|REGISTER\s|BYE\s|ACK\s', re.IGNORECASE)


def _extract_sip_blocks(soup: BeautifulSoup) -> List[str]:
    """
    Try multiple strategies to pull raw SIP text out of HTML.
    Returns a list of text blocks that appear to contain SIP content.
    """
    blocks: List[str] = []

    # Strategy 1 — <pre> tags (Wireshark "File → Export as HTML", sngrep HTML)
    for pre in soup.find_all("pre"):
        text = pre.get_text(separator="\n")
        if _SIP_SIGNAL.search(text):
            blocks.append(text)

    if blocks:
        return blocks

    # Strategy 2 — <td> cells in Wireshark packet-detail tables
    for td in soup.find_all("td"):
        text = td.get_text(separator="\n")
        if _SIP_SIGNAL.search(text) and len(text) > 80:
            blocks.append(text)

    if blocks:
        return blocks

    # Strategy 3 — <div> or <p> elements with SIP content
    for tag in soup.find_all(["div", "p", "span"]):
        text = tag.get_text(separator="\n")
        if _SIP_SIGNAL.search(text) and len(text) > 80:
            blocks.append(text)

    if blocks:
        return blocks

    # Strategy 4 — full page text as last resort
    full = soup.get_text(separator="\n")
    if _SIP_SIGNAL.search(full):
        blocks.append(full)

    return blocks


def parse_html_trace(content: str) -> List[Dict[str, Any]]:
    """
    Parse SIP trace from an HTML file (Wireshark HTML export or similar).
    Extracts raw SIP text blocks, then delegates to the text parser.
    """
    soup = BeautifulSoup(content, "lxml")

    # Remove non-content elements
    for tag in soup(["script", "style", "nav", "head", "footer"]):
        tag.decompose()

    blocks = _extract_sip_blocks(soup)

    if not blocks:
        logger.warning("HTML parser: no SIP content found in document")
        return []

    combined = "\n\n".join(blocks)
    messages = parse_text_trace(combined)
    logger.info(f"HTML parser: extracted {len(messages)} SIP messages from {len(blocks)} block(s)")
    return messages
