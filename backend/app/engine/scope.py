"""Decides which paragraphs the profile applies to. Style names only: no guessing from appearance."""
from .common import q

BODY_STYLES = {"normal", "body text"}
HEADING_IDS = {"heading1": "Heading 1", "heading2": "Heading 2"}
HEADING_NAMES = {"heading 1": "Heading 1", "heading 2": "Heading 2"}


def classify(p, style_id: str, style_name: str) -> str:
    """Returns 'body', 'Heading 1', 'Heading 2', or a skip reason."""
    sid = (style_id or "").lower()
    name = (style_name or "").lower()
    if sid in HEADING_IDS:
        return HEADING_IDS[sid]
    if name in HEADING_NAMES:
        return HEADING_NAMES[name]
    if name in BODY_STYLES or sid in ("normal", "bodytext"):
        ppr = p.find(q("w:pPr"))
        if ppr is not None and ppr.find(q("w:numPr")) is not None:
            return "skip:list"
        if p.find(q("m:oMathPara")) is not None:
            return "skip:equation"
        return "body"
    return "skip:other-style"
