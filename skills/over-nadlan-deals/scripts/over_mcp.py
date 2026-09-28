#!/usr/bin/env python3
"""Minimal client for the over.org.il MCP servers (deals, nadlan, data, ...).

  over_mcp.py --version
  over_mcp.py login                      # one-time Google OAuth in the browser
  over_mcp.py tools  <server>            # list tools + input schemas
  over_mcp.py call   <server> <tool> ['<json args>']

<server> is the path segment before /mcp: deals, nadlan, data, odata, cbs, knesset, ...
State (client registration + tokens) lives in ~/.config/over-mcp/state.json.
Importable: `rpc(server, method, params)` and `call_tool(server, tool, args)`;
both raise McpError. Thread-safe (token refresh is serialized).
"""
import base64, hashlib, http.server, json, os, pathlib, secrets, sys, threading, time
import urllib.error, urllib.parse, urllib.request, webbrowser

__version__ = "0.2.0"  # Semantic Versioning; keep SKILL.md metadata.version and CHANGELOG.md in sync

BASE = "https://www.over.org.il"
AUTH = BASE + "/mcp/oauth"
PORT = 53682
REDIRECT = f"http://127.0.0.1:{PORT}/callback"
STATE = pathlib.Path.home() / ".config" / "over-mcp" / "state.json"
_LOCK = threading.Lock()


class McpError(RuntimeError):
    pass


def load():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save(st):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1))
    os.chmod(STATE, 0o600)


def post(url, data, headers=None, form=False, timeout=240):
    body = urllib.parse.urlencode(data).encode() if form else json.dumps(data, ensure_ascii=False).encode()
    h = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, body, h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()
    except OSError as e:  # URLError, socket timeouts, connection resets (HTTPError is handled above)
        raise McpError(f"network error calling {url}: {e} (over.org.il can be slow; retry in a minute)") from e


def store_tokens(st, tok):
    st["access_token"] = tok["access_token"]
    st["refresh_token"] = tok.get("refresh_token", st.get("refresh_token"))
    st["expires_at"] = time.time() + int(tok.get("expires_in", 3600))
    save(st)


def login():
    st = load()
    if not st.get("client_id"):
        code, _, body = post(AUTH + "/register", {
            "client_name": "nadlan-over-deals-skill", "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "none", "scope": "mcp"})
        if code != 201:
            raise McpError(f"client registration failed: {code} {body}")
        st["client_id"] = json.loads(body)["client_id"]
        save(st)
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(12)
    url = AUTH + "/authorize?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": st["client_id"], "redirect_uri": REDIRECT,
        "code_challenge": challenge, "code_challenge_method": "S256", "scope": "mcp",
        "state": state, "resource": BASE + "/deals/mcp"})
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            p = urllib.parse.urlparse(self.path)
            if p.path == "/callback":
                got.update({k: v[0] for k, v in urllib.parse.parse_qs(p.query).items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"over.org.il login captured - you can close this tab.")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", PORT), H)
    srv.timeout = 5
    print("Opening Google sign-in (first login auto-registers the account):\n" + url, file=sys.stderr)
    webbrowser.open(url)
    deadline = time.time() + 300
    while "code" not in got and "error" not in got and time.time() < deadline:
        srv.handle_request()
    srv.server_close()
    if got.get("state") != state or "code" not in got:
        raise McpError(f"login failed or timed out: {got}")
    code, _, body = post(AUTH + "/token", {
        "grant_type": "authorization_code", "code": got["code"], "redirect_uri": REDIRECT,
        "client_id": st["client_id"], "code_verifier": verifier, "resource": BASE + "/deals/mcp"}, form=True)
    if code != 200:
        raise McpError(f"token exchange failed: {code} {body}")
    store_tokens(st, json.loads(body))
    print("logged in", file=sys.stderr)


def _refresh(st):
    if not st.get("refresh_token"):
        raise McpError("not logged in: run `over_mcp.py login`")
    code, _, body = post(AUTH + "/token", {
        "grant_type": "refresh_token", "refresh_token": st["refresh_token"],
        "client_id": st["client_id"]}, form=True)
    if code != 200:
        raise McpError(f"refresh failed ({code} {body}); run `over_mcp.py login`")
    store_tokens(st, json.loads(body))


def _token(stale=None):
    """Current access token; refreshes when expiring or when `stale` is the token that just got a 401."""
    with _LOCK:
        st = load()
        if not st.get("access_token"):
            raise McpError("not logged in: run `over_mcp.py login`")
        if st.get("expires_at", 0) - 60 < time.time() or (stale and st["access_token"] == stale):
            _refresh(st)
        return st["access_token"]


def rpc(server, method, params, timeout=240):
    msg = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    tok = _token()
    for attempt in (0, 1):
        code, hdrs, body = post(f"{BASE}/{server}/mcp", msg, {
            "Authorization": "Bearer " + tok,
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18"}, timeout=timeout)
        if code == 401 and attempt == 0:
            tok = _token(stale=tok)
            continue
        break
    if code != 200:
        raise McpError(f"HTTP {code}: {body[:500]}")
    if "event-stream" in hdrs.get("Content-Type", hdrs.get("content-type", "")):
        body = [l[5:].strip() for l in body.splitlines() if l.startswith("data:")][-1]
    res = json.loads(body)
    if "error" in res:
        raise McpError(json.dumps(res["error"], ensure_ascii=False))
    return res["result"]


def call_tool(server, tool, args):
    """Call a tool and return its parsed JSON payload (raises McpError on isError)."""
    r = rpc(server, "tools/call", {"name": tool, "arguments": args})
    txt = "".join(c.get("text", "") for c in r.get("content", []))
    if r.get("isError"):
        raise McpError(f"{server}.{tool}: {txt[:1500]}")
    try:
        return json.loads(txt)
    except ValueError:
        return txt


def main():
    a = sys.argv[1:]
    try:
        if a[:1] in (["--version"], ["-V"]):
            return print(f"nadlan {__version__}")
        if a[:1] == ["login"]:
            return login()
        if len(a) >= 2 and a[0] == "tools":
            for t in rpc(a[1], "tools/list", {})["tools"]:
                print(f"## {t['name']}\n{t.get('description', '')}\n"
                      f"input: {json.dumps(t.get('inputSchema'), ensure_ascii=False)}\n")
            return
        if len(a) >= 3 and a[0] == "call":
            args = json.loads(a[3]) if len(a) > 3 else {}
            r = rpc(a[1], "tools/call", {"name": a[2], "arguments": args})
            for c in r.get("content", []):
                txt = c.get("text", "")
                try:
                    print(json.dumps(json.loads(txt), ensure_ascii=False, indent=1))
                except ValueError:
                    print(txt)
            sys.exit(1 if r.get("isError") else 0)
    except McpError as e:
        sys.exit(str(e))
    sys.exit(__doc__)


if __name__ == "__main__":
    main()
