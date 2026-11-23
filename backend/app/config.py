"""Runtime settings, read from the environment on every call so tests can override them."""
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    max_upload_bytes: int
    max_uncompressed_bytes: int
    max_zip_entries: int
    max_compression_ratio: int
    worker_timeout_s: int
    worker_memory_mb: int
    max_concurrent_jobs: int
    rate_limit_per_min: int
    trust_proxy_hops: int
    allowed_origins: tuple
    allowed_origin_regex: str
    tmp_root: Path


def get_settings() -> Settings:
    origins = tuple(o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip())
    return Settings(
        max_upload_bytes=_int("MAX_UPLOAD_MB", 10) * 1024 * 1024,
        max_uncompressed_bytes=_int("MAX_UNCOMPRESSED_MB", 150) * 1024 * 1024,
        max_zip_entries=_int("MAX_ZIP_ENTRIES", 3000),
        max_compression_ratio=_int("MAX_COMPRESSION_RATIO", 100),
        worker_timeout_s=_int("WORKER_TIMEOUT_S", 60),
        worker_memory_mb=_int("WORKER_MEMORY_MB", 768),
        max_concurrent_jobs=_int("MAX_CONCURRENT_JOBS", 1),
        rate_limit_per_min=_int("RATE_LIMIT_PER_MIN", 10),
        trust_proxy_hops=_int("TRUST_PROXY_HOPS", 1),
        allowed_origins=origins,
        allowed_origin_regex=os.environ.get("ALLOWED_ORIGIN_REGEX", ""),
        tmp_root=Path(os.environ.get("PAPERALIGN_TMP", str(Path(tempfile.gettempdir()) / "paperalign"))),
    )
