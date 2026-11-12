"""Final patch (23.4.0): work item field rules discovered from the TFS project itself.

Different TFS projects (and process templates) have different fields. A field that is optional in one project can be
mandatory in another (for example a custom "Test Result" drop-down with "0 Not Started", or Severity on Bugs), and
the allowed values differ. Creating a work item without such a field makes TFS answer HTTP 400 with
``TF401320: Rule Error for field Test Result. Error code: Required, HasValues, LimitedToValues...``, which the earlier
versions showed only as "TFS request failed with HTTP 400".

This module never assumes a field exists. For every work item type it reads, from the project:

* every field with its reference name, display name, ``alwaysRequired`` flag, allowed values and default value;

and before any work item is created it:

1. checks each value in the payload against the field's allowed values (an equivalent value is matched, for example
   "2" -> "2 - High"); a value that cannot be matched is reported, never sent;
2. fills every mandatory field the payload lacks, in this order: the value the user just gave, the default saved in
   Preferences > TFS Integration for this project and type, the safe automatic choice (a field with one allowed value,
   or a "Not Started" / "0 ..." value of a result or outcome field), and otherwise leaves it to the server's own
   default when the type defines one;
3. stops with ``TFS_FIELDS_REQUIRED`` (HTTP 422) listing each mandatory field it cannot fill, with its allowed values,
   so the page can ask the user (and save the answer as the project's default).

Rules that TFS applies only on save (a field required in a state, by a rule) cannot be read through the REST API; when
the server rejects a create for such a field, ``rule_error_fields`` reads the field names from its message so the
caller can fill them the same way and try once more.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ..errors import QAError

# Fields TFS fills itself; never asked for even when the type marks them required.
SYSTEM_FILLED = {
    "System.Id", "System.Rev", "System.AuthorizedDate", "System.RevisedDate", "System.ChangedDate",
    "System.ChangedBy", "System.CreatedDate", "System.CreatedBy", "System.Watermark", "System.TeamProject",
    "System.NodeName", "System.AuthorizedAs", "System.PersonId", "System.WorkItemType", "System.AreaId",
    "System.IterationId", "System.Reason", "System.State", "System.BoardColumn", "System.BoardColumnDone",
    "System.BoardLane", "System.CommentCount", "System.ExternalLinkCount", "System.HyperLinkCount",
    "System.AttachedFileCount", "System.RelatedLinkCount", "System.RemoteLinkCount", "System.AreaLevel1",
    "System.IterationLevel1", "System.Parent", "Microsoft.VSTS.Common.StateChangeDate",
}

# Fields whose "safe" starting value is the not-started one (a result that has not happened yet).
_NOT_STARTED_FIELD = re.compile(r"(result|outcome|execution|run status|test status)", re.I)
_NOT_STARTED_VALUE = re.compile(r"^\s*(0\b|0\s*[-.:)]|not\s*started|not\s*run|not\s*executed|none\b|pending\b)", re.I)

_RULE_ERROR = re.compile(r"TF401320:\s*Rule Error for field\s+(.+?)\.\s*Error code:\s*([^.]*)", re.I)
_RULE_ERROR_ALT = re.compile(r"TF\d+:.*?field\s+'([^']+)'", re.I)


@dataclass
class FieldInfo:
    ref: str
    name: str
    required: bool = False
    allowed: list[str] = field(default_factory=list)
    default: Any = None
    read_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "name": self.name, "required": self.required, "allowed": list(self.allowed),
                "default": self.default, "read_only": self.read_only}


def parse_fields(raw: list[dict[str, Any]]) -> dict[str, FieldInfo]:
    """The /_apis/wit/workitemtypes/{type}/fields answer (with $expand=allowedValues) as FieldInfo by reference."""
    out: dict[str, FieldInfo] = {}
    for f in raw or []:
        ref = f.get("referenceName")
        if not ref:
            continue
        allowed = [str(v) for v in (f.get("allowedValues") or []) if v not in (None, "")]
        default = f.get("defaultValue")
        out[ref] = FieldInfo(ref=ref, name=str(f.get("name") or ref.rsplit(".", 1)[-1]),
                             required=bool(f.get("alwaysRequired")), allowed=allowed,
                             default=default if default not in ("", None) else None,
                             read_only=bool(f.get("readOnly")))
    return out


def match_allowed(value: Any, allowed: list[str]) -> str | None:
    """The allowed value equal to ``value``, or an unambiguous equivalent ("2" -> "2 - High", "high" -> "2 - High",
    "not started" -> "0 Not Started"). None when nothing matches or more than one value would."""
    if value is None:
        return None
    text = str(value).strip()
    if not allowed:
        return text
    if text in allowed:
        return text
    low = text.lower()
    same_case = [a for a in allowed if a.lower() == low]
    if len(same_case) == 1:
        return same_case[0]
    if re.fullmatch(r"\d+", text):
        lead = [a for a in allowed if re.match(rf"^\s*{text}\b", a)]
        if len(lead) == 1:
            return lead[0]
    words = re.sub(r"^\s*\d+\s*[-.:)]?\s*", "", low)
    if words:
        tail = [a for a in allowed if re.sub(r"^\s*\d+\s*[-.:)]?\s*", "", a.lower()) == words]
        if len(tail) == 1:
            return tail[0]
    return None


def automatic_value(info: FieldInfo) -> str | None:
    """A value chosen without asking, only when it is unambiguous and harmless."""
    if len(info.allowed) == 1:
        return info.allowed[0]
    if info.allowed and _NOT_STARTED_FIELD.search(f"{info.name} {info.ref}"):
        starts = [a for a in info.allowed if _NOT_STARTED_VALUE.match(a)]
        if starts:
            # "0 Not Started" before "None" or "Pending" when several look like a starting value
            starts.sort(key=lambda a: (0 if re.match(r"^\s*0\b", a) else 1 if "not" in a.lower() else 2))
            return starts[0]
    return None


def rule_error_fields(message: str | None) -> list[dict[str, str]]:
    """Field names and error codes in a TFS rule error (TF401320), e.g. [{"name": "Test Result", "codes": "Required,
    HasValues, LimitedToValues"}]."""
    out: list[dict[str, str]] = []
    for m in _RULE_ERROR.finditer(message or ""):
        out.append({"name": m.group(1).strip().strip("'\""), "codes": m.group(2).strip()})
    if not out:
        for m in _RULE_ERROR_ALT.finditer(message or ""):
            out.append({"name": m.group(1).strip(), "codes": ""})
    return out


class FieldRules:
    """Prepares work item payloads for one project. ``schema`` loads (and caches) a type's fields; ``defaults``
    returns the Preferences defaults for a type."""

    def __init__(self, schema: Callable[[str], dict[str, FieldInfo]],
                 defaults: Callable[[str], dict[str, Any]]) -> None:
        self.schema = schema
        self.defaults = defaults

    def find(self, type_name: str, name_or_ref: str) -> FieldInfo | None:
        fields = self.schema(type_name)
        if name_or_ref in fields:
            return fields[name_or_ref]
        low = name_or_ref.strip().lower()
        return next((f for f in fields.values() if f.name.lower() == low), None)

    def prepare(self, type_name: str, patch: list[dict[str, Any]], values: dict[str, Any] | None = None,
                extra_required: list[str] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Validate and complete a JSON patch. Returns (patch, report). Raises TFS_FIELDS_REQUIRED when a mandatory
        field cannot be filled or a value is not allowed. ``values`` are answers from the user (reference -> value);
        ``extra_required`` are fields the server reported as required by a rule."""
        fields = self.schema(type_name)
        values = {k: v for k, v in (values or {}).items() if v not in (None, "")}
        saved = {k: v for k, v in (self.defaults(type_name) or {}).items() if v not in (None, "")}
        out: list[dict[str, Any]] = []
        present: set[str] = set()
        report: dict[str, Any] = {"type": type_name, "filled": [], "corrected": [], "dropped": []}
        problems: list[dict[str, Any]] = []
        for op in patch:
            path = str(op.get("path", ""))
            if not path.startswith("/fields/"):
                out.append(op)
                continue
            ref = path[len("/fields/"):]
            info = fields.get(ref)
            if info is None:
                report["dropped"].append(ref)  # the type has no such field: never sent
                continue
            value = op.get("value")
            if ref in values:
                value = values[ref]
            if info.allowed and value not in (None, ""):
                fixed = match_allowed(value, info.allowed)
                if fixed is None:
                    alt = values.get(ref) or saved.get(ref)
                    fixed = match_allowed(alt, info.allowed) if alt is not None else None
                if fixed is None:
                    if info.required:
                        problems.append({**info.to_dict(), "problem": "not_allowed", "value": str(value)})
                    else:
                        report["dropped"].append(f"{ref} (value '{value}' is not allowed)")
                    present.add(ref)
                    continue
                if fixed != str(value).strip():
                    report["corrected"].append({"ref": ref, "from": str(value), "to": fixed})
                    value = fixed
            out.append({**op, "value": value})
            present.add(ref)
        must = {r for r, f in fields.items() if f.required and not f.read_only}
        for name in extra_required or []:
            info = self.find(type_name, name)
            if info:
                must.add(info.ref)
        for ref in sorted(must - present - SYSTEM_FILLED):
            info = fields[ref]
            chosen, source = None, None
            for candidate, src in ((values.get(ref), "you"), (saved.get(ref), "preferences")):
                if candidate is None:
                    continue
                chosen = match_allowed(candidate, info.allowed) if info.allowed else str(candidate)
                if chosen is not None:
                    source = src
                    break
            if chosen is None:
                chosen = automatic_value(info)
                source = "automatic" if chosen is not None else None
            if chosen is None and info.default is not None and ref not in (extra_required or []):
                continue  # the type has its own default value; TFS fills it
            if chosen is None:
                problems.append({**info.to_dict(), "problem": "missing"})
                continue
            out.append({"op": "add", "path": f"/fields/{ref}", "value": chosen})
            report["filled"].append({"ref": ref, "name": info.name, "value": chosen, "source": source})
        if problems:
            names_ = ", ".join(p["name"] for p in problems)
            raise QAError("TFS_FIELDS_REQUIRED",
                          f"This TFS project needs a value for: {names_}. Choose the value (it can be saved as this "
                          "project's default in Preferences > TFS Integration).", status=422,
                          details={"type": type_name, "fields": problems})
        return out, report

    def validate(self, type_name: str) -> dict[str, Any]:
        """What creating a work item of this type will need (Preferences > Validate Mandatory Fields)."""
        fields = self.schema(type_name)
        saved = self.defaults(type_name) or {}
        rows = []
        for ref, info in sorted(fields.items(), key=lambda kv: (not kv[1].required, kv[1].name.lower())):
            if ref in SYSTEM_FILLED or info.read_only:
                continue
            if not info.required and ref not in saved:
                continue
            default = saved.get(ref)
            ok_default = match_allowed(default, info.allowed) if (default not in (None, "") and info.allowed) else default
            auto = automatic_value(info)
            status = ("default" if ok_default not in (None, "") else "automatic" if auto else
                      "server_default" if info.default is not None else "ask")
            if default not in (None, "") and ok_default is None:
                status = "invalid_default"
            rows.append({**info.to_dict(), "saved_default": default, "automatic": auto, "status": status})
        return {"type": type_name, "fields": rows,
                "ready": all(r["status"] in ("default", "automatic", "server_default") or not r["required"]
                             for r in rows)}
