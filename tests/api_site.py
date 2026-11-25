"""23.12.0: a small, controllable local REST API for the API Testing tests (HTTP and HTTPS, IPv4 and IPv6).

Orders API (documented in openapi()):
  GET    /api/orders?page=&limit=&status=&sort=     list (pagination, filtering, sorting)
  POST   /api/orders                                create (required customer, amount 1..10000, status enum) -> 201
  GET    /api/orders/{id}                           one order, 404 when missing
  PUT    /api/orders/{id} / PATCH                   update -> 200
  DELETE /api/orders/{id}                           -> 204
  GET    /api/slow?ms=                               answers after a delay
  GET    /api/broken                                 500, or a body that breaks the contract (?mode=contract)
  POST   /oauth/token                                client_credentials -> access token
  GET    /redirect?to=                               302 to another URL (the API module must check each hop)
Authentication for /api/*: basic (qa / Secr3t-Pass!), bearer (tok-123456789), api key (X-API-Key: key-abcdef123).
Set Server.auth to "none", "basic", "bearer", "api_key" or "oauth".
"""

from __future__ import annotations

import base64
import json
import socket
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

BASIC = ("qa", "Secr3t-Pass!")
BEARER = "tok-123456789"
API_KEY = "key-abcdef123"
OAUTH = ("skyguard-client", "client-secret-xyz")
STATUSES = ["new", "approved", "shipped"]


class State:
    def __init__(self) -> None:
        self.auth = "none"
        self.orders: dict[int, dict] = {}
        self.next_id = 1
        self.requests: list[dict] = []
        self.tokens: set[str] = set()
        self.lock = threading.Lock()
        for c, a, s in (("Acme", 120.5, "new"), ("Globex", 75, "approved"), ("Initech", 300, "shipped"),
                        ("Umbrella", 42, "new"), ("Hooli", 999.99, "approved")):
            self.add({"customer": c, "amount": a, "status": s})

    def add(self, o: dict) -> dict:
        with self.lock:
            o = {"id": self.next_id, **o}
            self.orders[self.next_id] = o
            self.next_id += 1
            return o


def openapi(base: str) -> dict:
    order = {"type": "object", "required": ["id", "customer", "amount", "status"], "additionalProperties": False,
             "properties": {"id": {"type": "integer"}, "customer": {"type": "string", "minLength": 1, "maxLength": 40},
                            "amount": {"type": "number", "minimum": 1, "maximum": 10000},
                            "status": {"type": "string", "enum": STATUSES}}}
    new = {"type": "object", "required": ["customer", "amount"],
           "properties": {"customer": {"type": "string", "minLength": 1, "maxLength": 40, "example": "SkyGuard Ltd"},
                          "amount": {"type": "number", "minimum": 1, "maximum": 10000, "example": 250},
                          "status": {"type": "string", "enum": STATUSES}}}
    err = {"type": "object", "required": ["error"], "properties": {"error": {"type": "string"}}}
    json_of = lambda ref: {"application/json": {"schema": {"$ref": ref}}}  # noqa: E731
    return {
        "openapi": "3.0.3", "info": {"title": "Orders API", "version": "1.0"}, "servers": [{"url": base}],
        "components": {"schemas": {"Order": order, "NewOrder": new, "Error": err,
                                   "OrderList": {"type": "object", "required": ["items", "total"],
                                                 "properties": {"items": {"type": "array", "items": {"$ref": "#/components/schemas/Order"}},
                                                                "total": {"type": "integer"}}}},
                       "securitySchemes": {"basicAuth": {"type": "http", "scheme": "basic"}}},
        "security": [{"basicAuth": []}],
        "paths": {
            "/api/orders": {
                "get": {"operationId": "listOrders", "parameters": [
                    {"name": "page", "in": "query", "schema": {"type": "integer", "minimum": 1}},
                    {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100}},
                    {"name": "status", "in": "query", "schema": {"type": "string", "enum": STATUSES}}],
                    "responses": {"200": {"description": "orders", "content": json_of("#/components/schemas/OrderList")},
                                  "400": {"description": "bad", "content": json_of("#/components/schemas/Error")},
                                  "401": {"description": "no credentials"}}},
                "post": {"operationId": "createOrder", "requestBody": {"required": True, "content": json_of("#/components/schemas/NewOrder")},
                         "responses": {"201": {"description": "created", "content": json_of("#/components/schemas/Order")},
                                       "400": {"description": "invalid", "content": json_of("#/components/schemas/Error")},
                                       "401": {"description": "no credentials"}}}},
            "/api/orders/{id}": {
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer", "example": 1}}],
                "get": {"responses": {"200": {"description": "one", "content": json_of("#/components/schemas/Order")},
                                      "404": {"description": "missing", "content": json_of("#/components/schemas/Error")},
                                      "401": {"description": "no credentials"}}},
                "delete": {"responses": {"204": {"description": "deleted"}, "404": {"description": "missing"},
                                         "401": {"description": "no credentials"}}}},
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "OrdersAPI/1.0"
    st: State

    def log_message(self, *a) -> None:  # quiet
        pass

    def _send(self, code: int, body=None, ctype="application/json", headers: dict | None = None) -> None:
        data = b"" if body is None else (json.dumps(body).encode() if not isinstance(body, (bytes, str)) else
                                         body.encode() if isinstance(body, str) else body)
        self.send_response(code)
        if body is not None:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if data and self.command != "HEAD":
            self.wfile.write(data)

    def _authed(self) -> bool:
        a = self.st.auth
        h = self.headers.get("Authorization", "")
        if a == "none":
            return True
        if a == "basic":
            return h == "Basic " + base64.b64encode(f"{BASIC[0]}:{BASIC[1]}".encode()).decode()
        if a == "bearer":
            return h == f"Bearer {BEARER}"
        if a == "api_key":
            return self.headers.get("X-API-Key") == API_KEY
        if a == "oauth":
            return h.startswith("Bearer ") and h[7:] in self.st.tokens
        return False

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return raw.decode(errors="replace")

    def do_GET(self) -> None:
        self._route("GET")

    def do_POST(self) -> None:
        self._route("POST")

    def do_PUT(self) -> None:
        self._route("PUT")

    def do_PATCH(self) -> None:
        self._route("PATCH")

    def do_DELETE(self) -> None:
        self._route("DELETE")

    def do_HEAD(self) -> None:
        self._route("HEAD")

    def _route(self, method: str) -> None:
        u = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        body = self._body() if method in ("POST", "PUT", "PATCH") else None
        self.st.requests.append({"method": method, "path": u.path, "query": q, "headers": dict(self.headers), "body": body})
        p = u.path
        host = self.headers.get("Host", "")
        if p == "/openapi.json":
            scheme = "https" if isinstance(self.connection, ssl.SSLSocket) else "http"
            return self._send(200, openapi(f"{scheme}://{host}"))
        if p == "/oauth/token" and method == "POST":
            form = parse_qs(body if isinstance(body, str) else "")
            if form.get("client_id", [""])[0] == OAUTH[0] and form.get("client_secret", [""])[0] == OAUTH[1]:
                tok = f"oauth-{len(self.st.tokens) + 1}-{int(time.time())}"
                self.st.tokens.add(tok)
                return self._send(200, {"access_token": tok, "token_type": "bearer", "expires_in": 600})
            return self._send(401, {"error": "invalid_client"})
        if p == "/redirect":
            return self._send(302, None, headers={"Location": q.get("to", "/")})
        if p == "/" or p == "":
            return self._send(404, {"error": "nothing here"})
        if not p.startswith("/api/"):
            return self._send(404, {"error": "not found"})
        if not self._authed():
            return self._send(401, {"error": "authentication required"}, headers={"WWW-Authenticate": "Basic"})
        if p == "/api/slow":
            time.sleep(int(q.get("ms", "1000")) / 1000)
            return self._send(200, {"slow": True})
        if p == "/api/broken":
            if q.get("mode") == "contract":
                return self._send(200, {"items": [{"id": "x1", "customer": "", "amount": -5, "status": "lost"}], "total": "one"})
            return self._send(500, {"error": "database unavailable"})
        if p == "/api/xml":
            return self._send(200, "<?xml version='1.0'?><order><id>1</id></order>", ctype="application/xml")
        if p == "/api/orders":
            if method == "GET":
                items = sorted(self.st.orders.values(), key=lambda o: o["id"])
                if "status" in q:
                    items = [o for o in items if o["status"] == q["status"]]
                try:
                    page, limit = int(q.get("page", "1")), int(q.get("limit", "20"))
                except ValueError:
                    return self._send(400, {"error": "page and limit must be numbers"})
                if page < 1 or not 1 <= limit <= 100:
                    return self._send(400, {"error": "page/limit out of range"})
                return self._send(200, {"items": items[(page - 1) * limit: page * limit], "total": len(items)})
            if method == "POST":
                err = _validate(body, partial=False)
                if err:
                    return self._send(400, {"error": err})
                o = self.st.add({"customer": body["customer"], "amount": body["amount"], "status": body.get("status", "new")})
                return self._send(201, o, headers={"Location": f"/api/orders/{o['id']}"})
        if p.startswith("/api/orders/"):
            try:
                oid = int(p.rsplit("/", 1)[1])
            except ValueError:
                return self._send(400, {"error": "id must be a number"})
            o = self.st.orders.get(oid)
            if o is None:
                return self._send(404, {"error": f"order {oid} not found"})
            if method == "GET":
                return self._send(200, o)
            if method in ("PUT", "PATCH"):
                err = _validate(body, partial=method == "PATCH")
                if err:
                    return self._send(400, {"error": err})
                o.update({k: v for k, v in body.items() if k in ("customer", "amount", "status")})
                return self._send(200, o)
            if method == "DELETE":
                del self.st.orders[oid]
                return self._send(204)
        return self._send(404, {"error": "not found"})


def _validate(body, partial: bool) -> str | None:
    if not isinstance(body, dict):
        return "a JSON object is required"
    if not partial:
        for f in ("customer", "amount"):
            if f not in body:
                return f"{f} is required"
    if "customer" in body and (not isinstance(body["customer"], str) or not 1 <= len(body["customer"]) <= 40):
        return "customer must be 1 to 40 characters"
    if "amount" in body and (isinstance(body["amount"], bool) or not isinstance(body["amount"], (int, float))
                             or not 1 <= body["amount"] <= 10000):
        return "amount must be a number from 1 to 10000"
    if "status" in body and body["status"] not in STATUSES:
        return "status is not allowed"
    return None


class _V6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def start(host: str = "127.0.0.1", tls: tuple[str, str] | None = None) -> tuple[ThreadingHTTPServer, str, State]:
    st = State()
    handler = type("H", (Handler,), {"st": st})
    cls = _V6Server if ":" in host else ThreadingHTTPServer
    srv = cls((host, 0), handler)
    srv.daemon_threads = True
    scheme = "http"
    if tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(tls[0], tls[1])
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        scheme = "https"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    h = f"[{host}]" if ":" in host else host
    return srv, f"{scheme}://{h}:{port}", st


def make_cert(folder: Path, hostname: str = "localhost") -> dict[str, str]:
    """A private CA and a server certificate for localhost / 127.0.0.1 (openssl, as on any build machine)."""
    folder.mkdir(parents=True, exist_ok=True)
    ca_key, ca, key, csr, crt, ext = (folder / n for n in ("ca.key", "ca.pem", "srv.key", "srv.csr", "srv.pem", "ext.cnf"))
    run = lambda *a: subprocess.run(list(a), check=True, capture_output=True)  # noqa: E731
    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(ca_key), "-out", str(ca), "-days", "2",
        "-subj", "/CN=SkyGuard Test CA",
        "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(csr), "-subj", f"/CN={hostname}")
    # Python 3.13 verifies strictly (VERIFY_X509_STRICT): key usage and the authority key identifier must be present
    ext.write_text(f"subjectAltName=DNS:{hostname},IP:127.0.0.1\nbasicConstraints=CA:FALSE\n"
                   "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
                   "authorityKeyIdentifier=keyid,issuer\nsubjectKeyIdentifier=hash\n")
    run("openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca), "-CAkey", str(ca_key), "-CAcreateserial", "-out", str(crt),
        "-days", "2", "-extfile", str(ext))
    return {"ca": str(ca), "cert": str(crt), "key": str(key)}
