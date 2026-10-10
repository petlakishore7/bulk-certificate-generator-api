import re
from datetime import date

from .errors import validation_error

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_NAME = 100
MAX_EMAIL = 254
MAX_EVENT = 200
MAX_ISSUER = 100


def _clean(value):
    return " ".join(value.split())


def _encodable(text):
    """The built-in PDF font supports Latin-1 style text only."""
    try:
        text.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return not any(ord(ch) < 32 for ch in text)


def validate_job_payload(payload, max_recipients, default_issuer):
    """Validate job-level fields. Raises ApiError(422) when the request itself is invalid.

    Individual recipients are NOT rejected here; they are validated per item so that
    one bad recipient does not block the rest of the job.
    """
    if not isinstance(payload, dict):
        raise validation_error("Request body must be a JSON object.")

    errors = []

    event_name = payload.get("event_name")
    if not isinstance(event_name, str) or not _clean(event_name):
        errors.append({"field": "event_name", "message": "event_name is required and must be a non-empty string."})
    else:
        event_name = _clean(event_name)
        if len(event_name) > MAX_EVENT:
            errors.append({"field": "event_name", "message": f"event_name must be at most {MAX_EVENT} characters."})
        elif not _encodable(event_name):
            errors.append({"field": "event_name", "message": "event_name contains unsupported characters."})

    issuer = payload.get("issuer")
    if issuer is None:
        issuer = default_issuer
    elif not isinstance(issuer, str) or not _clean(issuer) or len(_clean(issuer)) > MAX_ISSUER:
        errors.append({"field": "issuer", "message": f"issuer must be a non-empty string of at most {MAX_ISSUER} characters."})
    else:
        issuer = _clean(issuer)
        if not _encodable(issuer):
            errors.append({"field": "issuer", "message": "issuer contains unsupported characters."})

    issue_date = payload.get("issue_date")
    if issue_date is None:
        issue_date = date.today().isoformat()
    else:
        try:
            if not isinstance(issue_date, str):
                raise ValueError
            issue_date = date.fromisoformat(issue_date).isoformat()
        except ValueError:
            errors.append({"field": "issue_date", "message": "issue_date must be a valid date in YYYY-MM-DD format."})

    recipients = payload.get("recipients")
    if not isinstance(recipients, list) or not recipients:
        errors.append({"field": "recipients", "message": "recipients is required and must be a non-empty list."})
    elif len(recipients) > max_recipients:
        errors.append({"field": "recipients", "message": f"A job may contain at most {max_recipients} recipients."})

    if errors:
        raise validation_error("Invalid certificate generation request.", errors)

    return {"event_name": event_name, "issuer": issuer, "issue_date": issue_date, "recipients": recipients}


def validate_recipient(raw):
    """Return (clean_recipient, None) or (None, error_message)."""
    if not isinstance(raw, dict):
        return None, "Recipient must be a JSON object with 'name' and 'email'."

    problems = []
    name = raw.get("name")
    email = raw.get("email")

    if not isinstance(name, str) or not _clean(name):
        problems.append("name is required and must be a non-empty string")
    else:
        name = _clean(name)
        if len(name) > MAX_NAME:
            problems.append(f"name must be at most {MAX_NAME} characters")
        elif not _encodable(name):
            problems.append("name contains characters not supported by the certificate font")

    if not isinstance(email, str) or not email.strip():
        problems.append("email is required and must be a non-empty string")
    else:
        email = email.strip()
        if len(email) > MAX_EMAIL or not EMAIL_RE.match(email):
            problems.append("email is not a valid email address")

    if problems:
        return None, "; ".join(problems)
    return {"name": name, "email": email}, None
