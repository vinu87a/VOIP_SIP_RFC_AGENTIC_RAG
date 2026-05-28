import os
import time
import logging
import requests

from config import RFC_NUMBERS, RFC_CACHE_DIR

logger = logging.getLogger(__name__)

RFC_URL_TEMPLATE = "https://www.rfc-editor.org/rfc/rfc{}.txt"


def fetch_rfc(rfc_no: int, force_refresh: bool = False) -> str:
    """Download RFC plain text, caching to disk to avoid repeated network calls."""
    os.makedirs(RFC_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(RFC_CACHE_DIR, f"rfc{rfc_no}.txt")

    if not force_refresh and os.path.exists(cache_path):
        logger.info(f"RFC {rfc_no}: loaded from cache")
        with open(cache_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    url = RFC_URL_TEMPLATE.format(rfc_no)
    logger.info(f"RFC {rfc_no}: fetching from {url}")

    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            text = resp.text
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(text)
            logger.info(f"RFC {rfc_no}: {len(text):,} chars saved to cache")
            return text
        except requests.RequestException as exc:
            if attempt == 2:
                raise RuntimeError(f"Failed to fetch RFC {rfc_no} after 3 attempts: {exc}")
            wait = 2 ** attempt
            logger.warning(f"RFC {rfc_no}: attempt {attempt + 1} failed, retrying in {wait}s")
            time.sleep(wait)


def fetch_all_rfcs(force_refresh: bool = False) -> dict:
    """
    Download all configured RFCs.
    Returns {rfc_no: text_content}.
    Skips any RFC that fails with a warning rather than aborting.
    """
    results = {}
    for rfc_no in RFC_NUMBERS:
        try:
            results[rfc_no] = fetch_rfc(rfc_no, force_refresh=force_refresh)
        except Exception as exc:
            logger.error(f"Skipping RFC {rfc_no}: {exc}")
    logger.info(f"Fetched {len(results)}/{len(RFC_NUMBERS)} RFCs successfully")
    return results
