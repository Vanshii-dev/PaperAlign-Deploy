"""Generates the DOCX fixtures used by the tests, so they are reproducible and reviewable."""
import struct
import zipfile
import zlib
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Inches, Pt


def png_bytes(w=40, h=30) -> bytes:
    raw = b"".join(b"\x00" + bytes([(x * 6) % 256, (y * 8) % 256, 160]) * w for y in range(h) for x in [0])
    raw = b"".join(b"\x00" + b"".join(bytes([(x * 6) % 256, (y * 8) % 256, 160]) for x in range(w)) for y in range(h))
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def add_hyperlink(paragraph, text, url):
    rid = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    h = OxmlElement("w:hyperlink")
    h.set(qn("r:id"), rid)
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = text
    r.append(t)
    h.append(r)
    paragraph._p.append(h)


def build_plain(path: Path):
    d = Document()
    d.add_heading("Introduction", level=1)
    p = d.add_paragraph()
    r = p.add_run("This paper studies ")
    r.font.name, r.font.size = "Arial", Pt(10)          # direct formatting that must be normalised
    b = p.add_run("alignment")
    b.bold = True
    p.add_run(" of ")
    i = p.add_run("manuscripts")
    i.italic = True
    p.add_run(" in a reproducible way.")
    p.paragraph_format.space_after = Pt(0)             # direct spacing override
    p.paragraph_format.line_spacing = 1.0
    d.add_paragraph("A second body paragraph with plain text.")
    d.add_heading("Background", level=2)
    d.add_paragraph("Unicode text: naïve café – ±5% (p < 0.05).")
    d.save(path)


def build_rich(path: Path):
    d = Document()
    d.add_heading("Methods", level=1)
    p = d.add_paragraph("Body text with a link to ")
    add_hyperlink(p, "the dataset", "https://example.org/data")
    p.add_run(" and a ")
    p.add_run("bold").bold = True
    d.add_paragraph("First bullet", style="List Bullet")
    d.add_paragraph("Second bullet", style="List Bullet")
    np_ = d.add_paragraph("Numbered via direct numbering on a Normal paragraph")
    ppr = np_._p.get_or_add_pPr()
    ppr.append(parse_xml('<w:numPr %s><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' % nsdecls("w")))
    t = d.add_table(rows=2, cols=2)
    t.style = "Table Grid"
    for r_, row in enumerate(t.rows):
        for c_, cell in enumerate(row.cells):
            cell.text = "R%dC%d" % (r_, c_)
    img = path.with_suffix(".png")
    img.write_bytes(png_bytes())
    d.add_paragraph().add_run().add_picture(str(img), width=Inches(1.2))
    img.unlink()
    d.add_paragraph("Title-like paragraph", style="Title")
    eq = parse_xml('<m:oMathPara %s><m:oMath><m:r><m:t>E=mc2</m:t></m:r></m:oMath></m:oMathPara>' % nsdecls("m"))
    ep = d.add_paragraph()
    ep._p.append(eq)
    sec = d.sections[0]
    sec.header.paragraphs[0].text = "Running header text"
    sec.footer.paragraphs[0].text = "Footer text"
    d.add_heading("Results", level=2)
    d.add_paragraph("Final body paragraph in section one.")
    new = d.add_section(WD_SECTION.NEW_PAGE)
    new.orientation = WD_ORIENT.LANDSCAPE
    new.page_width, new.page_height = new.page_height, new.page_width
    d.add_paragraph("Landscape section paragraph.")
    d.save(path)


def _zip_with(path: Path, base: Path, extra: dict):
    with zipfile.ZipFile(base) as zin, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for i in zin.infolist():
            zout.writestr(i.filename, zin.read(i.filename))
        for name, data in extra.items():
            zout.writestr(name, data)


def build_all(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    f = {k: out / v for k, v in {"plain": "plain.docx", "rich": "rich.docx", "corrupt": "corrupt.docx", "fake": "fake.docx", "bomb": "bomb.docx", "macro": "macro.docx", "legacy": "legacy.docx"}.items()}
    build_plain(f["plain"])
    build_rich(f["rich"])
    f["corrupt"].write_bytes(f["plain"].read_bytes()[:700])
    f["fake"].write_text("This is just text, not a docx.")
    _zip_with(f["bomb"], f["plain"], {"word/media/bomb.bin": b"\x00" * (30 * 1024 * 1024)})
    _zip_with(f["macro"], f["plain"], {"word/vbaProject.bin": b"fake"})
    f["legacy"].write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600)
    return f
