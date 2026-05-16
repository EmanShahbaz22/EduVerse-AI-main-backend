import os
import json
from urllib.parse import urlsplit

# Optional default tenant; keep empty by default to avoid hard-coded cross-tenant coupling.
TENANT_ID = os.getenv("TENANT_ID", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:4200").rstrip("/")

MAX_SUBSCRIPTION_PLANS = int(os.getenv("MAX_SUBSCRIPTION_PLANS", "3"))


def _normalize_origin(origin: str) -> str | None:
    origin = (origin or "").strip().strip("'").strip('"')
    if not origin:
        return None
    parts = urlsplit(origin)
    if not parts.scheme or not parts.netloc:
        return None
    # CORS origin must be scheme + host[:port] only (no path/query/fragment).
    return f"{parts.scheme}://{parts.netloc}"


def get_cors_origins() -> list[str]:
    # Priority: explicit CORS_ORIGINS CSV, then FRONTEND_URL, then local defaults.
    configured = os.getenv("CORS_ORIGINS", "")
    frontend_url = os.getenv("FRONTEND_URL", "")
    defaults = [
        "http://localhost:4200",
        "http://127.0.0.1:4200",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8001",
        "http://127.0.0.1:8001",
        "https://https://eduverse-ai-app.vercel.app/",
        "https://https://eduverse-okmrmw2fq-ayesha-javaids-projects.vercel.app/",
        "https://eduverse-ai-git-main-ayesha-javaids-projects.vercel.app",
    ]

    candidates: list[str] = []
    if configured:
        raw = configured.strip()
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    candidates.extend(
                        [str(item).strip() for item in parsed if str(item).strip()]
                    )
            except json.JSONDecodeError:
                pass
        if not candidates:
            candidates.extend(
                [item.strip() for item in configured.split(",") if item.strip()]
            )
    if frontend_url:
        candidates.append(frontend_url)

    env = os.getenv("ENV", "development").lower()
    if env != "production":
        candidates.extend(defaults)

    if not candidates:
        candidates = defaults

    normalized: list[str] = []
    for origin in candidates:
        parsed = _normalize_origin(origin)
        if parsed and parsed not in normalized:
            normalized.append(parsed)
    if not normalized:
        return defaults
    return normalized
