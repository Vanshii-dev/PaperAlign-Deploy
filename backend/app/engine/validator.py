"""Re-opens the generated file and checks it. Nothing here trusts the formatter's own bookkeeping."""
import zipfile
from pathlib import Path

from docx import Document

from .common import fingerprints, is_symbol_run, iter_runs, q, structure_counts
from .scope import classify

TOL_TWIPS = 20


def _check(cid, label, ok, detail="", status=None):
    return {"id": cid, "label": label, "status": status or ("passed" if ok else "failed"), "detail": detail}


# ---- style cascade ------------------------------------------------------------
class Cascade:
    def __init__(self, doc):
        root = doc.styles.element
        self.styles = {s.get(q("w:styleId")): s for s in root.findall(q("w:style"))}
        d = root.find(q("w:docDefaults"))
        self.rdef = d.find(q("w:rPrDefault") + "/" + q("w:rPr")) if d is not None else None
        self.pdef = d.find(q("w:pPrDefault") + "/" + q("w:pPr")) if d is not None else None
        self.default_pstyle = next((sid for sid, s in self.styles.items() if s.get(q("w:type")) == "paragraph" and s.get(q("w:default")) in ("1", "true")), None)

    def chain(self, sid):
        seen = []
        while sid and sid in self.styles and sid not in seen:
            seen.append(sid)
            b = self.styles[sid].find(q("w:basedOn"))
            sid = b.get(q("w:val")) if b is not None else None
        return [self.styles[s] for s in seen]

    def _first(self, levels, child, attr):
        """levels: list of (source_name, [elements holding rPr/pPr])."""
        for source, els in levels:
            for el in els:
                node = el.find(child) if el is not None else None
                if node is not None:
                    v = node.get(q("w:" + attr)) if attr else "present"
                    if v is not None:
                        return v, source
        return None, "default"

    def run_levels(self, r, psid):
        rpr = r.find(q("w:rPr"))
        rs = rpr.find(q("w:rStyle")) if rpr is not None else None
        char_chain = self.chain(rs.get(q("w:val"))) if rs is not None else []
        p_chain = self.chain(psid or self.default_pstyle)
        return [("direct", [rpr]), ("rstyle", [c.find(q("w:rPr")) for c in char_chain]),
                ("pstyle", [c.find(q("w:rPr")) for c in p_chain]), ("default", [self.rdef])]

    def para_levels(self, p, psid):
        ppr = p.find(q("w:pPr"))
        p_chain = self.chain(psid or self.default_pstyle)
        return [("direct", [ppr]), ("pstyle", [c.find(q("w:pPr")) for c in p_chain]), ("default", [self.pdef])]

    def font(self, levels, attr):
        """Effective font for one script slot: ('name'|'theme:x', source)."""
        for source, els in levels:
            for el in els:
                rf = el.find(q("w:rFonts")) if el is not None else None
                if rf is None:
                    continue
                v = rf.get(q("w:" + attr))
                if v:
                    return v, source
                t = rf.get(q("w:" + attr + "Theme" if attr != "cs" else "w:cstheme"))
                if t:
                    return "theme:" + t, source
        return None, "default"

    def size(self, levels):
        v, src = self._first(levels, q("w:sz"), "val")
        return (int(v) if v and v.isdigit() else None), src

    def bold(self, levels):
        v, src = self._first(levels, q("w:b"), "val")
        if v is None:
            node_present, _ = self._first(levels, q("w:b"), None)
            return (node_present is not None), src
        return v.lower() not in ("0", "false", "off"), src


def _twips(length):
    return None if length is None else round(length / 635)


def validate(original: Path, output: Path, profile: dict) -> list:
    checks = []
    page, body, heads = profile["page"], profile["body"], profile["headings"]

    # 1. valid package
    try:
        with zipfile.ZipFile(output) as zf:
            bad = zf.testzip()
        doc = Document(str(output))
        checks.append(_check("opens", "Output reopens as a valid DOCX", bad is None, "ZIP integrity and python-docx load OK" if bad is None else "Corrupt entry: %s" % bad))
        if bad is not None:
            return checks
    except Exception as e:
        return checks + [_check("opens", "Output reopens as a valid DOCX", False, "Could not open output (%s)" % type(e).__name__)]

    # 2. text unchanged
    fo, fn = fingerprints(original), fingerprints(output)
    missing = [k for k in fo if k not in fn]
    changed = [k for k in fo if k in fn and fo[k] != fn[k]]
    ok = not missing and not changed
    checks.append(_check("text", "Text is identical before and after", ok,
                         "%d part(s) compared (body, headers, footers, notes); %d paragraph(s) in the main body" % (len(fo), fo.get("word/document.xml", (0, 0))[1]) if ok else "Differences in: %s" % ", ".join(sorted(missing + changed))))

    # 3. structure preserved
    so, sn = structure_counts(original), structure_counts(output)
    diffs = [k for k in so if so[k] != sn.get(k)]
    labels = {"tables": "tables", "inline_images": "inline images", "hyperlinks": "hyperlinks", "sections": "sections", "equations": "equations",
              "list_paragraphs": "list paragraphs", "bold_markers": "bold runs", "italic_markers": "italic runs", "media_files": "image files"}
    checks.append(_check("structure", "Tables, lists, images, links, equations, emphasis, sections, headers/footers preserved", not diffs,
                         "Counts match: %d table(s), %d image(s), %d hyperlink(s), %d section(s), %d equation(s), %d list paragraph(s)" % (so["tables"], so["inline_images"], so["hyperlinks"], so["sections"], so["equations"], so["list_paragraphs"]) if not diffs
                         else "Changed: " + ", ".join(labels.get(d, d) for d in diffs)))

    # 4. page setup
    pw, ph, m = _twips(Mm_(page["width_mm"])), _twips(Mm_(page["height_mm"])), round(page["margin_in"] * 1440)
    bad_secs = 0
    secs = list(doc.sections)
    for s in secs:
        w, h = _twips(s.page_width), _twips(s.page_height)
        exp = (ph, pw) if (w is not None and h is not None and w > h) else (pw, ph)
        margins = [_twips(s.top_margin), _twips(s.bottom_margin), _twips(s.left_margin), _twips(s.right_margin)]
        if w is None or h is None or abs(w - exp[0]) > TOL_TWIPS or abs(h - exp[1]) > TOL_TWIPS or any(x is None or abs(x - m) > TOL_TWIPS for x in margins):
            bad_secs += 1
    checks.append(_check("page", "Page size and margins match the profile in every section", bad_secs == 0 and secs,
                         "%d section(s): %s, %g in margins" % (len(secs), page["size"], page["margin_in"]) if bad_secs == 0 else "%d section(s) do not match" % bad_secs))

    # 5. style-level + effective paragraph/run formatting
    cas = Cascade(doc)
    sizes_half = {"body": int(body["size_pt"] * 2), "Heading 1": int(heads["Heading 1"]["size_pt"] * 2), "Heading 2": int(heads["Heading 2"]["size_pt"] * 2)}
    fails = {"font": 0, "size": 0, "line": 0, "after": 0, "bold": 0}
    char_style_overrides = direct_bold_off = checked_p = checked_r = skipped_symbol = 0
    for para in doc.paragraphs:
        st = para.style
        kind = classify(para._p, st.style_id if st is not None else "", st.name if st is not None else "")
        if kind.startswith("skip"):
            continue
        sid = st.style_id if st is not None else None
        checked_p += 1
        if kind == "body":
            pl = cas.para_levels(para._p, sid)
            line, _ = cas._first(pl, q("w:spacing"), "line")
            rule, _ = cas._first(pl, q("w:spacing"), "lineRule")
            after, _ = cas._first(pl, q("w:spacing"), "after")
            auto_after, _ = cas._first(pl, q("w:spacing"), "afterAutospacing")
            if line != str(round(body["line_spacing"] * 240)) or (rule or "auto") != "auto":
                fails["line"] += 1
            if after != str(round(body["space_after_pt"] * 20)) or (auto_after or "0").lower() in ("1", "true", "on"):
                fails["after"] += 1
        for r in iter_runs(para._p):
            if r.find(q("w:t")) is None or is_symbol_run(r):
                skipped_symbol += is_symbol_run(r)
                continue
            checked_r += 1
            lv = cas.run_levels(r, sid)
            font_bad = size_bad = False
            src_char = False
            for attr in ("ascii", "hAnsi"):
                f, src = cas.font(lv, attr)
                if f != body["font"]:
                    if src == "rstyle":
                        src_char = True
                    else:
                        font_bad = True
            sz, src = cas.size(lv)
            if sz != sizes_half[kind]:
                if src == "rstyle":
                    src_char = True
                else:
                    size_bad = True
            fails["font"] += font_bad
            fails["size"] += size_bad
            char_style_overrides += src_char
            if kind != "body" and heads[kind].get("bold"):
                b, src = cas.bold(lv)
                if not b:
                    if src == "direct":
                        direct_bold_off += 1
                    elif src != "rstyle":
                        fails["bold"] += 1
    for key, label, detail in (("font", "Body and heading text uses %s" % body["font"], ""), ("size", "Font sizes match the profile (body %g pt, Heading 1 %g pt, Heading 2 %g pt)" % (body["size_pt"], heads["Heading 1"]["size_pt"], heads["Heading 2"]["size_pt"]), ""),
                               ("line", "Body line spacing is %g" % body["line_spacing"], ""), ("after", "Body spacing after is %g pt" % body["space_after_pt"], ""),
                               ("bold", "Headings are bold", "")):
        checks.append(_check("fmt_" + key, label, fails[key] == 0, ("Verified on %d paragraph(s) / %d run(s) with effective values" % (checked_p, checked_r)) if fails[key] == 0 else "%d mismatch(es) after applying" % fails[key]))

    # 6. style definitions themselves
    for name, sid, size, bold in (("Normal", None, sizes_half["body"], None), ("Heading 1", "Heading1", sizes_half["Heading 1"], True), ("Heading 2", "Heading2", sizes_half["Heading 2"], True)):
        st = cas.styles.get(sid or cas.default_pstyle)
        if st is None:
            continue  # reported as a warning by the formatter
        lv = [("pstyle", [c.find(q("w:rPr")) for c in cas.chain(st.get(q("w:styleId")))])]
        f = [cas.font(lv, a)[0] for a in ("ascii", "hAnsi", "cs", "eastAsia")]
        sz = cas.size(lv)[0]
        ok = all(x == body["font"] for x in f) and sz == size and (bold is None or cas.bold(lv)[0] == bold)
        checks.append(_check("style_" + name.replace(" ", "").lower(), "%s style definition matches (%s%s)" % (name, "%s, %g pt" % (body["font"], size / 2), ", bold" if bold else ""), ok, "" if ok else "Style still resolves to %s / %s half-points" % (f[0], sz)))

    notes = []
    if char_style_overrides:
        notes.append("%d run(s) keep font or size from a character style and were left as they are" % char_style_overrides)
    if direct_bold_off:
        notes.append("%d heading run(s) have bold switched off directly and were left as they are" % direct_bold_off)
    if skipped_symbol:
        notes.append("%d symbol/math-font run(s) kept their font" % skipped_symbol)
    for n in notes:
        checks.append(_check("note", n, True, "", status="warning"))
    return checks


def Mm_(mm):
    from docx.shared import Mm
    return Mm(mm)
