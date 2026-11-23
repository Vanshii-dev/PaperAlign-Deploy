"""Upload validation. Uploads are private, untrusted files: nothing is trusted from the browser."""
import zipfile
from pathlib import Path, PurePosixPath

from lxml import etree

from .config import Settings
from .engine.common import parse_xml


class UploadRejected(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


MAIN_CT = "wordprocessingml.document.main+xml"


def validate_docx_file(path: Path, s: Settings) -> None:
    size = path.stat().st_size
    if size == 0:
        raise UploadRejected("empty_file", "The file is empty.")
    if size > s.max_upload_bytes:
        raise UploadRejected("file_too_large", "The file is larger than the %d MB limit." % (s.max_upload_bytes // 1048576), 413)
    with open(path, "rb") as f:
        head = f.read(8)
    if head[:4] == b"\xd0\xcf\x11\xe0":
        raise UploadRejected("unsupported_format", "This looks like a legacy .doc or a password-protected file. Save it as an unprotected .docx in Word and try again.", 415)
    if head[:2] != b"PK":
        raise UploadRejected("not_docx", "This file is not a valid .docx document.", 415)
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise UploadRejected("corrupt_file", "The file is damaged and could not be opened as a .docx.", 422)
    with zf:
        infos = zf.infolist()
        if len(infos) > s.max_zip_entries:
            raise UploadRejected("too_many_parts", "The document contains too many internal parts.", 422)
        total = 0
        names = set()
        for i in infos:
            n = i.filename
            p = PurePosixPath(n)
            if n.startswith(("/", "\\")) or ".." in p.parts or "\\" in n or ":" in n.split("/")[0]:
                raise UploadRejected("unsafe_path", "The document contains an unsafe internal path.", 422)
            if i.flag_bits & 0x1:
                raise UploadRejected("encrypted", "Encrypted files are not supported.", 415)
            total += i.file_size
            if i.file_size > 1048576 and i.compress_size and i.file_size / i.compress_size > s.max_compression_ratio:
                raise UploadRejected("decompression_limit", "The document's contents expand too much to be processed safely.", 422)
            names.add(n)
        if total > s.max_uncompressed_bytes:
            raise UploadRejected("decompression_limit", "The document's contents expand too much to be processed safely.", 422)
        for req in ("[Content_Types].xml", "word/document.xml"):
            if req not in names:
                raise UploadRejected("not_docx", "This file is not a Word document (missing %s)." % req, 415)
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            raise UploadRejected("macros", "Documents with macros are not supported. Save a macro-free .docx and try again.", 415)
        try:
            ct = zf.read("[Content_Types].xml")
            if b"macroEnabled" in ct:
                raise UploadRejected("macros", "Macro-enabled documents are not supported. Save a macro-free .docx and try again.", 415)
            if MAIN_CT.encode() not in ct:
                raise UploadRejected("unsupported_format", "This is not a standard Word document (templates are not supported).", 415)
            for part in ("word/document.xml", "word/styles.xml"):
                if part in names:
                    data = zf.read(part)
                    if b"<!ENTITY" in data[:4096] or b"<!DOCTYPE" in data[:1024].upper():
                        raise UploadRejected("unsafe_xml", "The document contains unsupported XML declarations.", 422)
                    parse_xml(data)
            if zf.testzip() is not None:
                raise UploadRejected("corrupt_file", "The file is damaged and could not be opened as a .docx.", 422)
        except UploadRejected:
            raise
        except (zipfile.BadZipFile, etree.XMLSyntaxError, KeyError, RuntimeError, EOFError):
            raise UploadRejected("corrupt_file", "The file is damaged and could not be opened as a .docx.", 422)
