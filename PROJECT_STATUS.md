# PaperAlign Project Status

**Checked:** 2026-11-23  
**Repository:** PaperAlign  
**Overall:** The backend and frontend code pass their automated checks, but the local browser app is not currently fully started/configured.

## Executive summary

The backend is responding locally at `http://127.0.0.1:8000`: its health endpoint returns `status: ok`, and it lists one formatting profile. The frontend is not responding at `http://127.0.0.1:5173`, and `frontend/.env` does not exist. These facts explain why the local website may report that its formatting server is unavailable: the UI server is stopped, and if started without an API URL it will not be directed to the backend.

The Render/Vercel deployment was not checked. No deployed URLs or browser error details were provided, so this report cannot determine whether a live deployment has a separate configuration problem.

## Current checks

| Area | Result | Evidence |
|---|---|---|
| Local API health | **Pass** | `GET http://127.0.0.1:8000/api/health` returned `status: ok`, upload limit 10 MB |
| Formatting profiles | **Pass** | `GET http://127.0.0.1:8000/api/profiles` returned one profile |
| Local frontend | **Not running** | Request to `http://127.0.0.1:5173` could not connect |
| Frontend API environment | **Not configured** | `frontend/.env` is missing; the example file sets `VITE_API_BASE_URL=http://localhost:8000` |
| Backend tests | **Pass** | 21 tests passed; one Starlette/httpx deprecation warning |
| Frontend tests | **Pass** | 5 tests passed |
| Frontend production build | **Pass** | TypeScript check and Vite production build succeeded |
| Clean frontend install | **Pass** | `npm ci` completed with the current lockfile |
| Deployed app / real browser upload | **Not verified** | No deployment smoke test or shared browser details available |

## Get the local app working

Use two separate **Command Prompt** windows and leave both running.

### 1. Backend

```cmd
cd C:\Users\Acer\OneDrive\Desktop\PaperAlign\paperalign\backend
set ALLOWED_ORIGINS=http://localhost:5173
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Confirm the API in a browser at `http://localhost:8000/api/health`. It should return JSON containing `"status":"ok"`.

### 2. Configure and start the frontend

```cmd
cd C:\Users\Acer\OneDrive\Desktop\PaperAlign\paperalign\frontend
copy .env.example .env
npm run dev
```

If frontend dependencies have not been installed, run `npm ci` in this folder first. The `.env` file must contain:

```text
VITE_API_BASE_URL=http://localhost:8000
```

Open the address Vite prints, normally `http://localhost:5173`. If Vite was already running when `.env` was created or changed, stop it with **Ctrl+C** and run `npm run dev` again.

### 3. Test formatting

Wait for **“Formatting server ready”**, select a valid `.docx` document (up to 10 MB), and choose **Format my paper**. The app accepts DOCX files only; legacy `.doc`, password-protected, macro-enabled, and template files are rejected.

If it still fails, capture:

1. The exact message displayed on the page.
2. The latest error from the backend terminal.
3. Whether `http://localhost:8000/api/health` works.
4. Whether the page is local (`localhost:5173`) or deployed (share the page URL, without private document content).

## What the app does

- React + TypeScript + Vite frontend.
- FastAPI + `python-docx` backend.
- Applies the single **Basic Academic Layout** profile to supported Word paragraph styles, preserving document text and checking the output before offering a download.
- No database, user accounts, or external AI service.
- The project documentation says uploaded job files are removed after processing. Do not treat this as a substitute for keeping your own backup.

## Project layout and deployment

- `backend/app/`: API, processing pipeline, security checks, formatting engine, and profile.
- `backend/tests/`: API workflow and safety tests.
- `frontend/src/`: UI and API client.
- `render.yaml`: Render backend blueprint; configured for Python 3.12 and a free web service.
- Frontend is intended for Vercel with root directory `frontend`.

For deployment, set Render `ALLOWED_ORIGINS` to the exact Vercel site origin (no trailing slash). Set Vercel `VITE_API_BASE_URL` to the Render API URL (no trailing slash), then redeploy the frontend because Vite embeds this value at build time. Free Render services can sleep when idle, so the first request may take around 30–60 seconds.

## Supported behavior and limitations

- Maximum upload defaults to 10 MB; the backend also limits uncompressed archive size and worker memory/time.
- Only supported paragraph styles are reformatted; tables, lists, titles, captions, headers/footers, footnotes, equations, and several other elements are kept but not reformatted.
- A4 formatting may cause wide tables or images to extend beyond the page margins; review the downloaded document in Word.
- The service processes one job at a time by default and has an in-memory per-instance rate limit.
- The project README states that security review, load testing, deployed smoke testing, browser UI testing, and large-file testing on Render have not been completed.

## Workspace notes

At the time of this check, the working tree has local changes in `README.md` and `frontend/package-lock.json`. The README contains Windows setup steps; the lockfile records Vite 5 and Vitest 2, which installed successfully with the existing React plugin. Do not discard these changes without reviewing them.

The last dependency install reported 5 npm audit findings (2 moderate, 1 high, 2 critical). No automatic audit fix was applied because forced upgrades can create new incompatible versions.
