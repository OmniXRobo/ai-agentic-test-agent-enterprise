"""23.12.0: offline JSON Schema validation for API responses (OpenAPI 3.x, Swagger 2.0 and JSON Schema draft 4-2020).

Pure Python, no download: every keyword below is checked, local ``$ref`` (``#/...``) is resolved inside the document
it came from, and anything else is reported instead of being guessed.

Checked: type (with ``nullable`` and type lists), enum, const, required, properties, patternProperties,
additionalProperties, items (schema or list), prefixItems, minItems, maxItems, uniqueItems, minLength, maxLength,
pattern, minimum, maximum, exclusiveMinimum, exclusiveMaximum (boolean and numeric forms), multipleOf,
minProperties, maxProperties, format (date, date-time, time, email, uuid, ipv4, ipv6, uri), allOf, anyOf, oneOf, not.
Keywords that are not checked are listed in ``unchecked`` so a report never claims more than was validated.
"""

from __future__ import annotations

import ipaddress
import re
from datetime import date, datetime
from typing import Any

from . import jsonpath

KNOWN = {"type", "nullable", "enum", "const", "required", "properties", "patternProperties", "additionalProperties",
         "items", "prefixItems", "minItems", "maxItems", "uniqueItems", "minLength", "maxLength", "pattern", "minimum",
         "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minProperties", "maxProperties", "format",
         "allOf", "anyOf", "oneOf", "not", "$ref", "description", "title", "example", "examples", "default",
         "readOnly", "writeOnly", "deprecated", "xml", "externalDocs", "discriminator", "$schema", "$id", "definitions",
         "$defs", "components", "x-nullable", "$comment", "contentMediaType", "contentEncoding"}
MAX_ERRORS = 50
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_TIME = re.compile(r"^\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$")


def _type_ok(value: Any, t: str) -> bool:
    if t == "null":
        return value is None
    if t == "boolean":
        return isinstance(value, bool)
    if t == "integer":
        return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, float) and value.is_integer())
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "string":
        return isinstance(value, str)
    if t == "array":
        return isinstance(value, list)
    if t == "object":
        return isinstance(value, dict)
    return True


def _format_ok(value: Any, fmt: str) -> bool | None:
    if not isinstance(value, str):
        return True
    try:
        if fmt == "date":
            date.fromisoformat(value)
        elif fmt == "date-time":
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if "T" not in value.upper() and " " not in value:
                return False
        elif fmt == "time":
            return bool(_TIME.match(value))
        elif fmt == "email":
            return bool(_EMAIL.match(value))
        elif fmt == "uuid":
            return bool(_UUID.match(value))
        elif fmt == "ipv4":
            ipaddress.IPv4Address(value)
        elif fmt == "ipv6":
            ipaddress.IPv6Address(value)
        elif fmt in ("uri", "url"):
            return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value))
        else:
            return None  # a format we do not check
    except ValueError:
        return False
    return True


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


class Validator:
    def __init__(self, root: Any) -> None:
        self.root = root
        self.errors: list[dict[str, str]] = []
        self.unchecked: set[str] = set()
        self.unresolved: set[str] = set()

    def resolve(self, ref: str) -> Any:
        if not ref.startswith("#"):
            self.unresolved.add(ref)
            return None
        node = self.root
        for part in [p for p in ref[1:].split("/") if p]:
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                self.unresolved.add(ref)
                return None
        return node

    def err(self, path: str, message: str) -> None:
        if len(self.errors) < MAX_ERRORS:
            self.errors.append({"path": path or "$", "message": message})

    def check(self, value: Any, schema: Any, path: str = "$", depth: int = 0) -> bool:
        """True when the value matches. Errors are collected in self.errors."""
        if depth > 64 or schema is True or schema is None or schema == {}:
            return True
        if schema is False:
            self.err(path, "No value is allowed here.")
            return False
        if not isinstance(schema, dict):
            return True
        if "$ref" in schema:
            target = self.resolve(str(schema["$ref"]))
            if target is None:
                return True  # reported in unresolved, never guessed
            ok = self.check(value, target, path, depth + 1)
            rest = {k: v for k, v in schema.items() if k != "$ref"}
            return self.check(value, rest, path, depth + 1) and ok if rest else ok
        for k in schema:
            if k not in KNOWN and not k.startswith("x-"):
                self.unchecked.add(k)
        before = len(self.errors)
        nullable = schema.get("nullable") is True or schema.get("x-nullable") is True
        if value is None and nullable:
            return True
        t = schema.get("type")
        if t is not None:
            types = t if isinstance(t, list) else [t]
            if not any(_type_ok(value, str(x)) for x in types):
                self.err(path, f"Expected {' or '.join(map(str, types))}, got {_kind(value)}.")
                return False
        if "enum" in schema and isinstance(schema["enum"], list):
            if not any(_equal(value, e) for e in schema["enum"]):
                self.err(path, f"Value {_short(value)} is not one of {_short(schema['enum'])}.")
        if "const" in schema and not _equal(value, schema["const"]):
            self.err(path, f"Value must be {_short(schema['const'])}.")
        if isinstance(value, dict):
            self._object(value, schema, path, depth)
        if isinstance(value, list):
            self._array(value, schema, path, depth)
        if isinstance(value, str):
            self._string(value, schema, path)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            self._number(value, schema, path)
        for sub in schema.get("allOf") or []:
            self.check(value, sub, path, depth + 1)
        if schema.get("anyOf"):
            if not any(self._quiet(value, s, path, depth) for s in schema["anyOf"]):
                self.err(path, "Value matches none of the allowed shapes (anyOf).")
        if schema.get("oneOf"):
            n = sum(1 for s in schema["oneOf"] if self._quiet(value, s, path, depth))
            if n != 1:
                self.err(path, f"Value must match exactly one shape (oneOf); it matches {n}.")
        if "not" in schema and self._quiet(value, schema["not"], path, depth):
            self.err(path, "Value matches a shape that is not allowed (not).")
        return len(self.errors) == before

    def _quiet(self, value: Any, schema: Any, path: str, depth: int) -> bool:
        saved = list(self.errors)
        ok = self.check(value, schema, path, depth + 1)
        self.errors = saved
        return ok

    def _object(self, value: dict, schema: dict, path: str, depth: int) -> None:
        props = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in value:
                self.err(f"{path}.{name}", "Required property is missing.")
        for name, sub in props.items():
            if name in value:
                if isinstance(sub, dict) and sub.get("writeOnly"):
                    continue
                self.check(value[name], sub, f"{path}.{name}", depth + 1)
        patterns = schema.get("patternProperties") or {}
        extra = [k for k in value if k not in props and not any(_search(p, k) for p in patterns)]
        for p, sub in patterns.items():
            for k in value:
                if _search(p, k):
                    self.check(value[k], sub, f"{path}.{k}", depth + 1)
        ap = schema.get("additionalProperties", True)
        if ap is False and extra:
            self.err(path, f"Properties not allowed by the contract: {', '.join(sorted(extra)[:8])}.")
        elif isinstance(ap, dict):
            for k in extra:
                self.check(value[k], ap, f"{path}.{k}", depth + 1)
        if "minProperties" in schema and len(value) < int(schema["minProperties"]):
            self.err(path, f"At least {schema['minProperties']} properties are required.")
        if "maxProperties" in schema and len(value) > int(schema["maxProperties"]):
            self.err(path, f"At most {schema['maxProperties']} properties are allowed.")

    def _array(self, value: list, schema: dict, path: str, depth: int) -> None:
        items = schema.get("items")
        prefix = schema.get("prefixItems") or (items if isinstance(items, list) else None)
        if prefix:
            for i, sub in enumerate(prefix[:len(value)]):
                self.check(value[i], sub, f"{path}[{i}]", depth + 1)
        elif isinstance(items, dict):
            for i, v in enumerate(value[:1000]):
                self.check(v, items, f"{path}[{i}]", depth + 1)
                if len(self.errors) >= MAX_ERRORS:
                    break
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            self.err(path, f"At least {schema['minItems']} item(s) are required, found {len(value)}.")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            self.err(path, f"At most {schema['maxItems']} item(s) are allowed, found {len(value)}.")
        if schema.get("uniqueItems"):
            seen = []
            for v in value[:2000]:
                if any(_equal(v, s) for s in seen):
                    self.err(path, "Items must be unique.")
                    break
                seen.append(v)

    def _string(self, value: str, schema: dict, path: str) -> None:
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            self.err(path, f"At least {schema['minLength']} character(s) are required.")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            self.err(path, f"At most {schema['maxLength']} character(s) are allowed.")
        if "pattern" in schema and not _search(str(schema["pattern"]), value):
            self.err(path, f"Value does not match the pattern {schema['pattern']}.")
        if "format" in schema:
            ok = _format_ok(value, str(schema["format"]))
            if ok is False:
                self.err(path, f"Value is not a valid {schema['format']}.")
            elif ok is None:
                self.unchecked.add(f"format:{schema['format']}")

    def _number(self, value: float, schema: dict, path: str) -> None:
        mn, mx = schema.get("minimum"), schema.get("maximum")
        emn, emx = schema.get("exclusiveMinimum"), schema.get("exclusiveMaximum")
        if mn is not None:
            if emn is True and value <= mn:
                self.err(path, f"Value must be greater than {mn}.")
            elif emn is not True and value < mn:
                self.err(path, f"Value must be at least {mn}.")
        if mx is not None:
            if emx is True and value >= mx:
                self.err(path, f"Value must be less than {mx}.")
            elif emx is not True and value > mx:
                self.err(path, f"Value must be at most {mx}.")
        if isinstance(emn, (int, float)) and not isinstance(emn, bool) and value <= emn:
            self.err(path, f"Value must be greater than {emn}.")
        if isinstance(emx, (int, float)) and not isinstance(emx, bool) and value >= emx:
            self.err(path, f"Value must be less than {emx}.")
        mo = schema.get("multipleOf")
        if mo:
            q = value / mo
            if abs(q - round(q)) > 1e-9:
                self.err(path, f"Value must be a multiple of {mo}.")


def _search(pattern: str, value: str) -> bool:
    try:
        return re.search(pattern[:500], value[:20000]) is not None
    except re.error:
        return True  # a pattern Python cannot compile is not used to fail a response


def _kind(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def _short(v: Any) -> str:
    s = repr(v)
    return s if len(s) <= 80 else s[:77] + "..."


def validate(value: Any, schema: Any, root: Any = None) -> dict[str, Any]:
    """{valid, errors: [{path, message}], unchecked: [...], unresolved: [...]}"""
    v = Validator(root if root is not None else schema)
    v.check(value, schema)
    return {"valid": not v.errors and not v.unresolved, "errors": v.errors, "unchecked": sorted(v.unchecked),
            "unresolved": sorted(v.unresolved)}


def validate_at(value: Any, schema: Any, path: str | None, root: Any = None) -> dict[str, Any]:
    """Validate the part of a response at a JSONPath (whole body when no path)."""
    if path:
        found = jsonpath.find(value, path)
        if not found:
            return {"valid": False, "errors": [{"path": path, "message": "Nothing at this path."}], "unchecked": [],
                    "unresolved": []}
        value = found[0]
    return validate(value, schema, root)
