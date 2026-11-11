import asyncio
import logging
import re
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from app import db
from app.config import Settings
from app.extraction import ExtractedRecord, parse_feed, parse_html
from app.urls import ensure_public_host, normalize_url


logger = logging.getLogger("scrapz.collector")
USER_AGENT = "ScrapzResearch/1.0 (+public-source research; robots-aware)"
MAX_DOCUMENT_BYTES = 2_000_000
MAX_ROBOTS_BYTES = 100_000
MAX_REDIRECTS = 4
MAX_ATTEMPTS = 3
_host_locks: dict[str, asyncio.Lock] = {}
_host_next_request: dict[str, float] = {}


class CollectionError(RuntimeError):
    pass


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))


async def _read_limited(
    client: httpx.AsyncClient, url: str, max_bytes: int
) -> tuple[int, httpx.Headers, bytes]:
    try:
        async with client.stream(
            "GET",
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/atom+xml,application/rss+xml,application/xml,text/xml"},
            follow_redirects=False,
        ) as response:
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > max_bytes:
                raise CollectionError(f"Response from {url} exceeds the size limit.")
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise CollectionError(f"Response from {url} exceeds the size limit.")
                chunks.append(chunk)
            return response.status_code, response.headers, b"".join(chunks)
    except httpx.TimeoutException as exc:
        raise CollectionError(f"Request timed out for {url}.") from exc
    except httpx.RequestError as exc:
        raise CollectionError(f"Request failed for {url}: {exc}") from exc


async def _polite_read(
    client: httpx.AsyncClient, url: str, max_bytes: int, interval: float
) -> tuple[int, httpx.Headers, bytes]:
    origin = _origin(url)
    lock = _host_locks.setdefault(origin, asyncio.Lock())
    async with lock:
        loop = asyncio.get_running_loop()
        delay = _host_next_request.get(origin, 0.0) - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            return await _read_limited(client, url, max_bytes)
        finally:
            _host_next_request[origin] = loop.time() + interval


async def _robots_allowed(
    client: httpx.AsyncClient,
    url: str,
    cache: dict[str, RobotFileParser],
    interval: float,
) -> bool:
    origin = _origin(url)
    if origin not in cache:
        robots_url = origin.rstrip("/") + "/robots.txt"
        await ensure_public_host(robots_url)
        try:
            status, _, body = await _polite_read(
                client, robots_url, MAX_ROBOTS_BYTES, interval
            )
        except CollectionError:
            logger.warning("Robots policy could not be retrieved for %s; denying fetch", origin)
            return False
        parser = RobotFileParser(robots_url)
        if status in {404, 410}:
            parser.parse([])
        elif status == 200:
            parser.parse(body.decode("utf-8", errors="replace").splitlines())
        else:
            logger.warning("Robots policy returned HTTP %s for %s; denying fetch", status, origin)
            return False
        cache[origin] = parser
    return cache[origin].can_fetch(USER_AGENT, url)


async def _fetch_document(
    client: httpx.AsyncClient,
    target: str,
    cache: dict[str, RobotFileParser],
    interval: float,
) -> tuple[str, str, bytes]:
    current = normalize_url(target)
    for redirect_count in range(MAX_REDIRECTS + 1):
        await ensure_public_host(current)
        if not await _robots_allowed(client, current, cache, interval):
            raise CollectionError(f"robots.txt disallows or could not verify access to {current}.")
        status, headers, body = await _polite_read(
            client, current, MAX_DOCUMENT_BYTES, interval
        )
        if status in {301, 302, 303, 307, 308}:
            location = headers.get("location")
            if not location or redirect_count == MAX_REDIRECTS:
                raise CollectionError(f"Too many or invalid redirects from {current}.")
            from urllib.parse import urljoin

            current = normalize_url(urljoin(current, location))
            continue
        if status < 200 or status >= 300:
            raise CollectionError(f"Source returned HTTP {status}: {current}")
        return current, headers.get("content-type", "").casefold(), body
    raise CollectionError(f"Too many redirects from {target}.")


def _matches_query(record: ExtractedRecord, query: str) -> bool:
    terms = [term.casefold() for term in re.split(r"[,;\n]", query) if term.strip()]
    text = f"{record.title} {record.summary}".casefold()
    return not terms or any(term in text for term in terms)


async def collect(
    settings: Settings,
    job: dict,
) -> list[dict]:
    source_url = job["source_url"]
    category_hint = job["category"]
    if job["source_id"] is not None:
        source = db.get_source(settings.database_path, job["source_id"])
        if source is None or not source["enabled"]:
            raise CollectionError("The selected source no longer exists or is disabled.")
        source_url = source["url"]
        category_hint = source["category"]
        kind = source["kind"]
    else:
        kind = "web"
    if not source_url:
        raise CollectionError("Collection job has no source URL.")

    timeout = httpx.Timeout(12.0, connect=6.0)
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)
    cache: dict[str, RobotFileParser] = {}
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        final_url, content_type, body = await _fetch_document(
            client, source_url, cache, settings.min_host_interval_seconds
        )

    if kind == "feed" or any(
        marker in content_type for marker in ("rss", "atom", "xml")
    ):
        extracted = parse_feed(body, final_url, category_hint)
    elif "html" in content_type or not content_type:
        extracted = [parse_html(body.decode("utf-8", errors="replace"), final_url, category_hint)]
    else:
        raise CollectionError(f"Unsupported source content type: {content_type or 'unknown'}.")

    collected_at = datetime.now(UTC).isoformat(timespec="seconds")
    results = []
    for record in extracted:
        if not _matches_query(record, job["query"]):
            continue
        results.append(
            {
                "job_id": job["id"],
                "source_url": record.source_url,
                "canonical_url": record.canonical_url,
                "title": record.title,
                "summary": record.summary,
                "category": record.category,
                "published_at": record.published_at,
                "extraction_confidence": record.extraction_confidence,
                "entities": record.entities,
                "content_hash": record.content_hash,
                "collected_at": collected_at,
            }
        )
    return results


async def process_job(settings: Settings, job: dict) -> None:
    try:
        records = await collect(settings, job)
        inserted = db.add_records(settings.database_path, records)
        db.update_job(settings.database_path, job["id"], "completed", inserted)
        db.audit(
            settings.database_path,
            job["created_by"],
            "collection.completed",
            f"Job {job['id']} completed with {inserted} new records.",
        )
    except Exception as exc:
        logger.exception("Collection job %s failed on attempt %s", job["id"], job["attempts"])
        if job["attempts"] < MAX_ATTEMPTS:
            delay_seconds = 2 ** job["attempts"]
            retry_at = datetime.now(UTC).timestamp() + delay_seconds
            next_run_at = datetime.fromtimestamp(retry_at, UTC).isoformat(timespec="seconds")
            db.update_job(
                settings.database_path,
                job["id"],
                "queued",
                error=str(exc),
                next_run_at=next_run_at,
            )
        else:
            db.update_job(settings.database_path, job["id"], "failed", error=str(exc))
        db.audit(
            settings.database_path,
            job["created_by"],
            "collection.failed",
            f"Job {job['id']} attempt {job['attempts']}: {exc}",
        )


async def worker_loop(settings: Settings) -> None:
    active: set[asyncio.Task] = set()
    while True:
        active = {task for task in active if not task.done()}
        while len(active) < settings.worker_concurrency:
            job = db.claim_job(settings.database_path)
            if job is None:
                break
            task = asyncio.create_task(process_job(settings, job))
            active.add(task)
        await asyncio.sleep(0.5)
