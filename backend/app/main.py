import asyncio
import logging
import shutil
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import pipeline
from .config import get_settings
from .profiles import UnknownProfile, list_profiles, load_profile
from .security import UploadRejected

log = logging.getLogger("paperalign")  # only codes and sizes are ever logged, never document text
BODY_SLACK = 256 * 1024  # multipart overhead allowed on top of the file limit


class BodyTooLarge(Exception):
    pass


class BodyLimit:
    """Stops reading oversize request bodies early (before they are spooled to disk)."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        limit = get_settings().max_upload_bytes + BODY_SLACK
        cl = dict(scope["headers"]).get(b"content-length")
        started = False

        async def send_w(m):
            nonlocal started
            started = started or m["type"] == "http.response.start"
            await send(m)

        async def too_large():
            body = b'{"status":"error","code":"file_too_large","message":"The file is larger than the upload limit."}'
            await send_w({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send_w({"type": "http.response.body", "body": body})

        if cl and cl.isdigit() and int(cl) > limit:
            return await too_large()
        got = 0

        async def recv():
            nonlocal got
            m = await receive()
            if m["type"] == "http.request":
                got += len(m.get("body", b""))
                if got > limit:
                    raise BodyTooLarge()
            return m

        try:
            await self.app(scope, recv, send_w)
        except BodyTooLarge:
            if not started:
                await too_large()


_hits = defaultdict(deque)
_active = 0


def client_ip(request: Request, hops: int) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    if parts and hops > 0:
        return parts[-hops] if len(parts) >= hops else parts[0]
    return request.client.host if request.client else "unknown"


def err(status: int, code: str, message: str, headers=None):
    return JSONResponse({"status": "error", "code": code, "message": message}, status_code=status, headers=headers)


@asynccontextmanager
async def lifespan(app):
    pipeline.sweep_stale(get_settings(), 0)
    yield


app = FastAPI(title="PaperAlign API", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
s0 = get_settings()
app.add_middleware(CORSMiddleware, allow_origins=list(s0.allowed_origins), allow_origin_regex=s0.allowed_origin_regex or None,
                   allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"], max_age=600)
app.add_middleware(BodyLimit)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@app.get("/api/health")
async def health():
    return {"status": "ok", "max_upload_mb": get_settings().max_upload_bytes // 1048576}


@app.get("/api/profiles")
async def profiles():
    return {"profiles": list_profiles()}


@app.post("/api/format")
async def format_document(request: Request, file: UploadFile = File(...), profile_id: str = Form("basic-academic-layout")):
    global _active
    s = get_settings()
    ip = client_ip(request, s.trust_proxy_hops)
    now = time.monotonic()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= s.rate_limit_per_min:
        return err(429, "rate_limited", "Too many requests. Please wait a minute and try again.", {"Retry-After": "60"})
    q.append(now)

    try:
        load_profile(profile_id)
    except UnknownProfile:
        return err(400, "unknown_profile", "Unknown formatting profile.")
    name = file.filename or ""
    if not name.lower().endswith(".docx"):
        return err(415, "wrong_extension", "Only .docx files are supported.")
    if _active >= s.max_concurrent_jobs:
        return err(503, "busy", "The server is busy with another document. Please try again in a moment.", {"Retry-After": "10"})

    _active += 1
    workdir = None
    try:
        workdir = pipeline.make_workdir(s)
        upload = workdir / "input.docx"
        size = 0
        with open(upload, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > s.max_upload_bytes:
                    raise UploadRejected("file_too_large", "The file is larger than the %d MB limit." % (s.max_upload_bytes // 1048576), 413)
                out.write(chunk)
        result = await asyncio.to_thread(pipeline.process, upload, name, profile_id, s, workdir)
        if not result.ok:
            return JSONResponse({"status": "failed", "code": "validation_failed", "message": "The formatted file failed verification, so no download is offered.", "report": result.report}, status_code=422)
        return JSONResponse({"status": "success", "filename": result.filename, "report": result.report, "docx_base64": result.docx_b64})
    except UploadRejected as e:
        log.info("rejected code=%s", e.code)
        return err(e.status, e.code, e.message)
    except Exception as e:
        log.error("unexpected error type=%s", type(e).__name__)
        return err(500, "server_error", "Something went wrong while processing the document. Nothing was stored.")
    finally:
        _active -= 1
        await file.close()
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)
