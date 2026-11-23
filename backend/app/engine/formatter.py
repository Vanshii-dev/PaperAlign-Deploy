"""Applies a profile to a COPY of the uploaded DOCX. Only formatting properties are touched, never text."""
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Mm, Pt

from .common import detect_limitations, is_symbol_run, iter_runs, q
from .scope import classify

THEME_ATTRS = ("asciiTheme", "hAnsiTheme", "cstheme", "eastAsiaTheme")


@dataclass
class FormatResult:
    applied: list = field(default_factory=list)       # [{"label","detail"}]
    not_modified: list = field(default_factory=list)  # [{"label","count"}]
    issues: list = field(default_factory=list)        # [{"severity","code","message"}]
    stats: dict = field(default_factory=dict)


def find_style(doc, name: str, style_id: str):
    for s in doc.styles:
        if s.type == 1 and (s.style_id == style_id or (s.name or "").lower() == name.lower()):
            return s
    return None


def _set_style_font(style, font: str = None, size_pt: float = None, bold: bool = None):
    rpr = style.element.get_or_add_rPr()
    if font:
        rf = rpr.get_or_add_rFonts()
        for a in list(rf.attrib):
            del rf.attrib[a]
        for a in ("ascii", "hAnsi", "cs", "eastAsia"):
            rf.set(qn("w:" + a), font)
    if size_pt:
        style.font.size = Pt(size_pt)
        sz = rpr.find(qn("w:sz"))
        for old in rpr.findall(qn("w:szCs")):
            rpr.remove(old)
        cs = OxmlElement("w:szCs")
        cs.set(qn("w:val"), sz.get(qn("w:val")))
        sz.addnext(cs)
    if bold is not None:
        style.font.bold = bold


def _set_style_spacing(style, line: float, after_pt: float):
    pf = style.paragraph_format
    pf.line_spacing = line
    pf.space_after = Pt(after_pt)
    sp = style.element.get_or_add_pPr().find(qn("w:spacing"))
    for a in ("afterAutospacing", "afterLines"):
        if sp.get(qn("w:" + a)) is not None:
            del sp.attrib[qn("w:" + a)]


def _clear_direct_spacing(p):
    ppr = p.find(q("w:pPr"))
    if ppr is None:
        return False
    sp = ppr.find(q("w:spacing"))
    if sp is None:
        return False
    changed = False
    for a in ("line", "lineRule", "after", "afterAutospacing", "afterLines"):
        key = q("w:" + a)
        if key in sp.attrib:
            del sp.attrib[key]
            changed = True
    if not sp.attrib:
        ppr.remove(sp)
    return changed


def _clear_direct_run_font(r) -> bool:
    rpr = r.find(q("w:rPr"))
    if rpr is None:
        return False
    changed = False
    for tag in ("w:rFonts", "w:sz", "w:szCs"):
        for el in rpr.findall(q(tag)):
            rpr.remove(el)
            changed = True
    return changed


def format_docx(src: Path, dst: Path, profile: dict) -> FormatResult:
    res = FormatResult()
    doc = Document(str(src))
    page, body, heads = profile["page"], profile["body"], profile["headings"]

    # --- Page setup, every section ---------------------------------------
    doc.element.body.get_or_add_sectPr()
    sections = list(doc.sections)
    for s in sections:
        landscape = s.page_width is not None and s.page_height is not None and s.page_width > s.page_height
        w, h = Mm(page["width_mm"]), Mm(page["height_mm"])
        s.page_width, s.page_height = (h, w) if landscape else (w, h)
        for side in ("top_margin", "bottom_margin", "left_margin", "right_margin"):
            setattr(s, side, Inches(page["margin_in"]))
    res.applied.append({"label": "Page setup", "detail": "%s, %g in margins on all sides, applied to %d section(s); portrait/landscape orientation kept" % (page["size"], page["margin_in"], len(sections))})

    # --- Styles -----------------------------------------------------------
    normal = doc.styles.default(1)
    targets = [("Normal", normal)]
    bt = find_style(doc, "Body Text", "BodyText")
    if bt is not None:
        targets.append(("Body Text", bt))
    for name, st in targets:
        _set_style_font(st, body["font"], body["size_pt"])
        _set_style_spacing(st, body["line_spacing"], body["space_after_pt"])
    res.applied.append({"label": "Body style" + ("s" if len(targets) > 1 else ""), "detail": "%s: %s, %g pt, %g line spacing, %g pt after" % (" and ".join(n for n, _ in targets), body["font"], body["size_pt"], body["line_spacing"], body["space_after_pt"])})

    for hname, hrule in heads.items():
        hid = hname.replace(" ", "")
        st = find_style(doc, hname, hid)
        if st is None:
            res.issues.append({"severity": "warning", "code": "style_missing", "message": "The document has no %s style, so that rule was not applied." % hname})
            continue
        _set_style_font(st, body["font"], hrule["size_pt"], hrule.get("bold"))
        res.applied.append({"label": hname + " style", "detail": "%s, %g pt%s" % (body["font"], hrule["size_pt"], ", bold" if hrule.get("bold") else "")})

    # --- Direct formatting on in-scope body-level paragraphs ----------------
    counts = {"body": 0, "Heading 1": 0, "Heading 2": 0, "skip:list": 0, "skip:equation": 0, "skip:other-style": 0}
    runs_cleared = paras_cleared = symbol_runs = 0
    for para in doc.paragraphs:
        kind = classify(para._p, para.style.style_id if para.style is not None else "", para.style.name if para.style is not None else "")
        counts[kind] += 1
        if kind.startswith("skip"):
            continue
        if kind == "body" and _clear_direct_spacing(para._p):
            paras_cleared += 1
        for r in iter_runs(para._p):
            if is_symbol_run(r):
                symbol_runs += 1
                continue
            if _clear_direct_run_font(r):
                runs_cleared += 1
    res.applied.append({"label": "Direct formatting", "detail": "Removed conflicting font/size overrides from %d run(s) and spacing overrides from %d paragraph(s) so the profile takes effect" % (runs_cleared, paras_cleared)})

    in_table = len(doc.element.body.xpath(".//w:tbl//w:p"))
    res.not_modified = [
        {"label": "Table paragraphs", "count": in_table},
        {"label": "List paragraphs (body style with numbering)", "count": counts["skip:list"]},
        {"label": "Paragraphs with other styles (title, abstract, caption, quote, etc.)", "count": counts["skip:other-style"]},
        {"label": "Display equations", "count": counts["skip:equation"]},
        {"label": "Headers and footers", "count": len([n for n in doc.part.package.iter_parts() if "/header" in str(n.partname) or "/footer" in str(n.partname)])},
    ]
    res.stats = {"body_paragraphs": counts["body"], "heading1": counts["Heading 1"], "heading2": counts["Heading 2"],
                 "symbol_runs_preserved": symbol_runs, "runs_cleared": runs_cleared}
    doc.save(str(dst))

    for code, msg in detect_limitations(src):
        res.issues.append({"severity": "warning", "code": code, "message": msg})
    return res
