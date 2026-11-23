import base64
import io
import zipfile

from docx import Document

from app.engine.common import fingerprints, structure_counts
from app.engine.formatter import format_docx
from app.engine.validator import validate
from app.profiles import load_profile
from tests.conftest import post

PROFILE = load_profile("basic-academic-layout")


def decode(resp, tmp_path, name="out.docx"):
    p = tmp_path / name
    p.write_bytes(base64.b64decode(resp.json()["docx_base64"]))
    return p


def test_1_e2e_upload_to_download(client, fx, tmp_path):
    r = post(client, fx["rich"])
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "success" and j["filename"] == "rich_formatted.docx"
    assert j["report"]["summary"]["checks_failed"] == 0 and j["report"]["summary"]["checks_passed"] >= 8
    out = decode(r, tmp_path)
    assert len(Document(str(out)).paragraphs) > 5          # 5. reopens
    assert "pdf" not in j["report"]["profile"]["disclaimer"].lower()


def test_2_settings_applied(client, fx, tmp_path):
    out = decode(post(client, fx["plain"]), tmp_path)
    d = Document(str(out))
    s = d.sections[0]
    assert abs(s.page_width.mm - 210) < 0.5 and abs(s.page_height.mm - 297) < 0.5
    assert all(abs(m.inches - 1) < 0.01 for m in (s.top_margin, s.bottom_margin, s.left_margin, s.right_margin))
    n = d.styles["Normal"]
    assert n.font.name == "Times New Roman" and n.font.size.pt == 12 and n.paragraph_format.line_spacing == 1.5 and n.paragraph_format.space_after.pt == 6
    h1, h2 = d.styles["Heading 1"], d.styles["Heading 2"]
    assert (h1.font.size.pt, h1.font.bold, h1.font.name) == (14, True, "Times New Roman")
    assert (h2.font.size.pt, h2.font.bold) == (12, True)
    # the direct overrides in the fixture were cleared
    body = d.paragraphs[1]
    assert all(r.font.name is None and r.font.size is None for r in body.runs)
    assert body.paragraph_format.line_spacing is None and body.paragraph_format.space_after is None
    # theme fonts are gone from the heading style
    rf = h1.element.rPr.rFonts
    assert not [a for a in rf.attrib if "Theme" in a or "theme" in a]


def test_3_text_identical(fx, tmp_path):
    out = tmp_path / "o.docx"
    format_docx(fx["rich"], out, PROFILE)
    assert fingerprints(fx["rich"]) == fingerprints(out)
    assert [p.text for p in Document(str(fx["rich"])).paragraphs] == [p.text for p in Document(str(out)).paragraphs]


def test_4_preservation(fx, tmp_path):
    out = tmp_path / "o.docx"
    format_docx(fx["rich"], out, PROFILE)
    a, b = structure_counts(fx["rich"]), structure_counts(out)
    assert a == b
    assert a["tables"] == 1 and a["inline_images"] == 1 and a["hyperlinks"] == 1 and a["equations"] == 1 and a["sections"] == 2 and a["list_paragraphs"] == 1 and a["header_footer_parts"] >= 2
    d = Document(str(out))
    assert sum(1 for p in d.paragraphs if p.style.name == "List Bullet") == 2
    assert d.sections[0].header.paragraphs[0].text == "Running header text"
    assert d.sections[1].page_width > d.sections[1].page_height         # landscape kept
    assert abs(d.sections[1].page_width.mm - 297) < 0.5


def test_5_out_of_scope_untouched(fx, tmp_path):
    out = tmp_path / "o.docx"
    res = format_docx(fx["rich"], out, PROFILE)
    labels = {n["label"]: n["count"] for n in res.not_modified}
    assert labels["Table paragraphs"] == 4 and labels["Display equations"] == 1
    assert labels["List paragraphs (body style with numbering)"] == 1


def test_validator_catches_damage(fx, tmp_path):
    good = tmp_path / "good.docx"
    format_docx(fx["rich"], good, PROFILE)
    assert all(c["status"] != "failed" for c in validate(fx["rich"], good, PROFILE))
    # text changed
    bad = tmp_path / "bad.docx"
    with zipfile.ZipFile(good) as zi, zipfile.ZipFile(bad, "w") as zo:
        for i in zi.infolist():
            data = zi.read(i.filename)
            if i.filename == "word/document.xml":
                data = data.replace(b"Final body paragraph", b"Altered body paragraph")
            if i.filename.startswith("word/media/"):
                data = data + b"\x00"          # image bytes changed
            zo.writestr(i.filename, data)
    failed = {c["id"] for c in validate(fx["rich"], bad, PROFILE) if c["status"] == "failed"}
    assert "text" in failed and "structure" in failed
    # unformatted file fails the formatting checks
    unformatted = {c["id"] for c in validate(fx["plain"], fx["plain"], PROFILE) if c["status"] == "failed"}
    assert {"page", "fmt_font", "fmt_line"} <= unformatted


def test_9_consistent_and_idempotent(fx, tmp_path):
    a, b, c = tmp_path / "a.docx", tmp_path / "b.docx", tmp_path / "c.docx"
    format_docx(fx["rich"], a, PROFILE)
    format_docx(fx["rich"], b, PROFILE)
    format_docx(a, c, PROFILE)
    def xml(p, n):
        with zipfile.ZipFile(p) as z:
            return z.read(n)
    for part in ("word/document.xml", "word/styles.xml"):
        assert xml(a, part) == xml(b, part) == xml(c, part)
