# Bulk Certificate Generator API

A backend API that accepts **one request containing many recipients**, validates each recipient, generates a PDF certificate per valid recipient from a single predefined template, tracks progress, and lets the client retrieve the results (individually or as a ZIP).

## Features
- Bulk job creation (`POST /api/jobs`) - one request, many certificates
- Per-recipient validation; **one bad recipient or one failed render never blocks the rest of the job**
- Background processing with live progress (`queued → processing → completed | completed_with_errors | failed`)
- Per-certificate status and error reason in the job status
- Download one certificate (PDF) or all successful ones (ZIP)
- Consistent JSON error format

## Technology
Python 3.10+ · **Flask** · **SQLite** (relational DB, stdlib `sqlite3`) · **ReportLab** (PDF generation) · `unittest` for tests (`pypdf` is used to read generated PDFs in tests).

## Setup
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration (all optional - see `.env.example`)
| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_PATH` | `data/certificates.db` | SQLite file |
| `CERTIFICATE_DIR` | `data/certificates` | Where PDFs are stored |
| `ORGANIZATION_NAME` | `Example Organization` | Default "Issued by" text |
| `MAX_RECIPIENTS` | `1000` | Max recipients per job |
| `WORKER_THREADS` | `2` | Background worker threads |
| `SYNC_PROCESSING` | `false` | Process inside the request (used by some tests) |
| `HOST` / `PORT` | `127.0.0.1` / `5000` | Dev server bind |

## Run
```bash
python run.py
# API on http://127.0.0.1:5000
```

## Run tests
```bash
python -m unittest discover -s tests -t . -v
# (pytest also works if installed: pytest)
```

## API

Errors always look like `{"error": {"code": "...", "message": "...", "details": [...]}}`.

### 1. Submit a generation request - `POST /api/jobs`
| Field | Required | Notes |
|---|---|---|
| `event_name` | yes | Course/event printed on the certificate (≤200 chars) |
| `recipients` | yes | Non-empty list (≤ `MAX_RECIPIENTS`) of `{"name", "email"}` |
| `issuer` | no | Defaults to `ORGANIZATION_NAME` |
| `issue_date` | no | `YYYY-MM-DD`, defaults to today |

```bash
curl -X POST http://127.0.0.1:5000/api/jobs -H "Content-Type: application/json" -d '{
  "event_name": "Data Science Workshop",
  "issuer": "Acme Academy",
  "issue_date": "2026-10-01",
  "recipients": [
    {"name": "Alice Johnson", "email": "alice@example.com"},
    {"name": "Bob Smith", "email": "bob@example.com"},
    {"name": "No Email"}
  ]
}'
```
Response `202 Accepted` (+ `Location: /api/jobs/<job_id>`):
```json
{
  "job_id": "5f0c...",
  "status": "queued",
  "event_name": "Data Science Workshop",
  "summary": {"total": 3, "succeeded": 0, "failed": 1, "pending": 2, "progress_percent": 33.3},
  "certificates": [
    {"certificate_id": "a1...", "index": 0, "name": "Alice Johnson", "email": "alice@example.com",
     "status": "pending", "error": null, "download_url": null},
    {"certificate_id": "c3...", "index": 2, "name": "No Email", "email": null,
     "status": "failed", "error": "email is required and must be a non-empty string", "download_url": null}
  ],
  "links": {"self": "/api/jobs/5f0c...", "download_all": "/api/jobs/5f0c.../download"}
}
```
Request-level problems (bad JSON → `400`; missing `event_name`/`recipients`, too many recipients, bad date → `422` with per-field `details`) reject the whole request. **Recipient-level** problems do not.

### 2. Check progress/result - `GET /api/jobs/{job_id}[?status=pending|succeeded|failed]`
Returns the same structure as above. Poll until `status` is `completed`, `completed_with_errors` or `failed`.
`progress_percent` = (succeeded + failed) / total. Each certificate has its own `status` and `error`.

### 3. Retrieve certificates
- One PDF: `GET /api/jobs/{job_id}/certificates/{certificate_id}` (use `download_url` from the status) → `application/pdf`.
  `409` if the certificate is still pending or failed; `404` if unknown.
- All successful: `GET /api/jobs/{job_id}/download` → ZIP (`0001_Alice_Johnson.pdf`, …). `409` while the job is unfinished or when nothing was generated.

```bash
curl -o alice.pdf http://127.0.0.1:5000/api/jobs/<job_id>/certificates/<certificate_id>
curl -o all.zip   http://127.0.0.1:5000/api/jobs/<job_id>/download
```
`GET /health` returns `{"status": "ok"}`.

## Design decisions
- **Background processing (thread pool) instead of synchronous.** A job may hold hundreds of recipients, so the request returns `202` immediately and the client polls. The worker commits after every certificate, so progress is accurate in real time. Trade-off: jobs run in-process (see limitations); a task queue (Celery/RQ) would be the next step for multiple servers.
- **Validate per recipient, fail per recipient.** Invalid and duplicate recipients are stored as `failed` rows with a reason at creation time; valid ones are `pending`. Each certificate is generated in its own `try/except`, so one rendering error does not stop the others. Final job status reflects the mix.
- **Duplicates:** a repeated email (case-insensitive) within a job is marked failed - one certificate per person per job.
- **Atomic file writes:** PDFs are written to a temp file then renamed, so no partial certificates exist.
- **SQLite + stdlib `sqlite3`:** zero setup, relational, and enough for this scope; connections are short-lived per operation so they are safe across threads. The two-table schema (`jobs`, `certificates`) is in `app/db.py`.
- **Single template** in `app/certificate.py` (ReportLab, A4 landscape); name font shrinks to fit long names, long event names wrap.
- **Unguessable IDs:** job/certificate IDs are random UUIDs; certificate files are only served via DB-looked-up, path-checked locations.

## Assumptions and known limitations
- No authentication/authorization (not requested); anyone with a job ID can read it.
- The built-in PDF font supports Latin-1 (Western European) characters; names with other scripts (e.g. Chinese) are rejected per-recipient with a clear error rather than printed incorrectly.
- Background jobs run inside the web process: jobs still queued/processing when the process stops are not resumed automatically. With multiple server processes, use a proper queue.
- The job status returns every certificate (max 1000 per job, no pagination); use `?status=` to filter.
- Certificates are not emailed; `email` is validated and stored only.
- Generated files are stored on local disk and never expire.
