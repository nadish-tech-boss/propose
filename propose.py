#!/usr/bin/env python3
"""
Propose: share the "I Love You" page with a temporary Cloudflare link and watch it live.

  Windows:  propose.bat            (or: py propose.py)
  Linux:    ./propose.sh           (or: python3 propose.py)

Options:
  --port N    local port to serve on (default 8787)
  --local     don't create a public link; serve on this computer only
  -y, --yes   download cloudflared without asking (if it isn't installed)

Only the Python standard library is used. The public link comes from Cloudflare's free
"quick tunnel" (https://<random>.trycloudflare.com); it lives as long as this window stays open.
"""
import argparse
import collections
import json
import math
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "index.html")
BIN = os.path.join(HERE, "bin")
URL_RE = re.compile(r"https://[-a-z0-9]+\.trycloudflare\.com")


# ----------------------------------------------------------------------------- console
def setup_console():
    if os.name == "nt":
        os.system("")  # turns on ANSI colour handling in Windows 10/11 consoles
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


COLOR = bool(sys.stdout.isatty() or os.environ.get("FORCE_COLOR")) and not os.environ.get("NO_COLOR")
# 24-bit colour: Windows 10/11 consoles and most modern Linux terminals; others get the closest 256-colour shade.
TRUECOLOR = os.name == "nt" or os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit") \
    or bool(os.environ.get("WT_SESSION") or os.environ.get("FORCE_COLOR"))
ANIMATE = sys.stdout.isatty()
# Emoji render in Windows Terminal and Linux terminals; the classic cmd window gets plain symbols.
FANCY = os.name != "nt" or bool(os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM"))


def paint(code, text):
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def pink(t): return tint(t, (240, 176, 164))
def rose(t): return tint(t, (255, 186, 168), True)
def dim(t): return tint(t, (140, 130, 122))
def bold(t): return paint("1", t)
def green(t): return tint(t, (156, 222, 168))
def yellow(t): return tint(t, (255, 214, 146))


SYM = {
    "open": "💌" if FANCY else "♥",
    "no": "🙈" if FANCY else "×",
    "yes": "💍" if FANCY else "♥",
    "ok": "✔" if FANCY else "+",
}

PRINT_LOCK = threading.Lock()


def say(text="", bell=False):
    with PRINT_LOCK:
        print(text + ("\a" if bell else ""), flush=True)


def stamp():
    return dim(datetime.now().strftime("%H:%M:%S"))


def clean(text, limit=80):
    """Visitor-supplied text never reaches the terminal raw (no escape-sequence tricks)."""
    return "".join(ch for ch in str(text) if ch.isprintable())[:limit]


# ----------------------------------------------------------------------------- premium rendering
RESET = "\033[0m"


def rgb_code(rgb, bg=False):
    r, g, b = (max(0, min(255, int(c))) for c in rgb)
    if TRUECOLOR:
        return f"{48 if bg else 38};2;{r};{g};{b}"
    return f"{48 if bg else 38};5;{16 + 36 * round(r / 51) + 6 * round(g / 51) + round(b / 51)}"


def tint(text, rgb, strong=False):
    if not COLOR:
        return text
    return f"\033[{'1;' if strong else ''}{rgb_code(rgb)}m{text}{RESET}"


def mix(a, b, t):
    return tuple(a[k] + (b[k] - a[k]) * t for k in range(3))


def ramp(stops, t):
    """stops: [(position 0..1, rgb), ...] in order."""
    t = min(max(t, 0.0), 1.0)
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if t <= p1:
            return mix(c0, c1, (t - p0) / ((p1 - p0) or 1))
    return stops[-1][1]


# polished metals, top -> bottom: highlight, body, deep core, glint band, base
GOLD = [(0.0, (255, 250, 224)), (0.26, (250, 216, 128)), (0.50, (204, 146, 50)),
        (0.60, (255, 234, 166)), (1.0, (164, 106, 32))]
ROSE_METAL = [(0.0, (255, 232, 214)), (0.28, (238, 164, 136)), (0.52, (160, 86, 78)),
              (0.62, (255, 204, 178)), (1.0, (112, 52, 58))]
GOLD_TEXT = [(0.0, (255, 238, 186)), (0.5, (244, 196, 112)), (1.0, (232, 160, 132))]
GOLD_EDGE = [(0.0, (150, 108, 52)), (0.5, (255, 226, 158)), (1.0, (150, 108, 52))]
CHAMPAGNE = (228, 208, 172)
MUTED = (140, 130, 122)
SOFT = (238, 232, 222)
WHITE = (255, 253, 246)
ENGRAVE = (70, 40, 50)          # the ░ shading becomes a dark engraved edge
INK = (12, 10, 10)              # what "fading out" fades towards (dark terminals)


def text_gradient(text, stops, strong=True):
    if not COLOR:
        return text
    n = max(len(text) - 1, 1)
    body = "".join(ch if ch == " " else f"\033[{rgb_code(ramp(stops, i / n))}m{ch}" for i, ch in enumerate(text))
    return ("\033[1m" if strong else "") + body + RESET


def spaced(text):
    return " ".join(text)        # luxury letter-spacing: "Y O U   A R E"


def hairline(width, jewel="♦"):
    """A thin rule that fades in from both edges, with a jewel at its centre."""
    half = max(4, (width - 3) // 2)
    if not COLOR:
        return "─" * half + f" {jewel} " + "─" * half
    left = [f"\033[{rgb_code(mix(INK, CHAMPAGNE, ((i + 1) / half) ** 1.7))}m─" for i in range(half)]
    return "".join(left) + f"\033[1;{rgb_code((255, 216, 146))}m {jewel} \033[22m" + "".join(reversed(left)) + RESET


def card(items, min_inner=0):
    """Rounded card with a gold edge. items: [(plain_text, styled_text), ...]"""
    inner = max([len(p) for p, _ in items] + [min_inner]) + 6
    tl, tr, bl, br = ("╭", "╮", "╰", "╯") if FANCY else ("┌", "┐", "└", "┘")
    if not COLOR:
        rows = ["  " + tl + "─" * inner + tr]
        rows += ["  │   " + p + " " * (inner - 3 - len(p)) + "│" for p, _ in items]
        return rows + ["  " + bl + "─" * inner + br]
    edge = lambda i: rgb_code(ramp(GOLD_EDGE, i / inner))
    def border(l, r):
        return "  " + f"\033[{edge(0)}m{l}" + "".join(f"\033[{edge(i)}m─" for i in range(inner)) + f"\033[{edge(inner)}m{r}" + RESET
    rows = [border(tl, tr)]
    for plain, styled in items:
        rows.append("  " + f"\033[{edge(0)}m│{RESET}   " + styled + " " * (inner - 3 - len(plain)) + f"\033[{edge(inner)}m│{RESET}")
    return rows + [border(bl, br)]


def centered(plain_len, width):
    return " " * max(0, (width - plain_len) // 2)


# ---- compact pixel lettering: 2 pixels per character cell, fits one 80-column line ----
PIX = {
    "C": [".##.", "#..#", "#...", "#...", "#..#", ".##."],
    "O": [".##.", "#..#", "#..#", "#..#", "#..#", ".##."],
    "N": ["#..#", "##.#", "##.#", "#.##", "#.##", "#..#"],
    "G": [".##.", "#..#", "#...", "#.##", "#..#", ".###"],
    "R": ["###.", "#..#", "#..#", "###.", "#.#.", "#..#"],
    "A": [".##.", "#..#", "#..#", "####", "#..#", "#..#"],
    "T": ["####", ".##.", ".##.", ".##.", ".##.", ".##."],
    "U": ["#..#", "#..#", "#..#", "#..#", "#..#", ".##."],
    "L": ["#...", "#...", "#...", "#...", "#...", "####"],
    "I": ["###", ".#.", ".#.", ".#.", ".#.", "###"],
    "S": [".###", "#...", ".##.", "...#", "...#", "###."],
}


def pixel_word(word, gap=1):
    rows = [""] * 6
    for i, ch in enumerate(word):
        for r in range(6):
            rows[r] += ("." * gap if i else "") + PIX[ch][r]
    return rows


def render_pixels(bitmap, color_at, indent):
    """'▀' with its own top (foreground) and bottom (background) colour = two smooth pixels per cell."""
    H, W = len(bitmap), len(bitmap[0])
    out = []
    for r in range(0, H, 2):
        top, bot = bitmap[r], (bitmap[r + 1] if r + 1 < H else "." * W)
        cells = []
        for x in range(W):
            t, b = top[x] == "#", bot[x] == "#"
            if not COLOR:
                cells.append("█" if t and b else "▀" if t else "▄" if b else " ")
            elif t and b:
                cells.append(f"\033[{rgb_code(color_at(x, r))};{rgb_code(color_at(x, r + 1), bg=True)}m▀\033[49m")
            elif t:
                cells.append(f"\033[{rgb_code(color_at(x, r))}m▀")
            elif b:
                cells.append(f"\033[{rgb_code(color_at(x, r + 1))}m▄")
            else:
                cells.append(" ")
        out.append(" " * indent + "".join(cells) + (RESET if COLOR else ""))
    return out


def metal(stops, x, y, W, H, shine=None, warm=0.0):
    """Polished-metal colour at a point, with an optional diagonal light sweep."""
    c = ramp(stops, y / max(H - 1, 1))
    if warm:
        c = mix(c, (255, 176, 156), warm * x / max(W, 1))
    if shine is not None:
        d = x / max(W, 1) + 0.3 * y / max(H, 1)
        k = math.exp(-((d - shine) / 0.06) ** 2)
        c = mix(c, WHITE, 0.88 * k)
    return c


TWINKLE = ["·", "✧", "✦", "✧", "·"] if FANCY else [".", "+", "*", "+", "."]


def sparkle_row(width, seeds, frame, final):
    if not COLOR:
        return ""
    cells = [" "] * width
    for x, phase, speed in seeds:
        k = 0.5 if final else (frame * speed + phase) % 1.0
        glow = math.sin(math.pi * k)
        if final and phase > 0.55:
            continue
        cells[x] = f"\033[{rgb_code(mix(INK, (255, 236, 190), 0.25 + 0.75 * glow))}m{TWINKLE[min(4, int(k * 5))]}"
    return "".join(cells) + RESET


def play(build, frames, delay=0.038, width=0):
    """Print a block, then (on a real terminal) redraw it in place frame by frame."""
    if not (COLOR and ANIMATE) or width >= term_columns():      # wrapped rows would break the redraw
        with PRINT_LOCK:
            print("\n".join(build(frames, True)), flush=True)
        return
    with PRINT_LOCK:
        out = sys.stdout
        try:
            out.write("\033[?25l")
            rows = build(0, False)
            out.write("".join(r + "\033[K\n" for r in rows))
            out.flush()
            for f in range(1, frames + 1):
                time.sleep(delay)
                rows = build(f, f == frames)
                out.write(f"\033[{len(rows)}F" + "".join(r + "\033[K\n" for r in rows))
                out.flush()
        finally:
            out.write("\033[?25h")
            out.flush()


# ---- the header -------------------------------------------------------------------------
ART_PROPOSE = [
    ' ███████████',
    '░░███░░░░░███',
    ' ░███    ░███ ████████   ██████  ████████   ██████   █████   ██████',
    ' ░██████████ ░░███░░███ ███░░███░░███░░███ ███░░███ ███░░   ███░░███',
    ' ░███░░░░░░   ░███ ░░░ ░███ ░███ ░███ ░███░███ ░███░░█████ ░███████',
    ' ░███         ░███     ░███ ░███ ░███ ░███░███ ░███ ░░░░███░███░░░',
    ' █████        █████    ░░██████  ░███████ ░░██████  ██████ ░░██████',
    '░░░░░        ░░░░░      ░░░░░░   ░███░░░   ░░░░░░  ░░░░░░   ░░░░░░',
    '                                 ░███',
    '                                 █████',
    '                                ░░░░░',
]


def show_header():
    art = ART_PROPOSE
    W, H, body = max(map(len, art)), len(art), 8      # rows 0-7 are the letters, the rest is the "p" tail
    def build(f, final):
        shine = None if final else -0.25 + 1.6 * f / 26
        rows = []
        for y, line in enumerate(art):
            if not COLOR:
                rows.append("  " + line)
                continue
            cells = []
            for x, ch in enumerate(line):
                if ch == " ":
                    cells.append(" ")
                elif ch == "░":
                    cells.append(f"\033[{rgb_code(mix(ENGRAVE, ramp(ROSE_METAL, min(y / body, 1)), .16))}m░")
                else:
                    cells.append(f"\033[{rgb_code(metal(ROSE_METAL, x, min(y, body - 1), W, body, shine))}m█")
            rows.append("  " + "".join(cells) + RESET)
        return rows
    play(build, 26, width=W + 2)
    tag = spaced("share it · then watch it happen")
    say("  " + hairline(W))
    say("  " + centered(len(tag), W) + tint(tag, CHAMPAGNE))
    say()


# ---- the celebration ----------------------------------------------------------------------
def show_celebration(when, tries):
    word = pixel_word("CONGRATULATIONS")
    W, H = len(word[0]), len(word)
    cols = term_columns()
    if W + 4 > cols:                                   # absurdly narrow window: elegant text only
        say("  " + text_gradient(spaced("CONGRATULATIONS"), GOLD_TEXT))
    else:
        indent = max(2, (min(cols, 100) - W) // 2)
        rnd = __import__("random").Random(14)
        top = [(rnd.randrange(W), rnd.random(), .05 + rnd.random() * .07) for _ in range(16)]
        bot = [(rnd.randrange(W), rnd.random(), .05 + rnd.random() * .07) for _ in range(12)]
        frames = 32
        def build(f, final):
            shine = None if final else -0.3 + 1.7 * f / frames
            rows = [" " * indent + sparkle_row(W, top, f, final)]
            rows += render_pixels(word, lambda x, y: metal(GOLD, x, y, W, H, shine, warm=.2), indent)
            mirrored = [word[-1], word[-2]]            # a faint reflection, as if on polished black glass
            rows += render_pixels(mirrored, lambda x, y: mix(INK, ramp(GOLD, .55), .26 if y == 0 else .1), indent)
            rows.append(" " * indent + sparkle_row(W, bot, f, final))
            return rows
        play(build, frames, width=indent + W)
        line = spaced("YOU ARE COMMITTED NOW")
        ring = SYM["yes"]
        msg = f"{ring}   {line}   {ring}"
        say(" " * indent + centered(len(line) + 10, W) + text_gradient(msg, GOLD_TEXT))
        note = f"they said yes at {when}" + (f"  ·  after {tries} {'try' if tries == 1 else 'tries'} at the No button" if tries else "")
        say(" " * indent + centered(len(note), W) + tint(note, MUTED))
        return
    say("  " + text_gradient(f"{SYM['yes']}  you are committed now  {SYM['yes']}", GOLD_TEXT))


def term_columns():
    return shutil.get_terminal_size((200, 40)).columns
# ----------------------------------------------------------------------------- visitors
def describe(ua):
    ua = ua or ""
    if "iPhone" in ua: device = "iPhone"
    elif "iPad" in ua: device = "iPad"
    elif "Android" in ua: device = "Android"
    elif "CrOS" in ua: device = "Chromebook"
    elif "Windows" in ua: device = "Windows"
    elif "Mac OS X" in ua or "Macintosh" in ua: device = "Mac"
    elif "Linux" in ua: device = "Linux"
    else: device = "unknown device"
    if "Edg/" in ua: browser = "Edge"
    elif "OPR/" in ua or "Opera" in ua: browser = "Opera"
    elif "SamsungBrowser" in ua: browser = "Samsung Internet"
    elif "FxiOS" in ua or "Firefox/" in ua: browser = "Firefox"
    elif "CriOS" in ua or "Chrome/" in ua: browser = "Chrome"
    elif "Safari/" in ua: browser = "Safari"
    else: browser = "a browser"
    return f"{device} · {browser}"


class Tracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.visitors = {}

    def handle(self, event, headers):
        kind = event.get("type")
        vid = clean(event.get("v", ""), 16) or "anon"
        try:
            tries = max(0, min(10000, int(event.get("n") or 0)))
        except (TypeError, ValueError):
            tries = 0
        with self.lock:
            v = self.visitors.get(vid)
            if v is None:
                v = self.visitors[vid] = {"n": len(self.visitors) + 1, "opens": 0, "no": 0, "no_printed": 0.0, "yes": False}
            who = rose(f"visitor #{v['n']}")
            if kind == "open":
                v["opens"] += 1
                where = clean(headers.get("Cf-Ipcountry", ""), 3)
                info = describe(headers.get("User-Agent", "")) + (f" · {where}" if where and where != "XX" else "")
                what = "opened your link!" if v["opens"] == 1 else "opened it again"
                say(f"  {stamp()}   {SYM['open']}  {who}  {tint(what, SOFT, True)}   {dim(info)}", bell=v["opens"] == 1)
            elif kind == "no":
                v["no"] = max(v["no"], tries)
                now = time.monotonic()
                if now - v["no_printed"] > 2.5:
                    v["no_printed"] = now
                    count = f"{v['no']} {'try' if v['no'] == 1 else 'tries'}"
                    say(f"  {stamp()}   {SYM['no']}  {who}  {tint('is trying to press', SOFT)} {yellow('No')}   {dim(count + ' · it keeps running away')}")
            elif kind == "yes" and not v["yes"]:
                v["yes"] = True
                v["no"] = max(v["no"], tries)
                self.celebrate(v)

    def celebrate(self, v):
        when = datetime.now().strftime("%H:%M:%S")
        say()
        say(f"  {stamp()}   {SYM['yes']}  {rose('visitor #' + str(v['n']))}  {tint('pressed YES', (255, 226, 160), True)}", bell=True)
        say()
        show_celebration(when, v["no"])
        say()


TRACKER = Tracker()


# ----------------------------------------------------------------------------- web server
class Handler(BaseHTTPRequestHandler):
    server_version = "Propose/1.0"
    sys_version = ""

    def log_message(self, *args):  # keep the terminal for the good news only
        pass

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", head=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if body and not head:
            self.wfile.write(body)

    def _route(self, head=False):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                with open(PAGE, "rb") as f:
                    body = f.read()
            except OSError:
                return self._send(500, b"index.html is missing next to propose.py", head=head)
            return self._send(200, body, "text/html; charset=utf-8", head=head)
        if path == "/favicon.ico":
            return self._send(204, head=head)
        return self._send(404, b"not found", head=head)

    def do_GET(self):
        self._route()

    def do_HEAD(self):
        self._route(head=True)

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/event":
            return self._send(404, b"not found")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 4096:
            return self._send(413 if length > 4096 else 400)
        try:
            event = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(event, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            return self._send(400)
        TRACKER.handle(event, self.headers)
        self._send(204)


def start_server(port):
    for p in range(port, port + 25):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, daemon=True).start()
            return server, p
        except OSError:
            continue
    raise SystemExit(f"Could not find a free port near {port}. Try: --port 9000")


# ----------------------------------------------------------------------------- cloudflared
def asset_name():
    machine = platform.machine().lower()
    system = platform.system()
    if system == "Windows":
        return "cloudflared-windows-386.exe" if machine in ("x86", "i386", "i686") else "cloudflared-windows-amd64.exe"
    if system == "Linux":
        return {
            "x86_64": "cloudflared-linux-amd64", "amd64": "cloudflared-linux-amd64",
            "aarch64": "cloudflared-linux-arm64", "arm64": "cloudflared-linux-arm64",
            "armv7l": "cloudflared-linux-arm", "armv6l": "cloudflared-linux-arm",
            "i386": "cloudflared-linux-386", "i686": "cloudflared-linux-386",
        }.get(machine)
    return None


def find_cloudflared(auto_yes):
    found = shutil.which("cloudflared")
    if found:
        return found
    local = os.path.join(BIN, "cloudflared.exe" if os.name == "nt" else "cloudflared")
    if os.path.isfile(local):
        return local
    asset = asset_name()
    if not asset:
        say(f"  {yellow('cloudflared is needed for the public link.')} Install it, then run this again:")
        say("    macOS:  brew install cloudflared")
        say("    other:  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")
        return None
    url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/{asset}"
    say(f"  {yellow('cloudflared')} (Cloudflare's free tunnel tool) isn't installed yet.")
    say(f"  It can be downloaded from Cloudflare's official GitHub releases (~40 MB):")
    say(f"    {dim(url)}")
    if not auto_yes:
        try:
            answer = input("  Download it into ./bin now? [Y/n] ").strip().lower()
        except EOFError:
            answer = "n"
        if answer not in ("", "y", "yes"):
            say("  Okay. Install cloudflared yourself, or run with --local to test on this computer.")
            return None
    os.makedirs(BIN, exist_ok=True)
    part = local + ".part"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, open(part, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total and COLOR:
                    with PRINT_LOCK:
                        print(f"\r  downloading… {done * 100 // total:3d}%", end="", flush=True)
        if COLOR:
            print("\r" + " " * 30 + "\r", end="", flush=True)
        os.replace(part, local)
        if os.name != "nt":
            os.chmod(local, os.stat(local).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        say(f"  {green(SYM['ok'])} cloudflared downloaded to {dim(local)}")
        return local
    except Exception as exc:
        try:
            os.remove(part)
        except OSError:
            pass
        say(f"  Download failed: {exc}")
        say("  Install cloudflared manually: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")
        return None


def start_tunnel(cloudflared, port):
    cmd = [cloudflared, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    tail = collections.deque(maxlen=30)
    found = {"url": None}
    ready = threading.Event()

    def pump():
        for line in proc.stdout:
            tail.append(line.rstrip())
            if not found["url"]:
                m = URL_RE.search(line)
                if m:
                    found["url"] = m.group(0)
                    ready.set()
        ready.set()  # process ended

    threading.Thread(target=pump, daemon=True).start()
    ready.wait(60)
    return proc, found["url"], tail


def wait_until_live(url, seconds=45):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "propose-healthcheck"})
            with urllib.request.urlopen(req, timeout=6) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Share the proposal page with a temporary Cloudflare link and watch it live.")
    ap.add_argument("--port", type=int, default=8787, help="local port (default 8787)")
    ap.add_argument("--local", action="store_true", help="no public link; serve on this computer only")
    ap.add_argument("-y", "--yes", action="store_true", help="download cloudflared without asking")
    args = ap.parse_args()

    setup_console()
    if not os.path.isfile(PAGE):
        raise SystemExit("index.html must sit next to propose.py")

    say()
    show_header()

    server, port = start_server(args.port)
    local_url = f"http://127.0.0.1:{port}"
    proc = None

    def shutdown(*_):
        raise KeyboardInterrupt

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    try:
        if args.local:
            say(f"  {dim(spaced('LOCAL'))}    {tint(local_url, SOFT, True)}")
            say("  " + dim("open it in a browser on this computer to try everything out"))
        else:
            cloudflared = find_cloudflared(args.yes)
            if not cloudflared:
                say(f"  serving locally instead: {bold(local_url)}")
            else:
                say(dim("  starting a temporary Cloudflare link…"))
                proc, url, tail = start_tunnel(cloudflared, port)
                if not url:
                    say(f"  {yellow('Cloudflare did not hand out a link.')} Last messages from cloudflared:")
                    for line in list(tail)[-8:]:
                        say("    " + dim(clean(line, 160)))
                    say(f"  serving locally instead: {bold(local_url)}")
                    if proc.poll() is None:
                        proc.terminate()
                    proc = None
                else:
                    label = spaced("SEND THIS LINK")
                    for row in card([("", ""), (label, tint(label, MUTED)), (url, tint(url, WHITE, True)), ("", "")]):
                        say(row)
                    say("  " + dim("checking the link is reachable…"))
                    if wait_until_live(url):
                        say(f"  {tint('●', (120, 222, 140))} {tint('live', (156, 222, 168), True)}   {dim('works until you close this window')}")
                    else:
                        say("  " + dim("couldn't confirm it from here yet; new links can take a minute to spread. It usually works."))
        say()
        say("  " + dim("waiting for them to open it   ·   Ctrl+C to stop sharing"))
        say()
        while True:
            time.sleep(1)
            if proc is not None and proc.poll() is not None:
                say(f"  {yellow('The Cloudflare tunnel stopped.')} Run this again for a new link.")
                break
    except KeyboardInterrupt:
        say()
        say(dim("  stopping… the link no longer works. ♥"))
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
        server.shutdown()


if __name__ == "__main__":
    main()
