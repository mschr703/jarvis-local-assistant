"""Guard every browser request before Chromium sends it."""

import unittest
from unittest.mock import patch

from pilot import public_host
from read_page import request_allowed


class RequestGuardTests(unittest.TestCase):
    def test_private_dns_answer_is_rejected(self):
        with patch("pilot.socket.getaddrinfo", return_value=[
            (None, None, None, None, ("127.0.0.1", 443)),
        ]), self.assertRaisesRegex(ValueError, "Private"):
            public_host("https://example.com/page")

    def test_only_exact_https_host_and_standard_port(self):
        host = "example.com"
        self.assertTrue(request_allowed("https://example.com/a.js", host))
        self.assertTrue(request_allowed("https://example.com:443/a.css", host))
        for url in (
            "http://example.com/a.js", "https://example.com:444/a.js",
            "https://example.com.evil.test/a.js", "https://127.0.0.1/private",
            "https://user:pass@example.com/private", "file:///etc/passwd",
            "https://example.com:bad/path",
        ):
            with self.subTest(url=url):
                self.assertFalse(request_allowed(url, host))


if __name__ == "__main__":
    unittest.main()
