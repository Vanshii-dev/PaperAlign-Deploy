import os
import zipfile

import pytest

from app import pipeline
from app.security import UploadRejected
from tests.conftest import post


def leftovers(root):
    return list(root.glob("job_*")) if root.exists() else []


@pytest.mark.parametrize("key,status,code", [("corrupt", 422, "corrupt_file"), ("fake", 415, "not_docx"), ("bomb", 422, "decompression_limit"), ("macro", 415, "macros"), ("legacy", 415, "unsupported_format")])
def test_6_rejects_bad_files(client, fx, workroot, key, status, code):
    r = post(client, fx[key], name=key + ".docx")
    assert r.status_code == status and r.json()["code"] == code and r.json()["status"] == "error"
    assert "docx_base64" not in r.json()
    assert leftovers(workroot) == []


def test_6_wrong_extension_and_empty(client, fx, tmp_path, workroot):
    assert post(client, fx["plain"], name="paper.pdf").json()["code"] == "wrong_extension"
    e = tmp_path / "empty.docx"
    e.write_bytes(b"")
    assert post(client, e).json()["code"] == "empty_file"
    assert leftovers(workroot) == []


def test_6_oversize_boundary(client, fx, tmp_path, monkeypatch, workroot):
    size = fx["plain"].stat().st_size
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    big = tmp_path / "big.docx"
    big.write_bytes(fx["plain"].read_bytes() + b"\x00" * (1024 * 1024 + 1 - size))
    r = post(client, big)
    assert r.status_code == 413 and r.json()["code"] == "file_too_large"
    huge = tmp_path / "huge.docx"
    huge.write_bytes(b"PK" + b"\x00" * (3 * 1024 * 1024))
    assert post(client, huge).status_code == 413           # stopped by the body limiter
    assert leftovers(workroot) == []


def test_7_cleanup_after_success_and_failure(client, fx, workroot, monkeypatch):
    assert post(client, fx["plain"]).status_code == 200
    assert leftovers(workroot) == []
    def boom(*a, **k):
        raise RuntimeError("secret manuscript text")
    monkeypatch.setattr(pipeline, "process", boom)
    r = post(client, fx["plain"])
    assert r.status_code == 500 and "secret" not in r.text
    assert leftovers(workroot) == []


def test_8_no_false_success_on_validation_failure(client, fx, monkeypatch, workroot):
    real = pipeline.run_worker
    def sabotage(src, dst, pid, rep, s):
        real(src, dst, pid, rep, s)
        with zipfile.ZipFile(dst) as zi:
            items = {i.filename: zi.read(i.filename) for i in zi.infolist()}
        import json
        r = json.loads(rep.read_text())
        r["status"] = "failed"
        r["summary"]["checks_failed"] = 1
        rep.write_text(json.dumps(r))
    monkeypatch.setattr(pipeline, "run_worker", sabotage)
    r = post(client, fx["plain"])
    assert r.status_code == 422 and r.json()["status"] == "failed" and "docx_base64" not in r.json()
    assert leftovers(workroot) == []


def test_8_worker_crash_is_an_error(client, fx, monkeypatch):
    def crash(*a, **k):
        raise UploadRejected("processing_failed", "The document could not be processed.", 500)
    monkeypatch.setattr(pipeline, "run_worker", crash)
    r = post(client, fx["plain"])
    assert r.status_code == 500 and r.json()["status"] == "error"


def test_unknown_profile(client, fx):
    assert post(client, fx["plain"], profile_id="nope").json()["code"] == "unknown_profile"


def test_cors(client, fx):
    ok = client.options("/api/format", headers={"Origin": "https://paperalign.example", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "https://paperalign.example"
    bad = client.options("/api/format", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers
    r = client.get("/api/health")
    assert r.headers["cache-control"] == "no-store" and r.json()["max_upload_mb"] == 10


def test_no_document_text_in_logs(client, fx, caplog):
    caplog.set_level("DEBUG")
    post(client, fx["plain"])
    post(client, fx["corrupt"])
    assert "alignment" not in caplog.text and "manuscripts" not in caplog.text


def test_rate_limit(client, fx, monkeypatch):
    from app import main
    main._hits.clear()
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "2")
    codes = [post(client, fx["fake"]).status_code for _ in range(3)]
    assert codes[-1] == 429
    main._hits.clear()
