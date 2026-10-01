"""Immutable, lossless source slices. AI returns IDs; Python supplies exact text."""
import re
from .common import V6Error, digest

EVIDENCE_VERSION = "source-spans-1"
MAX_UNIT_CHARS = 240


def source_units(article):
    units = []
    for field, prefix in (("title", "T"), ("body", "B")):
        text = article[field]
        start, index = 0, 1
        while start < len(text):
            limit = min(start + MAX_UNIT_CHARS, len(text))
            # Retain punctuation and whitespace; offsets are Python Unicode indices.
            piece = text[start:limit]
            boundaries = list(re.finditer(r"\n+|(?<!\d)[.!?。！？]{1,}[ \t]*", piece))
            end = start + boundaries[0].end() if boundaries else limit
            if not boundaries and limit < len(text):
                gap = piece.rfind(" ")
                if gap >= MAX_UNIT_CHARS // 2:
                    end = start + gap + 1
            units.append({"id": f"{prefix}{index:04d}", "source": field,
                          "start": start, "end": end, "text": text[start:end]})
            start, index = end, index + 1
    return units


def resolve_evidence(data, article):
    units = source_units(article)
    by_id = {u["id"]: u for u in units}
    bindings = []

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, val in node.items():
                child = f"{path}.{key}" if path else key
                if key.endswith("evidence_ids"):
                    if len(val) != len(set(val)):
                        raise V6Error("INVALID_EVIDENCE", f"{child}: 중복 근거 번호가 있습니다.")
                    for uid in val:
                        if uid not in by_id or not by_id[uid]["text"].strip():
                            raise V6Error("INVALID_EVIDENCE", f"{child}: 원문에 없는 근거 번호입니다: {uid}")
                    bindings.append({"field": child, "evidence_ids": list(val),
                                     "quotes": [dict(by_id[uid]) for uid in val]})
                else:
                    walk(val, child)
        elif isinstance(node, list):
            for i, val in enumerate(node):
                walk(val, f"{path}[{i}]")

    walk(data)
    return {"version": EVIDENCE_VERSION, "offset_unit": "Python Unicode code points; end exclusive",
            "units_sha256": digest(units), "units": units, "bindings": bindings}


def evidence_text(fact, units):
    by_id = {u["id"]: u["text"] for u in units}
    return "\n".join(by_id[x] for x in fact["evidence_ids"])
