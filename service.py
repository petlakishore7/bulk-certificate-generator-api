"""Job creation, background processing and querying."""
import json
import logging
import os
import uuid

from . import certificate
from .db import get_conn, utcnow
from .validation import validate_recipient

logger = logging.getLogger(__name__)

# Job statuses: queued -> processing -> completed | completed_with_errors | failed
TERMINAL_STATUSES = ("completed", "completed_with_errors", "failed")


def _text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        value = json.dumps(value, default=str)
    return value[:255]


def _final_status(succeeded, failed):
    if succeeded == 0:
        return "failed"
    return "completed" if failed == 0 else "completed_with_errors"


def _counts(conn, job_id):
    rows = conn.execute(
        "SELECT status, COUNT(*) AS c FROM certificates WHERE job_id = ? GROUP BY status", (job_id,)
    ).fetchall()
    counts = {"pending": 0, "succeeded": 0, "failed": 0}
    counts.update({r["status"]: r["c"] for r in rows})
    return counts


def _finalize(conn, job_id):
    counts = _counts(conn, job_id)
    conn.execute(
        "UPDATE jobs SET status = ?, completed_at = ? WHERE id = ?",
        (_final_status(counts["succeeded"], counts["failed"]), utcnow(), job_id),
    )


def create_job(db_path, *, event_name, issuer, issue_date, recipients):
    """Validate every recipient and persist the job.

    Invalid/duplicate recipients are stored as `failed` certificates (with a reason);
    valid ones are stored as `pending` for the worker. Returns (job_id, pending_count).
    """
    job_id = uuid.uuid4().hex
    now = utcnow()
    rows, seen, pending = [], set(), 0

    for idx, raw in enumerate(recipients):
        clean, error = validate_recipient(raw)
        if clean and clean["email"].lower() in seen:
            clean, error = None, "Duplicate recipient: this email already appears earlier in the job"
        if clean:
            seen.add(clean["email"].lower())
            status = "pending"
            pending += 1
            name, email = clean["name"], clean["email"]
        else:
            status = "failed"
            name = _text(raw.get("name")) if isinstance(raw, dict) else None
            email = _text(raw.get("email")) if isinstance(raw, dict) else None
        rows.append((uuid.uuid4().hex, job_id, idx, name, email, status, error, None, now))

    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO jobs (id, event_name, issuer, issue_date, status, total, created_at) "
            "VALUES (?, ?, ?, ?, 'queued', ?, ?)",
            (job_id, event_name, issuer, issue_date, len(rows), now),
        )
        conn.executemany(
            "INSERT INTO certificates (id, job_id, idx, recipient_name, recipient_email, status, error, "
            "file_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        if pending == 0:  # nothing to generate: every recipient was invalid
            _finalize(conn, job_id)
    return job_id, pending


def process_job(db_path, cert_dir, job_id):
    """Generate every pending certificate of a job. One failure never stops the others."""
    try:
        with get_conn(db_path) as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if job is None:
                return
            conn.execute("UPDATE jobs SET status = 'processing' WHERE id = ? AND status = 'queued'", (job_id,))
            todo = conn.execute(
                "SELECT id, recipient_name FROM certificates WHERE job_id = ? AND status = 'pending' ORDER BY idx",
                (job_id,),
            ).fetchall()

        for row in todo:
            rel_path = f"{job_id}/{row['id']}.pdf"
            try:
                certificate.generate_certificate_pdf(
                    os.path.join(cert_dir, rel_path),
                    recipient_name=row["recipient_name"],
                    event_name=job["event_name"],
                    issuer=job["issuer"],
                    issue_date=job["issue_date"],
                    certificate_id=row["id"],
                )
                update = ("succeeded", None, rel_path)
            except Exception as exc:  # isolate the failure to this certificate
                logger.exception("Certificate %s in job %s failed", row["id"], job_id)
                update = ("failed", f"Certificate generation failed: {exc}", None)
            with get_conn(db_path) as conn:  # commit per certificate so progress is visible
                conn.execute(
                    "UPDATE certificates SET status = ?, error = ?, file_path = ? WHERE id = ?",
                    (*update, row["id"]),
                )
    except Exception as exc:  # e.g. database trouble: never leave the job stuck in "processing"
        logger.exception("Job %s aborted", job_id)
        with get_conn(db_path) as conn:
            conn.execute(
                "UPDATE certificates SET status = 'failed', error = ? WHERE job_id = ? AND status = 'pending'",
                (f"Job processing aborted: {exc}", job_id),
            )
    with get_conn(db_path) as conn:
        _finalize(conn, job_id)


def get_job(db_path, job_id, status_filter=None):
    """Return job detail (summary + per-certificate results) or None if unknown."""
    with get_conn(db_path) as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            return None
        counts = _counts(conn, job_id)
        query = "SELECT * FROM certificates WHERE job_id = ?"
        params = [job_id]
        if status_filter:
            query += " AND status = ?"
            params.append(status_filter)
        certs = conn.execute(query + " ORDER BY idx", params).fetchall()

    done = counts["succeeded"] + counts["failed"]
    base = f"/api/jobs/{job_id}"
    return {
        "job_id": job["id"],
        "status": job["status"],
        "event_name": job["event_name"],
        "issuer": job["issuer"],
        "issue_date": job["issue_date"],
        "created_at": job["created_at"],
        "completed_at": job["completed_at"],
        "summary": {
            "total": job["total"],
            "succeeded": counts["succeeded"],
            "failed": counts["failed"],
            "pending": counts["pending"],
            "progress_percent": round(100 * done / job["total"], 1) if job["total"] else 100.0,
        },
        "certificates": [
            {
                "certificate_id": c["id"],
                "index": c["idx"],
                "name": c["recipient_name"],
                "email": c["recipient_email"],
                "status": c["status"],
                "error": c["error"],
                "download_url": f"{base}/certificates/{c['id']}" if c["status"] == "succeeded" else None,
            }
            for c in certs
        ],
        "links": {"self": base, "download_all": f"{base}/download"},
    }


def get_certificate(db_path, job_id, certificate_id):
    with get_conn(db_path) as conn:
        return conn.execute(
            "SELECT * FROM certificates WHERE id = ? AND job_id = ?", (certificate_id, job_id)
        ).fetchone()


def get_job_status(db_path, job_id):
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return row["status"] if row else None


def list_successful(db_path, job_id):
    with get_conn(db_path) as conn:
        return conn.execute(
            "SELECT * FROM certificates WHERE job_id = ? AND status = 'succeeded' ORDER BY idx", (job_id,)
        ).fetchall()
