import io
import os
import re
import zipfile

from flask import Blueprint, current_app, jsonify, request, send_file

from . import service
from .errors import ApiError
from .validation import validate_job_payload

bp = Blueprint("api", __name__)
CERT_STATUSES = ("pending", "succeeded", "failed")


@bp.get("/health")
def health():
    return jsonify({"status": "ok"})


@bp.post("/api/jobs")
def create_job():
    payload = request.get_json(silent=True)
    if payload is None:
        raise ApiError(400, "invalid_json", "Request body must be valid JSON with Content-Type: application/json.")

    cfg = current_app.config
    data = validate_job_payload(payload, cfg["MAX_RECIPIENTS"], cfg["ORGANIZATION_NAME"])
    job_id, pending = service.create_job(cfg["DATABASE_PATH"], **data)

    if pending:
        args = (cfg["DATABASE_PATH"], cfg["CERTIFICATE_DIR"], job_id)
        if cfg["SYNC_PROCESSING"]:
            service.process_job(*args)
        else:
            current_app.extensions["executor"].submit(service.process_job, *args)

    response = jsonify(service.get_job(cfg["DATABASE_PATH"], job_id, status_filter=None))
    response.status_code = 202
    response.headers["Location"] = f"/api/jobs/{job_id}"
    return response


@bp.get("/api/jobs/<job_id>")
def job_status(job_id):
    status_filter = request.args.get("status")
    if status_filter and status_filter not in CERT_STATUSES:
        raise ApiError(400, "invalid_filter", f"status must be one of: {', '.join(CERT_STATUSES)}.")
    job = service.get_job(current_app.config["DATABASE_PATH"], job_id, status_filter)
    if job is None:
        raise ApiError(404, "job_not_found", f"Job '{job_id}' was not found.")
    return jsonify(job)


def _resolve_file(rel_path):
    root = os.path.realpath(current_app.config["CERTIFICATE_DIR"])
    full = os.path.realpath(os.path.join(root, rel_path))
    if not full.startswith(root + os.sep) or not os.path.isfile(full):
        raise ApiError(410, "file_missing", "The certificate file is no longer available.")
    return full


@bp.get("/api/jobs/<job_id>/certificates/<certificate_id>")
def download_certificate(job_id, certificate_id):
    cert = service.get_certificate(current_app.config["DATABASE_PATH"], job_id, certificate_id)
    if cert is None:
        raise ApiError(404, "certificate_not_found", "Certificate was not found for this job.")
    if cert["status"] == "pending":
        raise ApiError(409, "certificate_not_ready", "Certificate has not been generated yet.")
    if cert["status"] == "failed":
        raise ApiError(409, "certificate_failed", f"Certificate generation failed: {cert['error']}")
    return send_file(_resolve_file(cert["file_path"]), mimetype="application/pdf",
                     download_name=f"certificate_{certificate_id}.pdf")


@bp.get("/api/jobs/<job_id>/download")
def download_all(job_id):
    db_path = current_app.config["DATABASE_PATH"]
    status = service.get_job_status(db_path, job_id)
    if status is None:
        raise ApiError(404, "job_not_found", f"Job '{job_id}' was not found.")
    if status not in service.TERMINAL_STATUSES:
        raise ApiError(409, "job_not_finished", "Job is still being processed; try again when it has finished.")
    certs = service.list_successful(db_path, job_id)
    if not certs:
        raise ApiError(409, "no_certificates", "No certificates were generated for this job.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for cert in certs:
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", cert["recipient_name"]).strip("_") or "certificate"
            archive.write(_resolve_file(cert["file_path"]), f"{cert['idx'] + 1:04d}_{safe}.pdf")
    buffer.seek(0)
    return send_file(buffer, mimetype="application/zip", download_name=f"certificates_{job_id}.zip")
