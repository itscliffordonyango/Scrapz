import unittest

from app.urls import normalize_url


class UrlTests(unittest.TestCase):
    def test_normalizes_and_removes_tracking_parameters(self):
        self.assertEqual(
            normalize_url(" HTTPS://Example.org/path?utm_source=x&keep=1#section "),
            "https://example.org/path?keep=1",
        )

    def test_rejects_non_http_schemes_and_local_destinations(self):
        for url in (
            "file:///etc/passwd",
            "http://127.0.0.1/private",
            "http://localhost/admin",
            "http://example.org:8080/path",
            "https://example.org:80/path",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_url(url)

    def test_preserves_public_ipv6_literal_brackets(self):
        self.assertEqual(
            normalize_url("https://[2606:4700:4700::1111]/"),
            "https://[2606:4700:4700::1111]/",
        )


if __name__ == "__main__":
    unittest.main()
