import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from app.security import hash_password, verify_password


ROLES = {"admin", "analyst", "viewer"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


@contextmanager
def database(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(database_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db(database_path: Path) -> None:
    with database(database_path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'analyst', 'viewer')),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL CHECK (kind IN ('web', 'feed')),
                category TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_by INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY,
                created_by INTEGER NOT NULL REFERENCES users(id),
                query TEXT NOT NULL DEFAULT '',
                source_id INTEGER REFERENCES sources(id),
                source_url TEXT,
                category TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
                attempts INTEGER NOT NULL DEFAULT 0,
                result_count INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                next_run_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id),
                source_url TEXT NOT NULL,
                canonical_url TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                category TEXT NOT NULL,
                published_at TEXT,
                extraction_confidence REAL NOT NULL,
                entities_json TEXT NOT NULL DEFAULT '[]',
                content_hash TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                verification_status TEXT NOT NULL DEFAULT 'unreviewed'
                    CHECK (verification_status IN ('unreviewed', 'source_checked', 'uncertain')),
                reviewed_by INTEGER REFERENCES users(id),
                reviewed_at TEXT,
                UNIQUE(canonical_url, content_hash)
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY,
                user_id INTEGER REFERENCES users(id),
                action TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_status_run
                ON jobs(status, next_run_at, id);
            CREATE INDEX IF NOT EXISTS idx_records_collected
                ON records(collected_at DESC);
            CREATE INDEX IF NOT EXISTS idx_records_category
                ON records(category);
            CREATE INDEX IF NOT EXISTS idx_audit_created
                ON audit_events(created_at DESC);
            """
        )
        record_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(records)")
        }
        if "verification_status" not in record_columns:
            connection.execute(
                """ALTER TABLE records ADD COLUMN verification_status TEXT NOT NULL
                   DEFAULT 'unreviewed'
                   CHECK (verification_status IN ('unreviewed', 'source_checked', 'uncertain'))"""
            )
        if "reviewed_by" not in record_columns:
            connection.execute(
                "ALTER TABLE records ADD COLUMN reviewed_by INTEGER REFERENCES users(id)"
            )
        if "reviewed_at" not in record_columns:
            connection.execute("ALTER TABLE records ADD COLUMN reviewed_at TEXT")


def create_user(database_path: Path, username: str, password: str, role: str) -> int:
    username = username.strip()
    if not username or len(username) > 80:
        raise ValueError("Username must be between 1 and 80 characters.")
    if role not in ROLES:
        raise ValueError("Role must be admin, analyst, or viewer.")
    password_hash = hash_password(password)
    with database(database_path) as connection:
        cursor = connection.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (username, password_hash, role, utc_now()),
        )
        return int(cursor.lastrowid)


def authenticate(database_path: Path, username: str, password: str) -> dict[str, Any] | None:
    with database(database_path) as connection:
        row = connection.execute(
            "SELECT id, username, password_hash, role FROM users WHERE username = ?",
            (username.strip(),),
        ).fetchone()
    if row is None:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


def get_user(database_path: Path, user_id: int) -> dict[str, Any] | None:
    with database(database_path) as connection:
        row = connection.execute(
            "SELECT id, username, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def audit(database_path: Path, user_id: int | None, action: str, detail: str) -> None:
    with database(database_path) as connection:
        connection.execute(
            "INSERT INTO audit_events (user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (user_id, action, detail[:500], utc_now()),
        )


def create_source(
    database_path: Path,
    name: str,
    url: str,
    kind: str,
    category: str,
    user_id: int,
) -> int:
    if kind not in {"web", "feed"}:
        raise ValueError("Source type must be a webpage or RSS/Atom feed.")
    with database(database_path) as connection:
        cursor = connection.execute(
            """INSERT INTO sources (name, url, kind, category, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name.strip()[:120], url, kind, category, user_id, utc_now()),
        )
        return int(cursor.lastrowid)


def list_sources(database_path: Path, enabled_only: bool = False) -> list[dict[str, Any]]:
    condition = "WHERE enabled = 1" if enabled_only else ""
    with database(database_path) as connection:
        rows = connection.execute(
            f"SELECT * FROM sources {condition} ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [dict(row) for row in rows]


def get_source(database_path: Path, source_id: int) -> dict[str, Any] | None:
    with database(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
    return dict(row) if row else None


def toggle_source(database_path: Path, source_id: int) -> bool:
    with database(database_path) as connection:
        cursor = connection.execute(
            "UPDATE sources SET enabled = CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE id = ?",
            (source_id,),
        )
        return cursor.rowcount == 1


def create_job(
    database_path: Path,
    user_id: int,
    query: str,
    source_id: int | None,
    source_url: str | None,
    category: str,
) -> int:
    now = utc_now()
    with database(database_path) as connection:
        cursor = connection.execute(
            """INSERT INTO jobs
               (created_by, query, source_id, source_url, category, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)""",
            (user_id, query.strip()[:200], source_id, source_url, category, now, now),
        )
        return int(cursor.lastrowid)


def claim_job(database_path: Path) -> dict[str, Any] | None:
    now = utc_now()
    with database(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """SELECT * FROM jobs
               WHERE status = 'queued' AND (next_run_at IS NULL OR next_run_at <= ?)
               ORDER BY id LIMIT 1""",
            (now,),
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            """UPDATE jobs SET status = 'running', attempts = attempts + 1,
               next_run_at = NULL, updated_at = ? WHERE id = ?""",
            (now, row["id"]),
        )
        claimed = dict(row)
        claimed["attempts"] += 1
        return claimed


def recover_running_jobs(database_path: Path) -> None:
    with database(database_path) as connection:
        connection.execute(
            "UPDATE jobs SET status = 'queued', updated_at = ? WHERE status = 'running'",
            (utc_now(),),
        )


def add_records(database_path: Path, records: list[dict[str, Any]]) -> int:
    if not records:
        return 0
    inserted = 0
    with database(database_path) as connection:
        for record in records:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO records
                   (job_id, source_url, canonical_url, title, summary, category, published_at,
                    extraction_confidence, entities_json, content_hash, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["job_id"],
                    record["source_url"],
                    record["canonical_url"],
                    record["title"],
                    record["summary"],
                    record["category"],
                    record["published_at"],
                    record["extraction_confidence"],
                    json.dumps(record["entities"], ensure_ascii=False),
                    record["content_hash"],
                    record["collected_at"],
                ),
            )
            inserted += cursor.rowcount
    return inserted


def update_job(
    database_path: Path,
    job_id: int,
    status: str,
    result_count: int | None = None,
    error: str | None = None,
    next_run_at: str | None = None,
) -> None:
    with database(database_path) as connection:
        connection.execute(
            """UPDATE jobs SET status = ?, result_count = COALESCE(?, result_count),
               error = ?, next_run_at = ?, updated_at = ? WHERE id = ?""",
            (status, result_count, error[:500] if error else None, next_run_at, utc_now(), job_id),
        )


def list_jobs(database_path: Path, limit: int = 30) -> list[dict[str, Any]]:
    with database(database_path) as connection:
        rows = connection.execute(
            """SELECT j.*, u.username,
               COALESCE(s.name, j.source_url, 'Unknown source') AS source_name
               FROM jobs j JOIN users u ON u.id = j.created_by
               LEFT JOIN sources s ON s.id = j.source_id
               ORDER BY j.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_records(
    database_path: Path,
    query: str = "",
    category: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    values: list[Any] = []
    if query.strip():
        term = f"%{query.strip()[:100]}%"
        clauses.append("(title LIKE ? OR summary LIKE ? OR source_url LIKE ?)")
        values.extend((term, term, term))
    if category:
        clauses.append("category = ?")
        values.append(category)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    values.append(limit)
    with database(database_path) as connection:
        rows = connection.execute(
            f"""SELECT records.*, users.username AS reviewer
                FROM records LEFT JOIN users ON users.id = records.reviewed_by
                {where} ORDER BY records.collected_at DESC, records.id DESC LIMIT ?""",
            values,
        ).fetchall()
    records = []
    for row in rows:
        record = dict(row)
        record["entities"] = json.loads(record.pop("entities_json"))
        records.append(record)
    return records


def update_record_verification(
    database_path: Path,
    record_id: int,
    user_id: int,
    status: str,
) -> bool:
    if status not in {"unreviewed", "source_checked", "uncertain"}:
        raise ValueError("Invalid verification status.")
    reviewed_by = None if status == "unreviewed" else user_id
    reviewed_at = None if status == "unreviewed" else utc_now()
    with database(database_path) as connection:
        cursor = connection.execute(
            """UPDATE records
               SET verification_status = ?, reviewed_by = ?, reviewed_at = ?
               WHERE id = ?""",
            (status, reviewed_by, reviewed_at, record_id),
        )
        return cursor.rowcount == 1


def record_by_id(database_path: Path, record_id: int) -> dict[str, Any] | None:
    with database(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM records WHERE id = ?", (record_id,)
        ).fetchone()
    if row is None:
        return None
    record = dict(row)
    record["entities"] = json.loads(record.pop("entities_json"))
    return record


def statistics(database_path: Path) -> dict[str, Any]:
    since = (datetime.now(UTC) - timedelta(days=29)).date().isoformat()
    with database(database_path) as connection:
        total = connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        categories = connection.execute(
            "SELECT category, COUNT(*) AS count FROM records GROUP BY category ORDER BY count DESC"
        ).fetchall()
        daily = connection.execute(
            """SELECT substr(collected_at, 1, 10) AS day, COUNT(*) AS count
               FROM records WHERE substr(collected_at, 1, 10) >= ?
               GROUP BY day ORDER BY day""",
            (since,),
        ).fetchall()
        job_counts = connection.execute(
            "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
        ).fetchall()
    return {
        "total": total,
        "categories": [dict(row) for row in categories],
        "daily": [dict(row) for row in daily],
        "jobs": {row["status"]: row["count"] for row in job_counts},
    }


def list_audit_events(database_path: Path, limit: int = 25) -> list[dict[str, Any]]:
    with database(database_path) as connection:
        rows = connection.execute(
            """SELECT a.*, u.username FROM audit_events a
               LEFT JOIN users u ON u.id = a.user_id
               ORDER BY a.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
