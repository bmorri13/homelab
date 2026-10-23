#!/usr/bin/env python3
"""Login shim between ToolHive and Cribl's MCP server.

Cribl's MCP server (Leader only, /api/v1/mcp) takes a bearer session token from
/api/v1/auth/login that expires after an hour, so a static header can't be used.
This shim logs in with a Cribl user's credentials, keeps the token fresh (renewed
before its JWT expiry, and once more on a 401), and forwards MCP requests.

Only /mcp is forwarded, to /api/v1/mcp: the session is a Cribl admin (the free
license has no other role), and nothing else of the Cribl API is reachable through
here. Cribl's cert is verified against CRIBL_CA_FILE. Tokens and passwords are never
logged. Standard library only, so it runs on a stock Python image.

Environment:
  CRIBL_URL       Leader base URL, e.g. https://192.168.4.11:9000
  CRIBL_USERNAME  Cribl user
  CRIBL_PASSWORD  its password
  CRIBL_CA_FILE   CA bundle for the Leader's cert (default: system store)
  PORT            listen port (default 8080)
"""

import base64
import http.client
import http.server
import json
import os
import ssl
import sys
import threading
import time
import urllib.parse

UPSTREAM = urllib.parse.urlsplit(os.environ["CRIBL_URL"])
USERNAME = os.environ["CRIBL_USERNAME"]
PASSWORD = os.environ["CRIBL_PASSWORD"]
PORT = int(os.environ.get("PORT", "8080"))
MCP_PATH = "/api/v1/mcp"
LOGIN_PATH = "/api/v1/auth/login"
REFRESH_MARGIN = 300          # renew this many seconds before the token expires
FALLBACK_TTL = 1800           # if the token's exp can't be read
TIMEOUT = 300                 # seconds; search tools can be slow
MAX_BODY = 4 * 1024 * 1024

# Headers passed through in each direction. Everything else is dropped, notably
# the client's Authorization (replaced) and any cookies.
REQUEST_HEADERS = ("content-type", "accept", "mcp-session-id", "mcp-protocol-version", "last-event-id")
RESPONSE_HEADERS = ("content-type", "content-length", "mcp-session-id", "mcp-protocol-version", "cache-control")

if UPSTREAM.scheme != "https":
    sys.exit("CRIBL_URL must be https")
TLS = ssl.create_default_context(cafile=os.environ.get("CRIBL_CA_FILE") or None)


def log(msg):
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}", file=sys.stderr, flush=True)


def connect():
    # TLS is ssl.create_default_context(): certificate and hostname are verified.
    return http.client.HTTPSConnection(  # nosemgrep: python.lang.security.audit.httpsconnection-detected.httpsconnection-detected
        UPSTREAM.hostname, UPSTREAM.port or 443, context=TLS, timeout=TIMEOUT)


def jwt_exp(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, ValueError):
        return None


class Session:
    """The current Cribl session token, renewed under a lock."""

    def __init__(self):
        self._lock = threading.Lock()
        self._token = None
        self._expires = 0.0

    def token(self, stale=None):
        """A valid token. Pass the token that just got a 401 to force a new login."""
        with self._lock:
            if (self._token is None or self._token == stale
                    or time.time() > self._expires - REFRESH_MARGIN):
                self._login()
            return self._token

    def _login(self):
        body = json.dumps({"username": USERNAME, "password": PASSWORD})
        for attempt in range(5):
            conn = connect()
            try:
                conn.request("POST", LOGIN_PATH, body, {"Content-Type": "application/json"})
                resp = conn.getresponse()
                data = resp.read()
            finally:
                conn.close()
            if resp.status == 429:  # logins are rate-limited (2/second)
                time.sleep(1 + attempt)
                continue
            if resp.status != 200:
                raise RuntimeError(f"Cribl login failed: HTTP {resp.status}")
            token = json.loads(data)["token"]
            self._token = token
            self._expires = jwt_exp(token) or time.time() + FALLBACK_TTL
            log(f"logged in to Cribl as {USERNAME}; token valid for {int(self._expires - time.time())}s")
            return
        raise RuntimeError("Cribl login failed: rate-limited")


SESSION = Session()


class Handler(http.server.BaseHTTPRequestHandler):
    # HTTP/1.0: the connection closes after each response, so a streamed (SSE)
    # body needs no Content-Length or chunked encoding.
    protocol_version = "HTTP/1.0"
    server_version = "cribl-mcp-auth"
    sys_version = ""

    def do_GET(self):
        if self.path == "/health":
            return self._reply(200, b"ok\n")
        self._proxy()

    def do_POST(self):
        self._proxy()

    def do_DELETE(self):
        self._proxy()

    def _reply(self, status, body, ctype="text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self):
        if urllib.parse.urlsplit(self.path).path != "/mcp":
            return self._reply(404, b"not found\n")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._reply(413, b"request too large\n")
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() in REQUEST_HEADERS}

        conn = None
        try:
            token = SESSION.token()
            for attempt in (1, 2):
                conn = connect()
                conn.request(self.command, MCP_PATH, body, {**headers, "Authorization": f"Bearer {token}"})
                resp = conn.getresponse()
                if resp.status != 401 or attempt == 2:
                    break
                resp.read()
                conn.close()
                token = SESSION.token(stale=token)

            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() in RESPONSE_HEADERS:
                    self.send_header(k, v)
            self.end_headers()
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (OSError, http.client.HTTPException, RuntimeError, ValueError) as exc:
            log(f"{self.command} /mcp failed: {type(exc).__name__}: {exc}")
            if not self.wfile.closed:
                try:
                    self._reply(502, b"upstream error\n")
                except OSError:
                    pass
        finally:
            if conn is not None:
                conn.close()

    def log_message(self, fmt, *args):
        # Method, path and status only; never headers.
        log(f"{self.address_string()} {fmt % args}")


def main():
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    log(f"forwarding :{PORT}/mcp to {UPSTREAM.scheme}://{UPSTREAM.netloc}{MCP_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
