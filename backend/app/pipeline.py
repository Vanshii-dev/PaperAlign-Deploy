"""Upload -> private temp dir -> validate -> worker subprocess -> result. Always cleans up."""
import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .security import UploadRejected, validate_docx_file

BACKEND_DIR = Path(__file__).resolve().parents[1]


@dataclass
class Result:
    ok: bool
    report: dict
    filename: str = ""
    docx_b64: str = ""


def make_workdir(s: Settings) -> Path:
    s.tmp_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    d = Path(os.path.join(s.tmp_root, "job_" + secrets.token_hex(12)))
    d.mkdir(mode=0o700)
    return d


def sweep_stale(s: Settings, max_age_s: int = 900) -> None:
    if not s.tmp_root.exists():
        return
    now = time.time()
    for d in s.tmp_root.glob("job_*"):
        try:
            if now - d.stat().st_mtime > max_age_s:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def safe_output_name(original: str) -> str:
    stem = Path(original or "document").stem
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "", stem).strip(" .-_")[:80] or "document"
    return stem.replace(" ", "_") + "_formatted.docx"


def run_worker(src: Path, dst: Path, profile_id: str, report_path: Path, s: Settings) -> None:
    try:
        subprocess.run([sys.executable, "-m", "app.worker", str(src), str(dst), profile_id, str(report_path), str(s.worker_memory_mb)],
                       cwd=str(BACKEND_DIR), timeout=s.worker_timeout_s, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise UploadRejected("timeout", "Processing took too long and was stopped. Try a smaller or simpler document.", 504)
    except subprocess.CalledProcessError:
        raise UploadRejected("processing_failed", "The document could not be processed. It may be too complex or too large for this server.", 500)


def process(upload: Path, original_name: str, profile_id: str, s: Settings, workdir: Path) -> Result:
    """`upload` is already inside `workdir`. The caller removes workdir in a finally block."""
    validate_docx_file(upload, s)
    out, rep = workdir / "output.docx", workdir / "report.json"
    run_worker(upload, out, profile_id, rep, s)
    if not rep.exists():
        raise UploadRejected("processing_failed", "The document could not be processed.", 500)
    report = json.loads(rep.read_text(encoding="utf-8"))
    if report.get("status") == "error":
        raise UploadRejected(report["code"], report["message"], 422 if report["code"] != "out_of_memory" else 507)
    if report["status"] != "success" or not out.exists():
        return Result(False, report)
    return Result(True, report, safe_output_name(original_name), base64.b64encode(out.read_bytes()).decode("ascii"))
