"""Tests for propose.py: run with  python -m unittest discover -s tests -v"""
import io
import json
import os
import sys
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["NO_COLOR"] = "1"          # plain output: any escape code in the terminal would be an injection

import propose  # noqa: E402

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.port = propose.start_server(18787)
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, path, method="GET", body=None, headers=None):
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def post(self, event, ua=IPHONE):
        out = io.StringIO()
        with redirect_stdout(out):
            body = event if isinstance(event, bytes) else json.dumps(event).encode()
            code, _ = self.request("/api/event", "POST", body, {"Content-Type": "application/json", "User-Agent": ua})
        return code, out.getvalue()

    # -- serving ------------------------------------------------------------------
    def test_serves_the_page(self):
        code, body = self.request("/")
        self.assertEqual(code, 200)
        self.assertIn(b"<title>I Love You</title>", body)

    def test_only_the_page_is_ever_served(self):
        for path in ("/propose.py", "/..%2fpropose.py", "/../propose.py", "/.claude/launch.json",
                     "/bin/cloudflared.exe", "/tests/test_propose.py", "/README.md"):
            self.assertEqual(self.request(path)[0], 404, path)

    def test_head_request(self):
        self.assertEqual(self.request("/", "HEAD")[0], 200)

    # -- events -------------------------------------------------------------------
    def test_rejects_malformed_events(self):
        self.assertEqual(self.post(b"not json")[0], 400)
        self.assertEqual(self.post(b"[1, 2, 3]")[0], 400)
        self.assertEqual(self.post({"type": "open", "v": "x" * 5000})[0], 413)
        code, _ = self.request("/api/other", "POST", b"{}", {"Content-Type": "application/json"})
        self.assertEqual(code, 404)

    def test_open_no_yes_flow(self):
        code, out = self.post({"type": "open", "v": "flow"})
        self.assertEqual(code, 204)
        self.assertIn("opened your link", out)
        self.assertIn("iPhone · Safari", out)

        _, out = self.post({"type": "no", "v": "flow", "n": 4})
        self.assertIn("is trying to press", out)
        self.assertIn("4 tries", out)

        _, out = self.post({"type": "yes", "v": "flow", "n": 6})
        self.assertIn("pressed YES", out)
        self.assertIn("C O M M I T T E D", out)
        self.assertIn("after 6 tries", out)

        _, again = self.post({"type": "yes", "v": "flow", "n": 6})
        self.assertEqual(again, "", "a visitor is only celebrated once")

    def test_reopening_is_reported_as_again(self):
        self.post({"type": "open", "v": "twice"})
        _, out = self.post({"type": "open", "v": "twice"})
        self.assertIn("opened it again", out)

    def test_visitor_text_cannot_inject_terminal_codes(self):
        evil_ua = "Mozilla/5.0 \x1b[2J\x1b[31mHACKED \x1b]0;pwned\x07 iPhone"
        _, out = self.post({"type": "open", "v": "\x1b[2Jevil"}, ua=evil_ua)
        out = out.replace("\a\n", "\n")            # the tool's own "someone opened it" beep is intentional
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x07", out)
        self.assertNotIn("pwned", out)
        self.assertNotIn("HACKED", out, "raw user-agent text is never printed")


class UnitTests(unittest.TestCase):
    def test_describe_devices(self):
        self.assertEqual(propose.describe(IPHONE), "iPhone · Safari")
        self.assertEqual(propose.describe("Mozilla/5.0 (Linux; Android 14; Pixel 8) Chrome/120 Mobile Safari/537.36"),
                         "Android · Chrome")
        self.assertEqual(propose.describe("Mozilla/5.0 (Windows NT 10.0; Win64) Chrome/120 Safari/537.36 Edg/120"),
                         "Windows · Edge")
        self.assertEqual(propose.describe(""), "unknown device · a browser")

    def test_congratulations_fits_one_80_column_line(self):
        rows = propose.pixel_word("CONGRATULATIONS")
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({len(r) for r in rows}), 1, "all pixel rows have the same width")
        self.assertLessEqual(len(rows[0]) + 4, 80)

    def test_every_letter_glyph_is_well_formed(self):
        for ch, glyph in propose.PIX.items():
            self.assertEqual(len(glyph), 6, ch)
            self.assertEqual(len({len(r) for r in glyph}), 1, ch)

    def test_header_art_is_intact(self):
        self.assertEqual(len(propose.ART_PROPOSE), 11)
        self.assertTrue(all(set(row) <= set(" █░") for row in propose.ART_PROPOSE))

    def test_cloudflared_asset_names(self):
        cases = {
            ("Windows", "AMD64"): "cloudflared-windows-amd64.exe",
            ("Windows", "ARM64"): "cloudflared-windows-amd64.exe",
            ("Linux", "x86_64"): "cloudflared-linux-amd64",
            ("Linux", "aarch64"): "cloudflared-linux-arm64",
            ("Linux", "armv7l"): "cloudflared-linux-arm",
            ("Darwin", "arm64"): None,
        }
        for (system, machine), expected in cases.items():
            with mock.patch("platform.system", return_value=system), mock.patch("platform.machine", return_value=machine):
                self.assertEqual(propose.asset_name(), expected, (system, machine))

    def test_trycloudflare_url_detection(self):
        line = "2026-11-13T10:00:00Z INF |  https://silver-moon-promise-candle.trycloudflare.com  |"
        self.assertEqual(propose.URL_RE.search(line).group(0), "https://silver-moon-promise-candle.trycloudflare.com")

    def test_launcher_line_endings(self):
        with open(os.path.join(ROOT, "propose.sh"), "rb") as f:
            self.assertNotIn(b"\r", f.read(), "propose.sh must use LF")
        with open(os.path.join(ROOT, "propose.bat"), "rb") as f:
            data = f.read()
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), "propose.bat must use CRLF")


if __name__ == "__main__":
    unittest.main()
