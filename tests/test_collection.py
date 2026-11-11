import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.collector import _polite_read, _robots_allowed, collect


class CollectionPipelineTests(unittest.TestCase):
    def test_explicit_url_is_parsed_and_keyword_filtered(self):
        html = b"""<html><head><title>Renewable energy report</title>
        <meta name="description" content="A public summary about renewable energy.">
        </head><body><main>Longer public report content.</main></body></html>"""
        job = {
            "id": 4,
            "source_id": None,
            "source_url": "https://example.org/report",
            "category": "other",
            "query": "renewable energy",
        }
        settings = SimpleNamespace(
            database_path=Path(tempfile.gettempdir()) / "unused.sqlite",
            min_host_interval_seconds=0,
        )
        fetch = AsyncMock(
            return_value=("https://example.org/report", "text/html; charset=utf-8", html)
        )
        with patch("app.collector._fetch_document", fetch):
            records = asyncio.run(collect(settings, job))

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["job_id"], 4)
        self.assertEqual(records[0]["category"], "environment")
        self.assertEqual(records[0]["source_url"], "https://example.org/report")

    def test_nonmatching_query_returns_no_records(self):
        job = {
            "id": 5,
            "source_id": None,
            "source_url": "https://example.org/report",
            "category": "other",
            "query": "unrelated keyword",
        }
        settings = SimpleNamespace(
            database_path=Path(tempfile.gettempdir()) / "unused.sqlite",
            min_host_interval_seconds=0,
        )
        fetch = AsyncMock(
            return_value=(
                "https://example.org/report",
                "text/html",
                b"<html><title>Research report</title><body>Study summary</body></html>",
            )
        )
        with patch("app.collector._fetch_document", fetch):
            records = asyncio.run(collect(settings, job))
        self.assertEqual(records, [])


class CollectionPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_robots_disallow_is_enforced(self):
        body = b"User-agent: *\nDisallow: /private\n"
        with (
            patch("app.collector.ensure_public_host", new=AsyncMock()),
            patch(
                "app.collector._polite_read",
                new=AsyncMock(return_value=(200, httpx.Headers(), body)),
            ),
        ):
            allowed = await _robots_allowed(
                object(),
                "https://robots-policy.example/private/report",
                {},
                0,
            )
        self.assertFalse(allowed)

    async def test_same_host_requests_are_spaced(self):
        starts = []

        async def fake_read(_client, _url, _max_bytes):
            starts.append(asyncio.get_running_loop().time())
            return 200, httpx.Headers(), b"ok"

        with patch("app.collector._read_limited", side_effect=fake_read):
            await asyncio.gather(
                _polite_read(object(), "https://rate-limit.example/one", 10, 0.02),
                _polite_read(object(), "https://rate-limit.example/two", 10, 0.02),
            )
        self.assertGreaterEqual(starts[1] - starts[0], 0.018)


if __name__ == "__main__":
    unittest.main()
