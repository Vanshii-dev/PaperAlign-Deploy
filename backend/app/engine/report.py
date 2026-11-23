def build_report(profile: dict, fmt, checks: list) -> dict:
    failed = [c for c in checks if c["status"] == "failed"]
    passed = [c for c in checks if c["status"] == "passed"]
    warn = [c for c in checks if c["status"] == "warning"]
    issues = list(fmt.issues if fmt else []) + [{"severity": "warning", "code": "check_note", "message": c["label"]} for c in warn]
    return {
        "status": "success" if not failed and checks else "failed",
        "profile": {"id": profile["id"], "label": profile["label"], "version": profile["version"], "disclaimer": profile["disclaimer"]},
        "summary": {"checks_passed": len(passed), "checks_failed": len(failed), "warnings": len(issues)},
        "applied": fmt.applied if fmt else [],
        "checks": [c for c in checks if c["status"] != "warning"],
        "issues": issues,
        "not_modified": fmt.not_modified if fmt else [],
        "stats": fmt.stats if fmt else {},
    }
