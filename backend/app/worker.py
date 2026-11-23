"""Runs in a child process with a memory cap. Writes a JSON report; never prints document content."""
import json
import sys
from pathlib import Path


def _limit_memory(mb: int):
    try:
        import resource
        limit = mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except Exception:
        pass  # not available on this platform; the parent still enforces a timeout


def main(argv):
    src, dst, profile_id, report_path, mem_mb = Path(argv[1]), Path(argv[2]), argv[3], Path(argv[4]), int(argv[5])
    _limit_memory(mem_mb)
    from .engine.formatter import format_docx
    from .engine.report import build_report
    from .engine.validator import validate
    from .profiles import load_profile
    profile = load_profile(profile_id)
    try:
        fmt = format_docx(src, dst, profile)
        checks = validate(src, dst, profile)
        report = build_report(profile, fmt, checks)
    except MemoryError:
        report = {"status": "error", "code": "out_of_memory", "message": "The document is too complex to process within this server's memory limit."}
    except Exception as e:  # message deliberately omitted: it could contain document text
        report = {"status": "error", "code": "processing_failed", "message": "The document could not be processed (%s)." % type(e).__name__}
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
