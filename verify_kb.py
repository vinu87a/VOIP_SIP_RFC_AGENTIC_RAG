"""
Verify which RFCs are currently indexed in the ChromaDB knowledge base.
Run: python3 verify_kb.py
"""
import sys
sys.path.insert(0, ".")
from collections import Counter
from config import RFC_NUMBERS
from store.vector_store import VectorStore

vs    = VectorStore()
total = vs.rfc_count()

print(f"\nTotal chunks in knowledge base: {total}\n")

if total == 0:
    print("Collection is empty — click 'Re-index RFCs' in the sidebar.")
    sys.exit(0)

results = vs._rfc_col.get(include=["metadatas"])
counts  = Counter(m.get("rfc_no") for m in results["metadatas"])

print(f"{'RFC':<8} {'Title':<55} {'Chunks':>7}  {'Status'}")
print("-" * 85)

from config import RFC_META
for rfc in sorted(RFC_NUMBERS):
    title  = RFC_META.get(rfc, {}).get("title", "")[:54]
    chunks = counts.get(rfc, 0)
    status = "OK" if chunks > 0 else "MISSING"
    print(f"RFC {rfc:<5} {title:<55} {chunks:>7}  {status}")

print("-" * 85)
print(f"{'TOTAL':<64} {total:>7}  chunks across {len(counts)} RFCs\n")

extra_sources = [
    ("IANA-SIP",        "IANA SIP Parameters Registry"),
    ("WIKI-SIP-CODES",  "Wikipedia: List of SIP Response Codes"),
    ("SIP-GLOSSARY",    "SIP/VoIP Terminology Glossary"),
]
for doc_id, label in extra_sources:
    n = counts.get(doc_id, 0)
    status = "OK" if n > 0 else "MISSING"
    print(f"{doc_id:<12} {label:<51} {n:>7}  {status}")

print("-" * 85)
print(f"{'TOTAL':<64} {total:>7}  chunks across {len(counts)} sources\n")

missing = [n for n in RFC_NUMBERS if n not in counts]
if missing:
    print(f"Missing RFCs ({len(missing)}): {missing}")
    print("-> Click 'Re-index RFCs' in the Streamlit sidebar to ingest them.")
else:
    extras_ok  = [label for doc_id, label in extra_sources if counts.get(doc_id, 0) > 0]
    extras_str = (" + " + " + ".join(extras_ok)) if extras_ok else ""
    print(f"All {len(RFC_NUMBERS)} RFCs{extras_str} are fully indexed.")
