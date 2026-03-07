ALLOWED_AUTH_STATUSES = {"active", "studying"}


def normalize_status(status: str | None) -> str:
    if status is None:
        return "active"
    return str(status).strip().lower()


def is_auth_status_allowed(status: str | None) -> bool:
    return normalize_status(status) in ALLOWED_AUTH_STATUSES
