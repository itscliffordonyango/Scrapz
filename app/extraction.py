import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from app.entities import extract_entities
from app.urls import normalize_url


CATEGORIES = {
    "news",
    "science",
    "government",
    "health",
    "environment",
    "technology",
    "other",
}
CATEGORY_TERMS = {
    "science": {"research", "study", "science", "scientist", "experiment"},
    "government": {"government", "department", "agency", "minister", "parliament"},
    "health": {"health", "medical", "hospital", "disease", "medicine"},
    "environment": {"climate", "environment", "wildlife", "pollution", "energy"},
    "technology": {"technology", "software", "cybersecurity", "artificial intelligence"},
    "news": {"news", "report", "breaking", "press release"},
}
DATE_PATTERN = re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b")


@dataclass(frozen=True)
class ExtractedRecord:
    source_url: str
    canonical_url: str
    title: str
    summary: str
    category: str
    published_at: str | None
    extraction_confidence: float
    entities: list[dict[str, str]]
    content_hash: str


def classify(title: str, summary: str, category_hint: str) -> str:
    text = f"{title} {summary}".casefold()
    scores = {
        category: sum(term in text for term in terms)
        for category, terms in CATEGORY_TERMS.items()
    }
    best_category = max(scores, key=scores.get)
    if scores[best_category] > 0:
        return best_category
    return category_hint if category_hint in CATEGORIES else "other"


def _clean(value: str | None, limit: int = 3000) -> str:
    if not value:
        return ""
    normalized = re.sub(r"\s+", " ", unescape(value)).strip()
    return normalized[:limit]


def _parse_date(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat(timespec="seconds")
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC).isoformat(timespec="seconds")
        except (TypeError, ValueError, OverflowError):
            return None


def _record(
    source_url: str,
    canonical_url: str,
    title: str,
    summary: str,
    published_at: str | None,
    category_hint: str,
    evidence: str,
) -> ExtractedRecord:
    title = _clean(title, 500) or canonical_url
    summary = _clean(summary, 3000)
    category = classify(title, summary, category_hint)
    confidence = min(
        0.95,
        0.35
        + (0.25 if title else 0)
        + (0.20 if summary else 0)
        + (0.10 if published_at else 0)
        + (0.05 if canonical_url else 0),
    )
    digest = hashlib.sha256(
        f"{canonical_url}\n{title}\n{summary}".encode("utf-8")
    ).hexdigest()
    return ExtractedRecord(
        source_url=source_url,
        canonical_url=canonical_url,
        title=title,
        summary=summary,
        category=category,
        published_at=published_at,
        extraction_confidence=round(confidence, 2),
        entities=extract_entities(f"{title}\n{summary}\n{evidence}"),
        content_hash=digest,
    )


def parse_html(
    html: str, source_url: str, category_hint: str = "other"
) -> ExtractedRecord:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        element.decompose()

    title = ""
    if soup.title:
        title = soup.title.get_text(" ", strip=True)
    if not title:
        heading = soup.find("h1")
        title = heading.get_text(" ", strip=True) if heading else ""

    description = ""
    for selector in (
        'meta[name="description"]',
        'meta[property="og:description"]',
        'meta[name="twitter:description"]',
    ):
        meta = soup.select_one(selector)
        if meta and meta.get("content"):
            description = str(meta["content"])
            break

    published_at = None
    for selector in (
        'meta[property="article:published_time"]',
        'meta[name="date"]',
        'meta[name="publishdate"]',
        "time[datetime]",
    ):
        element = soup.select_one(selector)
        if element:
            published_at = _parse_date(
                str(element.get("content") or element.get("datetime") or element.get_text())
            )
            if published_at:
                break

    canonical = soup.select_one('link[rel="canonical"][href]')
    canonical_url = source_url
    if canonical:
        try:
            canonical_url = normalize_url(urljoin(source_url, str(canonical["href"])))
        except ValueError:
            canonical_url = source_url
    article = soup.find("article") or soup.find("main") or soup.body or soup
    evidence = article.get_text(" ", strip=True) if article else ""
    evidence = _clean(evidence, 20_000)
    summary = _clean(description) or evidence[:700]
    return _record(
        source_url,
        canonical_url,
        title,
        summary,
        published_at,
        category_hint,
        evidence,
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_feed(
    body: bytes, feed_url: str, category_hint: str = "other", limit: int = 30
) -> list[ExtractedRecord]:
    root = ElementTree.fromstring(body)
    entries = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
    records: list[ExtractedRecord] = []
    for entry in entries[:limit]:
        fields: dict[str, list[Any]] = {}
        for child in list(entry):
            fields.setdefault(_local_name(child.tag), []).append(child)

        def text_for(*names: str) -> str:
            for name in names:
                candidates = fields.get(name, [])
                if candidates:
                    return "".join(candidates[0].itertext()).strip()
            return ""

        link = ""
        for item in fields.get("link", []):
            href = item.attrib.get("href")
            rel = item.attrib.get("rel", "alternate")
            if href and rel == "alternate":
                link = href
                break
            if item.text and item.text.strip():
                link = item.text.strip()
        if not link:
            continue
        canonical_url = normalize_url(urljoin(feed_url, link))
        title = text_for("title")
        summary = text_for("description", "summary", "content", "encoded")
        published_at = _parse_date(
            text_for("pubdate", "published", "updated", "date")
        )
        evidence = _clean(BeautifulSoup(summary, "html.parser").get_text(" ", strip=True), 20_000)
        records.append(
            _record(
                feed_url,
                canonical_url,
                title,
                evidence[:3000],
                published_at,
                category_hint,
                evidence,
            )
        )
    return records
