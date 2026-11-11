import os
import secrets
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    database_path: Path
    session_secret: str
    secure_cookies: bool
    worker_concurrency: int
    min_host_interval_seconds: float
    spacy_model: str | None


def _session_secret(state_dir: Path) -> str:
    configured = os.environ.get("SCRAPZ_SESSION_SECRET")
    if configured:
        if len(configured) < 32:
            raise ValueError("SCRAPZ_SESSION_SECRET must be at least 32 characters.")
        return configured

    state_dir.mkdir(parents=True, exist_ok=True)
    secret_path = state_dir / "session.key"
    try:
        descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        secret = secret_path.read_text(encoding="utf-8").strip()
        if len(secret) < 32:
            raise ValueError(f"Session key at {secret_path} is invalid.")
        return secret

    secret = secrets.token_urlsafe(48)
    with os.fdopen(descriptor, "w", encoding="utf-8") as key_file:
        key_file.write(secret)
    return secret


def get_settings() -> Settings:
    state_dir = Path(os.environ.get("SCRAPZ_STATE_DIR", ROOT / ".state")).expanduser()
    database_path = Path(
        os.environ.get("SCRAPZ_DATABASE_PATH", state_dir / "scrapz.sqlite3")
    ).expanduser()
    try:
        concurrency = int(os.environ.get("SCRAPZ_WORKER_CONCURRENCY", "3"))
    except ValueError as exc:
        raise ValueError("SCRAPZ_WORKER_CONCURRENCY must be an integer.") from exc
    if not 1 <= concurrency <= 16:
        raise ValueError("SCRAPZ_WORKER_CONCURRENCY must be between 1 and 16.")
    try:
        min_host_interval = float(os.environ.get("SCRAPZ_MIN_HOST_INTERVAL_SECONDS", "1"))
    except ValueError as exc:
        raise ValueError("SCRAPZ_MIN_HOST_INTERVAL_SECONDS must be a number.") from exc
    if not 0 <= min_host_interval <= 60:
        raise ValueError("SCRAPZ_MIN_HOST_INTERVAL_SECONDS must be between 0 and 60.")

    model = os.environ.get("SCRAPZ_SPACY_MODEL") or None
    return Settings(
        database_path=database_path,
        session_secret=_session_secret(state_dir),
        secure_cookies=os.environ.get("SCRAPZ_SECURE_COOKIES", "0") == "1",
        worker_concurrency=concurrency,
        min_host_interval_seconds=min_host_interval,
        spacy_model=model,
    )
