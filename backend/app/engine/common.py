"""Shared XML helpers. No FastAPI imports: the engine is usable from the command line."""
import hashlib
import zipfile
from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
NS = {"w": W, "m": M, "wp": WP, "mc": MC}

# Fonts used for symbols/math: runs using them keep their font on purpose.
SYMBOL_FONTS = {"symbol", "wingdings", "wingdings 2", "wingdings 3", "webdings", "cambria math", "mt extra"}


def q(tag: str) -> str:
    pfx, name = tag.split(":")
    return "{%s}%s" % (NS[pfx], name)


def safe_parser() -> etree.XMLParser:
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, remove_blank_text=False)


def parse_xml(data: bytes):
    return etree.fromstring(data, safe_parser())


_RUNS = etree.XPath(".//w:r[not(ancestor::w:txbxContent) and not(ancestor::mc:Fallback)]", namespaces=NS)


def iter_runs(p_el):
    """Runs belonging to this paragraph (not those inside text boxes or compatibility fallbacks)."""
    return _RUNS(p_el)


def run_font_names(r_el) -> list:
    rpr = r_el.find(q("w:rPr"))
    if rpr is None:
        return []
    rf = rpr.find(q("w:rFonts"))
    if rf is None:
        return []
    return [v.lower() for k, v in rf.attrib.items() if k.split("}")[-1] in ("ascii", "hAnsi", "cs", "eastAsia")]


def is_symbol_run(r_el) -> bool:
    return any(n in SYMBOL_FONTS for n in run_font_names(r_el)) or r_el.find(q("w:sym")) is not None


def text_parts(zf: zipfile.ZipFile) -> list:
    pat = ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml", "word/comments.xml")
    return sorted(n for n in zf.namelist() if n in pat or (n.startswith("word/header") or n.startswith("word/footer")) and n.endswith(".xml"))


def part_text_fingerprint(zf: zipfile.ZipFile, name: str) -> tuple:
    """(hash, paragraph_count) of the ordered text of one XML part."""
    root = parse_xml(zf.read(name))
    h = hashlib.sha256()
    n = 0
    for p in root.iter(q("w:p")):
        n += 1
        parts = [t.text or "" for t in p.iter(q("w:t"), q("w:delText"), q("w:instrText"), q("m:t"))]
        h.update("".join(parts).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest(), n


def fingerprints(path) -> dict:
    with zipfile.ZipFile(path) as zf:
        return {n: part_text_fingerprint(zf, n) for n in text_parts(zf)}


def structure_counts(path) -> dict:
    """Counts of key structural elements, used to confirm nothing was lost."""
    with zipfile.ZipFile(path) as zf:
        root = parse_xml(zf.read("word/document.xml"))
        c = {
            "tables": len(root.findall(".//" + q("w:tbl"))),
            "inline_images": len(root.findall(".//" + q("wp:inline"))),
            "floating_objects": len(root.findall(".//" + q("wp:anchor"))),
            "hyperlinks": len(root.findall(".//" + q("w:hyperlink"))),
            "sections": len(root.findall(".//" + q("w:sectPr"))),
            "equations": len(root.findall(".//" + q("m:oMath"))),
            "list_paragraphs": len(root.findall(".//" + q("w:numPr"))),
            "bold_markers": len(root.findall(".//" + q("w:b"))),
            "italic_markers": len(root.findall(".//" + q("w:i"))),
            "underline_markers": len(root.findall(".//" + q("w:u"))),
            "footnote_references": len(root.findall(".//" + q("w:footnoteReference"))),
            "header_footer_references": len(root.findall(".//" + q("w:headerReference"))) + len(root.findall(".//" + q("w:footerReference"))),
        }
        names = zf.namelist()
        c["header_footer_parts"] = len([n for n in names if n.startswith(("word/header", "word/footer")) and n.endswith(".xml")])
        c["media_files"] = hashlib.sha256("|".join(sorted(hashlib.sha256(zf.read(n)).hexdigest() for n in names if n.startswith("word/media/"))).encode()).hexdigest()
        c["media_count"] = len([n for n in names if n.startswith("word/media/")])
        return c


def detect_limitations(path) -> list:
    """Things the engine does not reformat or cannot guarantee. Returned as (code, message)."""
    out = []
    with zipfile.ZipFile(path) as zf:
        root = parse_xml(zf.read("word/document.xml"))
        names = zf.namelist()

        def count(xp):
            return len(root.xpath(xp, namespaces=NS))

        if count("//w:ins | //w:del"):
            out.append(("tracked_changes", "The document contains tracked changes. They were left as they are; accept or reject them in Word first if you want them formatted."))
        if "word/comments.xml" in names:
            out.append(("comments", "The document contains comments. They were kept but not reformatted."))
        if count("//w:txbxContent"):
            out.append(("text_boxes", "Text boxes were kept but their text was not reformatted."))
        if count("//w:sdt"):
            out.append(("content_controls", "Content controls (form fields, building blocks) were kept but not reformatted."))
        if count("//w:instrText[contains(., 'TOC')]"):
            out.append(("toc", "A table of contents field is present. Update it in Word after opening the file."))
        if count("//wp:anchor"):
            out.append(("floating_objects", "Floating images or shapes are present. Their positions may shift after the page size change."))
        if count("//w:pict | //w:object"):
            out.append(("legacy_objects", "Legacy drawings or embedded objects are present and were not modified."))
        if any(n.startswith("word/diagrams/") for n in names):
            out.append(("smartart", "SmartArt diagrams are present and were not modified."))
        if "word/footnotes.xml" in names and len(parse_xml(zf.read("word/footnotes.xml")).findall(q("w:footnote"))) > 2:
            out.append(("footnotes", "Footnotes were kept but their text was not reformatted."))
        if count("//w:altChunk"):
            out.append(("alt_chunk", "The document embeds imported content blocks that were not reformatted."))
    return out
