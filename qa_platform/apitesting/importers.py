"""23.12.0: offline import of API definitions: OpenAPI 3.x / Swagger 2.0 (JSON or YAML), Postman collections (v2.0 and
v2.1), a cURL command, SkyGuard's own export, and an existing Python API test (read, never run).

Nothing imported is executed or fetched automatically:

* ``$ref`` to ``#/...`` is resolved inside the document; a reference to another file is resolved only among the
  files uploaded together; a reference to http(s) is listed as unavailable, never downloaded.
* A cURL command is split like a shell would split it, but never run; ``@file`` data and ``-k`` are reported and not
  applied; a user name and password given with ``-u`` are not kept (the password goes in Authentication).
* A Python test is parsed with ``ast``: its literal URLs and HTTP methods are read; its code is not run.
"""

from __future__ import annotations

import ast
import copy
import json
import re
import shlex
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ..errors import QAError
from .safety import METHODS

MAX_SPEC = 8 * 1024 * 1024
MAX_NODES = 400_000
MAX_DEPTH = 12
HTTP_METHODS = {m.lower() for m in METHODS} | {"trace"}


def _yaml() -> Any:
    from ..vendor import yaml  # PyYAML 6.0.3, pure Python, shipped with SkyGuard (safe_load only)

    return yaml


def parse_document(text: str, name: str = "specification") -> Any:
    if len(text.encode("utf-8", "ignore")) > MAX_SPEC:
        raise QAError("API_SPEC_TOO_LARGE", f"{name} is larger than {MAX_SPEC // (1024 * 1024)} MB.", status=413)
    stripped = text.lstrip()
    try:
        if stripped.startswith(("{", "[")):
            doc = json.loads(text)
        else:
            doc = _yaml().safe_load(text)
    except Exception as exc:
        raise QAError("API_SPEC_INVALID", f"{name} is neither valid JSON nor valid YAML: {str(exc)[:200]}",
                      status=422) from None
    _count_nodes(doc, name)
    return doc


def _count_nodes(doc: Any, name: str) -> None:
    """Refuse documents whose YAML aliases expand to a huge structure (the 'billion laughs' pattern)."""
    stack, n, seen = [doc], 0, 0
    while stack:
        node = stack.pop()
        n += 1
        if n > MAX_NODES:
            raise QAError("API_SPEC_TOO_LARGE", f"{name} expands to more than {MAX_NODES} values; it is refused.",
                          status=413)
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        seen += 1


# ---- references ---------------------------------------------------------------------------------------------------

class Resolver:
    def __init__(self, files: dict[str, Any], main: str) -> None:
        self.files = files
        self.main = main
        self.unresolved: dict[str, str] = {}

    def lookup(self, ref: str, current: str) -> tuple[Any, str] | None:
        if ref.startswith(("http://", "https://", "//")):
            self.unresolved[ref] = "external reference: not downloaded (SkyGuard works offline)"
            return None
        file_part, _, pointer = ref.partition("#")
        doc_name = current
        if file_part:
            key = file_part.replace("\\", "/").split("/")[-1]
            if key not in self.files:
                self.unresolved[ref] = f"the file {file_part} was not uploaded with the specification"
                return None
            doc_name = key
        node = self.files[doc_name]
        for part in [p for p in pointer.split("/") if p]:
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                self.unresolved[ref] = "the referenced part does not exist"
                return None
        return node, doc_name

    def inline(self, node: Any, current: str | None = None, depth: int = 0, chain: tuple[str, ...] = ()) -> Any:
        """A copy with references replaced by what they point to (cycles and depth limited, never expanded forever)."""
        current = current or self.main
        if depth > MAX_DEPTH:
            return {"description": "(nested too deeply; not expanded)"}
        if isinstance(node, dict):
            if "$ref" in node and isinstance(node["$ref"], str):
                ref = node["$ref"]
                key = f"{current}::{ref}"
                if key in chain:
                    return {"description": f"(recursive reference {ref})"}
                found = self.lookup(ref, current)
                if found is None:
                    return {"x-unresolved-ref": ref}
                target, doc = found
                out = self.inline(target, doc, depth + 1, chain + (key,))
                extra = {k: v for k, v in node.items() if k != "$ref"}
                if extra and isinstance(out, dict):
                    out = {**out, **self.inline(extra, current, depth + 1, chain)}
                return out
            return {k: self.inline(v, current, depth + 1, chain) for k, v in node.items()}
        if isinstance(node, list):
            return [self.inline(v, current, depth + 1, chain) for v in node]
        return node


# ---- OpenAPI / Swagger -----------------------------------------------------------------------------------------------

def _json_media(content: dict[str, Any] | None) -> tuple[str | None, dict[str, Any] | None]:
    if not isinstance(content, dict) or not content:
        return None, None
    for ctype in content:
        if "json" in ctype.lower():
            return ctype, content[ctype] or {}
    ctype = next(iter(content))
    return ctype, content[ctype] or {}


def import_openapi(files: dict[str, str], main: str | None = None) -> dict[str, Any]:
    """files: {file name: text}. Returns {version, title, base_urls, endpoints, security_schemes, unresolved, warnings}."""
    if not files:
        raise QAError("API_SPEC_MISSING", "Upload an OpenAPI or Swagger file.", status=422)
    docs = {name.replace("\\", "/").split("/")[-1]: parse_document(text, name) for name, text in files.items()}
    main = (main or next(iter(docs))).replace("\\", "/").split("/")[-1]
    root = docs[main]
    if not isinstance(root, dict):
        raise QAError("API_SPEC_INVALID", "The specification must be an object.", status=422)
    if str(root.get("openapi", "")).startswith("3"):
        version = f"OpenAPI {root['openapi']}"
        v3 = True
    elif str(root.get("swagger", "")) == "2.0":
        version = "Swagger 2.0"
        v3 = False
    else:
        raise QAError("API_SPEC_UNSUPPORTED", "This is not an OpenAPI 3.x or Swagger 2.0 document (no 'openapi: 3...' "
                      "or 'swagger: \"2.0\"').", status=422)
    res = Resolver(docs, main)
    warnings: list[str] = []
    info = root.get("info") or {}
    if v3:
        base_urls = [str(s.get("url")) for s in root.get("servers") or [] if isinstance(s, dict) and s.get("url")]
        schemes = res.inline((root.get("components") or {}).get("securitySchemes") or {})
    else:
        host = root.get("host")
        base = root.get("basePath") or ""
        base_urls = [f"{s}://{host}{base}" for s in (root.get("schemes") or ["https"])] if host else ([base] if base else [])
        schemes = res.inline(root.get("securityDefinitions") or {})
    global_security = root.get("security") or []
    endpoints: list[dict[str, Any]] = []
    paths = root.get("paths") or {}
    if not isinstance(paths, dict) or not paths:
        warnings.append("The specification has no paths.")
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        if "$ref" in item:
            item = res.inline(item)
        shared = item.get("parameters") or []
        for method, op in item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(op, dict):
                continue
            params = {}
            for p in [*shared, *(op.get("parameters") or [])]:
                p = res.inline(p)
                if isinstance(p, dict) and p.get("name"):
                    params[(p.get("in"), p["name"])] = p
            ep: dict[str, Any] = {
                "method": method.upper(), "path": str(path), "operation_id": op.get("operationId"),
                "summary": op.get("summary") or op.get("description", "")[:200], "tags": op.get("tags") or [],
                "deprecated": bool(op.get("deprecated")), "parameters": [], "request_body": None, "responses": {},
                "security": [list(s.keys()) for s in (op.get("security") if "security" in op else global_security)
                             if isinstance(s, dict)],
            }
            body_param = None
            for (where, name), p in params.items():
                if where == "body":
                    body_param = p
                    continue
                schema = p.get("schema") if v3 or "schema" in p else {k: p[k] for k in p if k in (
                    "type", "format", "enum", "minimum", "maximum", "minLength", "maxLength", "pattern", "items",
                    "default", "exclusiveMinimum", "exclusiveMaximum")}
                ep["parameters"].append({"name": name, "in": where, "required": bool(p.get("required") or where == "path"),
                                         "schema": res.inline(schema or {}), "example": p.get("example"),
                                         "description": (p.get("description") or "")[:200]})
            if v3 and op.get("requestBody"):
                rb = res.inline(op["requestBody"])
                ctype, media = _json_media(rb.get("content"))
                ep["request_body"] = {"content_type": ctype, "required": bool(rb.get("required")),
                                      "schema": (media or {}).get("schema") or {}, "example": _example(media)}
            elif body_param is not None:
                consumes = op.get("consumes") or root.get("consumes") or ["application/json"]
                ep["request_body"] = {"content_type": consumes[0], "required": bool(body_param.get("required")),
                                      "schema": res.inline(body_param.get("schema") or {}),
                                      "example": body_param.get("x-example")}
            for status, resp in (op.get("responses") or {}).items():
                resp = res.inline(resp) if isinstance(resp, dict) else {}
                if v3:
                    ctype, media = _json_media(resp.get("content"))
                    schema = (media or {}).get("schema")
                    example = _example(media)
                else:
                    produces = op.get("produces") or root.get("produces") or ["application/json"]
                    ctype, schema = (produces[0] if resp.get("schema") else None), resp.get("schema")
                    example = (resp.get("examples") or {}).get("application/json")
                ep["responses"][str(status)] = {"description": (resp.get("description") or "")[:300],
                                                "content_type": ctype, "schema": schema, "example": example}
            endpoints.append(ep)
    if len(endpoints) > 2000:
        warnings.append(f"Only the first 2000 of {len(endpoints)} operations were imported.")
        endpoints = endpoints[:2000]
    for ref, why in res.unresolved.items():
        warnings.append(f"Reference {ref} is unavailable: {why}. Schema checks that need it are reported as not "
                        "checked, never guessed.")
    return {"kind": "openapi", "version": version, "title": info.get("title") or main, "api_version": info.get("version"),
            "base_urls": base_urls, "endpoints": endpoints, "security_schemes": schemes,
            "unresolved": sorted(res.unresolved), "warnings": warnings}


def _example(media: dict[str, Any] | None) -> Any:
    if not isinstance(media, dict):
        return None
    if "example" in media:
        return media["example"]
    ex = media.get("examples")
    if isinstance(ex, dict) and ex:
        first = next(iter(ex.values()))
        return first.get("value") if isinstance(first, dict) else None
    return None


# ---- cURL ----------------------------------------------------------------------------------------------------------

def import_curl(command: str) -> dict[str, Any]:
    text = (command or "").replace("\\\r\n", " ").replace("\\\n", " ").replace("^\r\n", " ").replace("^\n", " ").strip()
    if len(text) > 100_000:
        raise QAError("API_CURL_TOO_LARGE", "The cURL command is too long.", status=413)
    try:
        args = shlex.split(text, posix=True)
    except ValueError as exc:
        raise QAError("API_CURL_INVALID", f"The cURL command could not be read: {exc}", status=422) from None
    if not args or args[0].lower() not in ("curl", "curl.exe"):
        raise QAError("API_CURL_INVALID", "Paste a command that starts with curl.", status=422)
    method, url, headers, data, warnings = None, None, {}, [], []
    auth: dict[str, Any] = {"type": "none"}
    get_mode = False
    i = 1
    while i < len(args):
        a = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else None
        if a in ("-X", "--request") and nxt:
            method, i = nxt.upper(), i + 2
            continue
        if a in ("-H", "--header") and nxt:
            k, _, v = nxt.partition(":")
            k, v = k.strip(), v.strip()
            if k.lower() in ("authorization", "cookie", "x-api-key", "api-key", "proxy-authorization"):
                if k.lower() == "authorization" and v.lower().startswith("bearer "):
                    auth = {"type": "bearer"}
                elif k.lower() == "authorization" and v.lower().startswith("basic "):
                    auth = {"type": "basic"}
                elif k.lower() in ("x-api-key", "api-key"):
                    auth = {"type": "api_key", "in": "header", "name": k}
                warnings.append(f"The {k} header holds a secret; it was not imported. Save it under Authentication.")
            elif k:
                headers[k] = v
            i += 2
            continue
        if a in ("-d", "--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode", "--json") and nxt is not None:
            if nxt.startswith("@") and a != "--data-raw":
                warnings.append(f"Data from the file {nxt[1:]} was not read (files are never read from a pasted command).")
            else:
                data.append(nxt)
            if a == "--json":
                headers.setdefault("Content-Type", "application/json")
                headers.setdefault("Accept", "application/json")
            i += 2
            continue
        if a in ("-u", "--user") and nxt:
            auth = {"type": "basic", "username": nxt.partition(":")[0]}
            warnings.append("The password given with -u was not kept. Save it under Authentication.")
            i += 2
            continue
        if a in ("-b", "--cookie") and nxt:
            warnings.append("Cookies given with -b were not imported (session secrets are never stored).")
            i += 2
            continue
        if a in ("-k", "--insecure"):
            warnings.append("-k (no TLS verification) was not applied; TLS certificates are verified by default.")
            i += 1
            continue
        if a in ("-G", "--get"):
            get_mode, i = True, i + 1
            continue
        if a == "--url" and nxt:
            url, i = nxt, i + 2
            continue
        if a in ("-A", "--user-agent", "-e", "--referer", "-o", "--output", "-w", "--write-out", "--connect-timeout",
                 "-m", "--max-time", "--cacert", "--cert", "--key", "-x", "--proxy", "--retry") and nxt is not None:
            if a in ("-A", "--user-agent"):
                headers["User-Agent"] = nxt
            elif a in ("--cert", "--key", "--cacert", "-x", "--proxy"):
                warnings.append(f"{a} was not imported; set certificates and proxies on the API application.")
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        url = url or a
        i += 1
    if not url:
        raise QAError("API_CURL_INVALID", "The cURL command has no URL.", status=422)
    if not re.match(r"^https?://", url, re.I):
        url = "http://" + url
    parts = urlsplit(url)
    query = [{"name": k, "value": v} for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    body: Any = "&".join(data) if data else None
    if get_mode and body:
        query += [{"name": k, "value": v} for k, v in parse_qsl(body, keep_blank_values=True)]
        body = None
    method = method or ("POST" if body is not None else "GET")
    if method not in METHODS:
        raise QAError("API_CURL_INVALID", f"The method {method} is not supported.", status=422)
    ctype = next((v for k, v in headers.items() if k.lower() == "content-type"), None)
    if body is not None and (ctype or "").lower().startswith("application/json"):
        try:
            body = json.loads(body)
        except ValueError:
            warnings.append("The body is marked as JSON but is not valid JSON; it was kept as text.")
    elif body is not None and ctype is None:
        ctype = "application/x-www-form-urlencoded"
    return {"kind": "curl", "base_url": f"{parts.scheme}://{parts.netloc}",
            "endpoint": {"method": method, "path": parts.path or "/", "query": query,
                         "headers": [{"name": k, "value": v} for k, v in headers.items() if k.lower() != "content-type"],
                         "body": body, "content_type": ctype},
            "auth": auth, "warnings": warnings}


# ---- Postman collection --------------------------------------------------------------------------------------------

def import_postman(text: str) -> dict[str, Any]:
    doc = parse_document(text, "collection")
    if not isinstance(doc, dict) or "item" not in doc:
        raise QAError("API_COLLECTION_INVALID", "This is not a Postman collection (v2.0 or v2.1).", status=422)
    out, warnings = [], []

    def walk(items: list[Any], folder: str) -> None:
        for it in items or []:
            if not isinstance(it, dict):
                continue
            if "item" in it:
                walk(it["item"], f"{folder}{it.get('name', '')}/")
                continue
            req = it.get("request")
            if isinstance(req, str):
                req = {"url": req, "method": "GET"}
            if not isinstance(req, dict):
                continue
            url = req.get("url")
            raw = url.get("raw") if isinstance(url, dict) else url
            raw = str(raw or "")
            parts = urlsplit(raw if "://" in raw else "http://" + raw)
            headers = []
            for h in req.get("header") or []:
                if not isinstance(h, dict) or h.get("disabled"):
                    continue
                if str(h.get("key", "")).lower() in ("authorization", "cookie", "x-api-key"):
                    warnings.append(f"{it.get('name')}: the {h.get('key')} header was not imported (a secret).")
                    continue
                headers.append({"name": h.get("key"), "value": h.get("value")})
            body = None
            ctype = None
            b = req.get("body") or {}
            if b.get("mode") == "raw":
                body = b.get("raw")
                lang = ((b.get("options") or {}).get("raw") or {}).get("language")
                if lang == "json" or (isinstance(body, str) and body.strip().startswith(("{", "["))):
                    ctype = "application/json"
                    try:
                        body = json.loads(body) if body else None
                    except ValueError:
                        pass
            elif b.get("mode") == "urlencoded":
                ctype = "application/x-www-form-urlencoded"
                body = "&".join(f"{p.get('key')}={p.get('value', '')}" for p in b.get("urlencoded") or [])
            if req.get("auth"):
                warnings.append(f"{it.get('name')}: its authentication settings were not imported; set them on the "
                                "API application.")
            out.append({"name": f"{folder}{it.get('name') or parts.path}", "method": str(req.get("method") or "GET").upper(),
                        "path": parts.path or "/", "query": [{"name": k, "value": v} for k, v in
                                                            parse_qsl(parts.query, keep_blank_values=True)],
                        "headers": headers, "body": body, "content_type": ctype,
                        "base_url": f"{parts.scheme}://{parts.netloc}" if parts.netloc and "{{" not in parts.netloc else None})

    walk(doc["item"], "")
    return {"kind": "postman", "title": (doc.get("info") or {}).get("name") or "Collection", "requests": out[:2000],
            "warnings": warnings}


# ---- existing Python API test -------------------------------------------------------------------------------------

def import_python(source: str) -> dict[str, Any]:
    """Literal requests found in a Python test using httpx or requests. The code is parsed, never run."""
    if len(source) > 400_000:
        raise QAError("API_SCRIPT_TOO_LARGE", "The script is larger than 400 KB.", status=413)
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise QAError("API_SCRIPT_INVALID", f"The script has a syntax error at line {exc.lineno}: {exc.msg}",
                      status=422) from None
    found, warnings = [], []
    consts: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and \
                isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            consts[node.targets[0].id] = node.value.value
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr.upper()
        if method not in METHODS or not node.args:
            continue
        url = _literal_url(node.args[0], consts)
        if url is None:
            warnings.append(f"Line {node.lineno}: the URL is computed at run time and was not imported.")
            continue
        parts = urlsplit(url if "://" in url or url.startswith("/") else "/" + url)
        found.append({"method": method, "path": parts.path or "/", "base_url": f"{parts.scheme}://{parts.netloc}" if
                      parts.netloc else None, "line": node.lineno,
                      "query": [{"name": k, "value": v} for k, v in parse_qsl(parts.query, keep_blank_values=True)]})
    if not found:
        warnings.append("No literal httpx/requests calls were found.")
    return {"kind": "python", "requests": found[:500], "warnings": warnings}


def _literal_url(node: ast.AST, consts: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = ""
        for v in node.values:
            if isinstance(v, ast.Constant):
                out += str(v.value)
            elif isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name) and v.value.id in consts:
                out += consts[v.value.id]
            elif isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name):
                out += "{" + v.value.id + "}" if out else ""
            else:
                return None
        return out
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _literal_url(node.left, consts), _literal_url(node.right, consts)
        return a + b if a is not None and b is not None else None
    if isinstance(node, ast.Name) and node.id in consts:
        return consts[node.id]
    return None


def deepcopy(v: Any) -> Any:
    return copy.deepcopy(v)
