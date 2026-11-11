import asyncio
import csv
import io
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import db
from app.collector import worker_loop
from app.config import get_settings
from app.extraction import CATEGORIES
from app.urls import normalize_url


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("scrapz")
settings = get_settings()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def safe_csv_cell(value: str) -> str:
    formula_prefix = value.lstrip(" \t\r\n")
    return (
        f"'{value}"
        if formula_prefix.startswith(("=", "+", "-", "@", "\t", "\r"))
        else value
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    from app.entities import load_pipeline

    db.init_db(settings.database_path)
    db.recover_running_jobs(settings.database_path)
    load_pipeline(settings.spacy_model)
    worker = asyncio.create_task(worker_loop(settings), name="collection-worker")
    logger.info("Scrapz started; database=%s concurrency=%s", settings.database_path, settings.worker_concurrency)
    try:
        yield
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Scrapz Research", lifespan=lifespan)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="scrapz_session",
    same_site="strict",
    https_only=settings.secure_cookies,
    max_age=8 * 60 * 60,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; style-src 'self'; img-src 'self' data:; "
        "object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    return response


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def valid_csrf(request: Request, submitted: str) -> bool:
    token = request.session.get("csrf_token", "")
    return bool(token) and secrets.compare_digest(token, submitted)


def current_user(request: Request) -> dict[str, Any] | None:
    user_id = request.session.get("user_id")
    if not isinstance(user_id, int):
        return None
    return db.get_user(settings.database_path, user_id)


def page(
    request: Request,
    template: str,
    user: dict[str, Any] | None = None,
    **context: Any,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name=template,
        context={
            "user": user,
            "csrf_token": csrf_token(request),
            "notice": request.query_params.get("notice"),
            **context,
        },
    )


def require_user(request: Request) -> dict[str, Any] | RedirectResponse:
    user = current_user(request)
    return user if user else RedirectResponse("/login", status_code=303)


def require_role(
    user: dict[str, Any], *roles: str
) -> bool:
    return user["role"] in roles


def notice_redirect(path: str, message: str) -> RedirectResponse:
    from urllib.parse import urlencode

    separator = "&" if "?" in path else "?"
    return RedirectResponse(
        f"{path}{separator}{urlencode({'notice': message})}",
        status_code=303,
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return page(request, "login.html", error=request.query_params.get("error"))


@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf: str = Form(...),
):
    if not valid_csrf(request, csrf):
        return page(request, "login.html", error="The form expired. Please try again.")
    user = db.authenticate(settings.database_path, username, password)
    if user is None:
        db.audit(settings.database_path, None, "auth.failed", f"Failed login for {username[:80]}")
        return page(request, "login.html", error="Invalid username or password.")
    request.session.clear()
    request.session["user_id"] = user["id"]
    csrf_token(request)
    db.audit(settings.database_path, user["id"], "auth.login", "Successful login.")
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
async def logout(request: Request, csrf: str = Form(...)):
    user = current_user(request)
    if not valid_csrf(request, csrf):
        return notice_redirect("/", "Form expired")
    if user:
        db.audit(settings.database_path, user["id"], "auth.logout", "User logged out.")
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    return page(
        request,
        "dashboard.html",
        user,
        stats=db.statistics(settings.database_path),
        jobs=db.list_jobs(settings.database_path),
        sources=db.list_sources(settings.database_path, enabled_only=True),
        categories=sorted(CATEGORIES),
    )


@app.post("/jobs")
async def create_collection_job(
    request: Request,
    csrf: str = Form(...),
    query: str = Form(""),
    source_id: str = Form(""),
    target_url: str = Form(""),
    category: str = Form("other"),
):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not require_role(user, "admin", "analyst"):
        return notice_redirect("/", "You do not have permission")
    if not valid_csrf(request, csrf):
        return notice_redirect("/", "Form expired")
    if category not in CATEGORIES:
        return notice_redirect("/", "Invalid category")

    selected_source_id: int | None = None
    normalized_url: str | None = None
    try:
        if source_id:
            selected_source_id = int(source_id)
            source = db.get_source(settings.database_path, selected_source_id)
            if source is None or not source["enabled"]:
                raise ValueError("Select an enabled source.")
            if target_url.strip():
                raise ValueError("Choose a registered source or a direct URL, not both.")
        elif target_url.strip():
            normalized_url = normalize_url(target_url)
        else:
            raise ValueError("Choose a registered source or enter a URL.")
    except (ValueError, TypeError) as exc:
        return notice_redirect("/", str(exc))

    job_id = db.create_job(
        settings.database_path,
        user["id"],
        query,
        selected_source_id,
        normalized_url,
        category,
    )
    db.audit(settings.database_path, user["id"], "collection.created", f"Job {job_id} queued.")
    return RedirectResponse("/?notice=Collection+queued", status_code=303)


@app.get("/records", response_class=HTMLResponse)
async def records_page(
    request: Request,
    q: str = "",
    category: str = "",
):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if category and category not in CATEGORIES:
        category = ""
    return page(
        request,
        "records.html",
        user,
        records=db.list_records(settings.database_path, q, category),
        q=q,
        selected_category=category,
        categories=sorted(CATEGORIES),
    )


@app.post("/records/{record_id}/review")
async def review_record(
    request: Request,
    record_id: int,
    csrf: str = Form(...),
    verification_status: str = Form(...),
):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not require_role(user, "admin", "analyst"):
        return notice_redirect("/records", "You do not have permission")
    if not valid_csrf(request, csrf):
        return notice_redirect("/records", "Form expired")
    if verification_status not in {"unreviewed", "source_checked", "uncertain"}:
        return notice_redirect("/records", "Invalid review status")
    changed = db.update_record_verification(
        settings.database_path,
        record_id,
        user["id"],
        verification_status,
    )
    if not changed:
        return notice_redirect("/records", "Record not found")
    db.audit(
        settings.database_path,
        user["id"],
        "record.reviewed",
        f"Record {record_id} set to {verification_status}.",
    )
    return notice_redirect("/records", "Review status updated")


@app.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if not require_role(user, "admin"):
        return RedirectResponse("/?notice=Administrator+access+required", status_code=303)
    return page(
        request,
        "sources.html",
        user,
        sources=db.list_sources(settings.database_path),
        categories=sorted(CATEGORIES),
    )


@app.post("/sources")
async def add_source(
    request: Request,
    csrf: str = Form(...),
    name: str = Form(...),
    url: str = Form(...),
    kind: str = Form(...),
    category: str = Form(...),
):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not require_role(user, "admin"):
        return notice_redirect("/", "Administrator access required")
    if not valid_csrf(request, csrf):
        return notice_redirect("/sources", "Form expired")
    try:
        normalized = normalize_url(url)
        if not name.strip() or len(name) > 120:
            raise ValueError("Source name must be between 1 and 120 characters.")
        if kind not in {"web", "feed"} or category not in CATEGORIES:
            raise ValueError("Choose a valid source type and category.")
        source_id = db.create_source(
            settings.database_path,
            name,
            normalized,
            kind,
            category,
            user["id"],
        )
    except ValueError as exc:
        return notice_redirect("/sources", str(exc))
    except Exception as exc:
        if "UNIQUE constraint failed" not in str(exc):
            raise
        return notice_redirect("/sources", "That source URL is already registered")
    db.audit(settings.database_path, user["id"], "source.created", f"Source {source_id} created.")
    return RedirectResponse("/sources?notice=Source+added", status_code=303)


@app.post("/sources/{source_id}/toggle")
async def toggle_source(
    request: Request,
    source_id: int,
    csrf: str = Form(...),
):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not require_role(user, "admin"):
        return notice_redirect("/", "Administrator access required")
    if not valid_csrf(request, csrf):
        return notice_redirect("/sources", "Form expired")
    changed = db.toggle_source(settings.database_path, source_id)
    if changed:
        db.audit(settings.database_path, user["id"], "source.toggled", f"Source {source_id} toggled.")
    return notice_redirect("/sources", "Source updated")


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    return page(
        request,
        "analytics.html",
        user,
        stats=db.statistics(settings.database_path),
        audit_events=db.list_audit_events(settings.database_path)
        if user["role"] == "admin"
        else [],
    )


@app.get("/export.csv")
async def export_csv(request: Request, q: str = "", category: str = ""):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if category and category not in CATEGORIES:
        category = ""
    records = db.list_records(settings.database_path, q, category, limit=10_000)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "title",
            "summary",
            "category",
            "source_url",
            "canonical_url",
            "published_at",
            "collected_at",
            "extraction_confidence",
            "verification_status",
            "reviewer",
            "reviewed_at",
            "entities",
        ]
    )
    for record in records:
        writer.writerow(
            [
                safe_csv_cell(record["title"]),
                safe_csv_cell(record["summary"]),
                safe_csv_cell(record["category"]),
                safe_csv_cell(record["source_url"]),
                safe_csv_cell(record["canonical_url"]),
                safe_csv_cell(record["published_at"] or ""),
                safe_csv_cell(record["collected_at"]),
                record["extraction_confidence"],
                record["verification_status"],
                safe_csv_cell(record["reviewer"] or ""),
                safe_csv_cell(record["reviewed_at"] or ""),
                safe_csv_cell("; ".join(f"{entity['text']} ({entity['label']})" for entity in record["entities"])),
            ]
        )
    db.audit(settings.database_path, user["id"], "records.exported", f"Exported {len(records)} records.")
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="scrapz-records.csv"'},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
