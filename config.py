import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _flag(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def load_config(overrides=None):
    """Build configuration from environment variables (with defaults)."""
    config = {
        "DATABASE_PATH": os.environ.get("DATABASE_PATH", str(BASE_DIR / "data" / "certificates.db")),
        "CERTIFICATE_DIR": os.environ.get("CERTIFICATE_DIR", str(BASE_DIR / "data" / "certificates")),
        "ORGANIZATION_NAME": os.environ.get("ORGANIZATION_NAME", "Example Organization"),
        "MAX_RECIPIENTS": int(os.environ.get("MAX_RECIPIENTS", "1000")),
        "WORKER_THREADS": int(os.environ.get("WORKER_THREADS", "2")),
        # Process jobs inside the request instead of in the background (mainly for tests).
        "SYNC_PROCESSING": _flag(os.environ.get("SYNC_PROCESSING", "false")),
        "MAX_CONTENT_LENGTH": 5 * 1024 * 1024,
    }
    if overrides:
        config.update(overrides)
    return config
