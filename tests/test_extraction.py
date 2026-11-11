import unittest

from app.extraction import classify, parse_feed, parse_html


class ExtractionTests(unittest.TestCase):
    def test_html_extracts_attributed_fields_and_normalizes_tracking_url(self):
        record = parse_html(
            """<html><head><title>Climate research update</title>
            <meta name="description" content="New climate research findings">
            <meta property="article:published_time" content="2025-03-04T10:00:00Z">
            <link rel="canonical" href="/reports/1?utm_source=mail"></head>
            <body><nav>Navigation text</nav><main><p>Report excerpt.</p></main></body></html>""",
            "https://example.org/story",
        )
        self.assertEqual(record.title, "Climate research update")
        self.assertEqual(record.category, "science")
        self.assertEqual(record.canonical_url, "https://example.org/reports/1")
        self.assertEqual(record.published_at, "2025-03-04T10:00:00+00:00")
        self.assertNotIn("Navigation text", record.summary)
        self.assertGreater(record.extraction_confidence, 0.5)

    def test_rss_extracts_entries_and_dates(self):
        xml = b"""<?xml version="1.0"?><rss version="2.0"><channel>
        <item><title>Agency report</title><link>https://example.org/report</link>
        <description>Government agency findings</description><pubDate>Tue, 04 Mar 2025 10:00:00 GMT</pubDate></item>
        </channel></rss>"""
        records = parse_feed(xml, "https://example.org/feed.xml", "other")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].category, "government")
        self.assertEqual(records[0].canonical_url, "https://example.org/report")
        self.assertEqual(records[0].published_at, "2025-03-04T10:00:00+00:00")

    def test_source_category_is_fallback_only(self):
        self.assertEqual(classify("Hospital medical update", "", "technology"), "health")
        self.assertEqual(classify("Public update", "", "government"), "government")


if __name__ == "__main__":
    unittest.main()
