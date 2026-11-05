"""23.3.23: performance scripts as JMX, edited as a tree.

The JMX text is the source of truth. The editor reads it into a tree of nodes, applies edits to the XML elements it
addresses, and writes the document back, so elements the editor has no form for (and their properties) stay exactly as
they were. Nothing here starts JMeter or sends traffic.

Node ids are paths of element positions inside the JMX ``hashTree`` structure, e.g. ``0`` is the Test Plan, ``0/2`` its
third child, ``0/2/0`` the first child of that.
"""

from __future__ import annotations

import copy
import difflib
import functools
import hashlib
import re
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from ..errors import QAError
from .jmx import _a, _b, _s  # the same escaping helpers the generator uses
from .models import ThreadGroupSpec

MAX_SCRIPT_BYTES = 20 * 1024 * 1024

SIMPLE_PROPS = {"stringProp", "boolProp", "intProp", "longProp", "doubleProp", "floatProp"}
THREAD_GROUPS = {"ThreadGroup", "SetupThreadGroup", "PostThreadGroup"}
LISTENERS = {"ResultCollector", "BackendListener", "Summariser"}
# Elements JMeter ships with but whose load the editor cannot compute yet (they arrive with the workload milestone)
UNSIZED_GROUPS = {"OpenModelThreadGroup"}

CATEGORY_BY_TAG = {
    "TestPlan": "plan", "ThreadGroup": "thread_group", "SetupThreadGroup": "thread_group",
    "PostThreadGroup": "thread_group", "OpenModelThreadGroup": "thread_group",
    "HTTPSamplerProxy": "sampler", "HTTPSampler": "sampler", "HTTPSampler2": "sampler", "DebugSampler": "sampler",
    "JSR223Sampler": "sampler", "JDBCSampler": "sampler", "TCPSampler": "sampler", "FlowControlAction": "sampler",
    "TransactionController": "controller", "GenericController": "controller", "LoopController": "controller",
    "IfController": "controller", "WhileController": "controller", "OnceOnlyController": "controller",
    "ForeachController": "controller", "RandomController": "controller", "ThroughputController": "controller",
    "RunTime": "controller", "SwitchController": "controller", "ModuleController": "controller",
    "IncludeController": "controller", "InterleaveControl": "controller", "CriticalSectionController": "controller",
    "RandomOrderController": "controller", "RecordingController": "controller",
    "RegexExtractor": "extractor", "JSONPostProcessor": "extractor", "BoundaryExtractor": "extractor",
    "XPathExtractor": "extractor", "XPath2Extractor": "extractor", "HtmlExtractor": "extractor",
    "JSR223PostProcessor": "post", "DebugPostProcessor": "post", "JSR223PreProcessor": "pre",
    "UserParameters": "pre", "RegExUserParameters": "pre",
    "ResponseAssertion": "assertion", "DurationAssertion": "assertion", "JSONPathAssertion": "assertion",
    "SizeAssertion": "assertion", "XPathAssertion": "assertion", "JSR223Assertion": "assertion",
    "HTMLAssertion": "assertion", "XMLAssertion": "assertion", "MD5HexAssertion": "assertion",
    "ConstantTimer": "timer", "UniformRandomTimer": "timer", "GaussianRandomTimer": "timer",
    "PoissonRandomTimer": "timer", "ConstantThroughputTimer": "timer", "PreciseThroughputTimer": "timer",
    "SyncTimer": "timer", "JSR223Timer": "timer",
    "HeaderManager": "config", "CookieManager": "config", "CacheManager": "config", "AuthManager": "config",
    "ConfigTestElement": "config", "Arguments": "config", "CSVDataSet": "config", "DNSCacheManager": "config",
    "KeystoreConfig": "config", "CounterConfig": "config", "RandomVariableConfig": "config",
    "JDBCDataSource": "config", "LoginConfig": "config",
    "ResultCollector": "listener", "BackendListener": "listener", "Summariser": "listener",
}

LABELS = {
    "ThreadGroup.num_threads": "Users (threads)", "ThreadGroup.ramp_time": "Ramp-up (s)",
    "ThreadGroup.scheduler": "Use duration", "ThreadGroup.duration": "Duration (s)",
    "ThreadGroup.delay": "Start delay (s)", "ThreadGroup.on_sample_error": "On sample error",
    "ThreadGroup.same_user_on_next_iteration": "Same user on each iteration",
    "ThreadGroup.main_controller/LoopController.loops": "Loops (-1 = forever)",
    "ThreadGroup.main_controller/LoopController.continue_forever": "Continue forever",
    "HTTPSampler.domain": "Server", "HTTPSampler.port": "Port", "HTTPSampler.protocol": "Protocol",
    "HTTPSampler.path": "Path", "HTTPSampler.method": "Method", "HTTPSampler.contentEncoding": "Content encoding",
    "HTTPSampler.follow_redirects": "Follow redirects", "HTTPSampler.auto_redirects": "Redirect automatically",
    "HTTPSampler.use_keepalive": "Keep-alive", "HTTPSampler.DO_MULTIPART_POST": "Multipart",
    "HTTPSampler.connect_timeout": "Connect timeout (ms)", "HTTPSampler.response_timeout": "Response timeout (ms)",
    "HTTPSampler.postBodyRaw": "Raw body", "HTTPSampler.image_parser": "Download embedded resources",
    "TransactionController.includeTimers": "Include timers in the time",
    "TransactionController.parent": "Report as one sample (parent)",
    "RegexExtractor.refname": "Variable name", "RegexExtractor.regex": "Regular expression",
    "RegexExtractor.template": "Template", "RegexExtractor.match_number": "Match number (0 = random, -1 = all)",
    "RegexExtractor.default": "Default value", "RegexExtractor.useHeaders": "Field to check",
    "JSONPostProcessor.referenceNames": "Variable names", "JSONPostProcessor.jsonPathExprs": "JSON path expressions",
    "JSONPostProcessor.match_numbers": "Match numbers", "JSONPostProcessor.defaultValues": "Default values",
    "BoundaryExtractor.refname": "Variable name", "BoundaryExtractor.lboundary": "Left boundary",
    "BoundaryExtractor.rboundary": "Right boundary", "BoundaryExtractor.match_number": "Match number",
    "BoundaryExtractor.default": "Default value",
    "Assertion.test_field": "Field to test", "Assertion.test_type": "Test type",
    "Assertion.assume_success": "Ignore status", "Assertion.custom_message": "Failure message",
    "DurationAssertion.duration": "Maximum duration (ms)",
    "JSON_PATH": "JSON path", "EXPECTED_VALUE": "Expected value", "JSONVALIDATION": "Check the value",
    "EXPECT_NULL": "Expect null", "INVERT": "Invert", "ISREGEX": "Expected value is a regular expression",
    "ConstantTimer.delay": "Delay (ms)", "RandomTimer.range": "Random range (ms)",
    "CookieManager.clearEachIteration": "Clear cookies each iteration",
    "TestPlan.functional_mode": "Functional test mode", "TestPlan.serialize_threadgroups":
        "Run thread groups one after another",
    "filename": "File", "variableNames": "Variable names", "delimiter": "Delimiter", "recycle": "Recycle on end",
    "stopThread": "Stop thread on end", "shareMode": "Sharing mode",
}

# Response assertion test types (JMeter bit flags): matches 1, contains 2, not 4, equals 8, substring 16, or 32
ASSERT_TYPES = {"matches": 1, "contains": 2, "equals": 8, "substring": 16}
ASSERT_FIELDS = {"code": "Assertion.response_code", "text": "Assertion.response_data",
                 "headers": "Assertion.response_headers", "message": "Assertion.response_message",
                 "url": "Assertion.sample_label", "document": "Assertion.response_data_as_document"}


def _err(message: str, code: str = "PERF_SCRIPT_INVALID", status: int = 422) -> QAError:
    return QAError(code, message, status=status)


# ---- parse and write --------------------------------------------------------------------------------------------

def parse(text: str) -> ET.Element:
    """Parse JMX text. DOCTYPE declarations (never used by JMeter) are refused, so no entity tricks are possible."""
    if not isinstance(text, str) or not text.strip():
        raise _err("The script is empty.")
    if len(text.encode("utf-8")) > MAX_SCRIPT_BYTES:
        raise _err(f"The script is larger than {MAX_SCRIPT_BYTES // (1024 * 1024)} MB.")
    if re.search(r"<!DOCTYPE|<!ENTITY", text, re.I):
        raise _err("The JMX contains a DOCTYPE or ENTITY declaration, which JMeter files never use.")
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    try:
        root = ET.fromstring(text.lstrip("﻿"), parser=parser)
    except ET.ParseError as exc:
        raise _err(f"The JMX is not valid XML: {exc}") from exc
    if root.tag != "jmeterTestPlan":
        raise _err("This is not a JMeter test plan: the root element is not jmeterTestPlan.")
    return root


def dump(root: ET.Element) -> str:
    """Write the document back in JMeter's indented layout, so versions can be compared line by line."""
    root = copy.deepcopy(root)
    _strip_whitespace(root)
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _strip_whitespace(el: ET.Element) -> None:
    """Drop layout-only whitespace between child elements, so indent() lays everything out the same way."""
    if len(el):
        if el.text is not None and not el.text.strip():
            el.text = None
        for child in el:
            if child.tail is not None and not child.tail.strip():
                child.tail = None
            _strip_whitespace(child)


def normalize(text: str) -> str:
    return dump(parse(text))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---- tree ----------------------------------------------------------------------------------------------------------

def _is_element(el: Any) -> bool:
    return isinstance(el.tag, str)


def _pairs(hash_tree: ET.Element) -> list[tuple[ET.Element, ET.Element | None]]:
    """(element, its hashTree) pairs of a hashTree, skipping comments."""
    kids = [k for k in hash_tree if _is_element(k)]
    out: list[tuple[ET.Element, ET.Element | None]] = []
    i = 0
    while i < len(kids):
        el = kids[i]
        if el.tag == "hashTree":  # a stray hashTree without its element: keep walking
            i += 1
            continue
        ht = kids[i + 1] if i + 1 < len(kids) and kids[i + 1].tag == "hashTree" else None
        out.append((el, ht))
        i += 2 if ht is not None else 1
    return out


def _top(root: ET.Element) -> ET.Element:
    ht = root.find("hashTree")
    if ht is None:
        raise _err("The JMX has no hashTree under jmeterTestPlan.")
    return ht


def locate(root: ET.Element, node_id: str) -> tuple[ET.Element, ET.Element | None, ET.Element]:
    """(element, its hashTree, the hashTree that contains it) for a node id."""
    try:
        path = [int(p) for p in str(node_id).split("/")]
    except ValueError as exc:
        raise _err(f"Unknown script node {node_id}.", "PERF_SCRIPT_NODE", 404) from exc
    container = _top(root)
    el: ET.Element | None = None
    ht: ET.Element | None = None
    for depth, idx in enumerate(path):
        pairs = _pairs(container)
        if idx < 0 or idx >= len(pairs):
            raise _err(f"Unknown script node {node_id}.", "PERF_SCRIPT_NODE", 404)
        el, ht = pairs[idx]
        if depth < len(path) - 1:
            if ht is None:
                raise _err(f"Unknown script node {node_id}.", "PERF_SCRIPT_NODE", 404)
            container = ht
    assert el is not None
    return el, ht, container


def _ensure_tree(container: ET.Element, el: ET.Element) -> ET.Element:
    """The element's hashTree, created right after it when missing."""
    kids = list(container)
    i = kids.index(el)
    if i + 1 < len(kids) and kids[i + 1].tag == "hashTree":
        return kids[i + 1]
    ht = ET.Element("hashTree")
    container.insert(i + 1, ht)
    return ht


def is_plugin(el: ET.Element) -> bool:
    """Elements from JMeter plugins (e.g. kg.apc.*): their class names are fully qualified outside org.apache.jmeter."""
    for name in (el.tag, el.get("testclass") or "", el.get("guiclass") or ""):
        if "." in name and not name.startswith("org.apache.jmeter"):
            return True
    return False


def category(el: ET.Element) -> str:
    if el.tag in CATEGORY_BY_TAG:
        return CATEGORY_BY_TAG[el.tag]
    t = el.tag
    for suffix, cat in (("ThreadGroup", "thread_group"), ("Sampler", "sampler"), ("Controller", "controller"),
                        ("Extractor", "extractor"), ("Assertion", "assertion"), ("Timer", "timer"),
                        ("PreProcessor", "pre"), ("PostProcessor", "post"), ("Listener", "listener"),
                        ("Visualizer", "listener"), ("Config", "config")):
        if t.endswith(suffix):
            return cat
    return "other"


def enabled(el: ET.Element) -> bool:
    return (el.get("enabled") or "true").lower() != "false"


def _prop_text(p: ET.Element) -> str:
    return p.text or ""


def simple_props(el: ET.Element) -> dict[str, str]:
    """Direct simple properties, plus those one level down in element properties (e.g. the thread group's loop count)
    as ``elementProp/name``."""
    out: dict[str, str] = {}
    for p in el:
        if not _is_element(p):
            continue
        name = p.get("name")
        if p.tag in SIMPLE_PROPS and name is not None:
            out[name] = _prop_text(p)
        elif p.tag == "elementProp" and name and name != "HTTPsampler.Arguments":
            for q in p:
                if _is_element(q) and q.tag in SIMPLE_PROPS and q.get("name") is not None:
                    out[f"{name}/{q.get('name')}"] = _prop_text(q)
    return out


def _find_prop(el: ET.Element, name: str) -> ET.Element | None:
    for p in el:
        if _is_element(p) and p.get("name") == name:
            return p
    return None


def _collection(el: ET.Element, name: str) -> ET.Element | None:
    p = _find_prop(el, name)
    return p if p is not None and p.tag == "collectionProp" else None


def _get_or_add_collection(el: ET.Element, name: str) -> ET.Element:
    coll = _collection(el, name)
    return coll if coll is not None else ET.SubElement(el, "collectionProp", {"name": name})


def _args_collection(el: ET.Element, element_prop: str) -> ET.Element | None:
    ep = _find_prop(el, element_prop)
    if ep is None or ep.tag != "elementProp":
        return None
    return _collection(ep, "Arguments.arguments")


def _arguments(coll: ET.Element | None) -> list[dict[str, str]]:
    out = []
    for ep in (coll if coll is not None else []):
        if not _is_element(ep) or ep.tag != "elementProp":
            continue
        vals = {q.get("name"): _prop_text(q) for q in ep if _is_element(q)}
        out.append({"name": vals.get("Argument.name", ep.get("name") or ""), "value": vals.get("Argument.value", "")})
    return out


def _headers(el: ET.Element) -> list[dict[str, str]]:
    coll = _collection(el, "HeaderManager.headers")
    out = []
    for ep in (coll if coll is not None else []):
        if _is_element(ep):
            vals = {q.get("name"): _prop_text(q) for q in ep if _is_element(q)}
            out.append({"name": vals.get("Header.name", ""), "value": vals.get("Header.value", "")})
    return out


def _patterns(el: ET.Element) -> list[str]:
    coll = _collection(el, "Asserion.test_strings")  # sic: JMeter's own property name
    return [_prop_text(p) for p in (coll if coll is not None else []) if _is_element(p)]


def details(el: ET.Element) -> dict[str, Any]:
    """Everything the property panel shows for one element."""
    props = simple_props(el)
    d: dict[str, Any] = {
        "tag": el.tag, "testclass": el.get("testclass") or el.tag, "name": el.get("testname") or "",
        "enabled": enabled(el), "category": category(el), "plugin": is_plugin(el),
        "comments": props.pop("TestPlan.comments", ""),  # JMeter keeps every element's comment under this name
        "props": [{"name": k, "label": LABELS.get(k, k), "value": v} for k, v in props.items()],
    }
    if el.tag == "TestPlan":
        d["variables"] = _arguments(_args_collection(el, "TestPlan.user_defined_variables"))
    if el.tag == "Arguments":
        d["variables"] = _arguments(_collection(el, "Arguments.arguments"))
    if el.tag in ("HTTPSamplerProxy", "HTTPSampler", "HTTPSampler2"):
        raw = props.get("HTTPSampler.postBodyRaw", "false").lower() == "true"
        args = _arguments(_args_collection(el, "HTTPsampler.Arguments"))
        if raw:
            d["body"] = args[0]["value"] if args else ""
        else:
            d["params"] = args
        d["url"] = _sampler_url(props)
    if el.tag == "HeaderManager":
        d["headers"] = _headers(el)
    if el.tag == "ResponseAssertion":
        d["patterns"] = _patterns(el)
    # Collections the panel has no form for are shown as XML, read only (edit them in the Raw JMX view)
    raw_parts = [ET.tostring(p, encoding="unicode") for p in el
                 if _is_element(p) and p.tag == "collectionProp"
                 and p.get("name") not in ("HeaderManager.headers", "Asserion.test_strings")]
    if raw_parts:
        d["other_xml"] = "\n".join(raw_parts)[:20000]
    return d


def _sampler_url(props: dict[str, str]) -> str:
    proto = props.get("HTTPSampler.protocol") or "http"
    domain = props.get("HTTPSampler.domain") or ""
    port = props.get("HTTPSampler.port") or ""
    path = props.get("HTTPSampler.path") or ""
    if path.lower().startswith(("http://", "https://")):
        return path
    if not domain:
        return path
    return f"{proto}://{domain}{':' + port if port else ''}{path if path.startswith('/') or not path else '/' + path}"


def tree(root: ET.Element) -> list[dict[str, Any]]:
    def walk(container: ET.Element, prefix: str, parent_on: bool) -> list[dict[str, Any]]:
        nodes = []
        for i, (el, ht) in enumerate(_pairs(container)):
            nid = f"{prefix}{i}"
            on = enabled(el)
            nodes.append({"id": nid, "tag": el.tag, "name": el.get("testname") or el.tag, "enabled": on,
                          "active": on and parent_on, "category": category(el), "plugin": is_plugin(el),
                          "children": walk(ht, nid + "/", on and parent_on) if ht is not None else []})
        return nodes
    return walk(_top(root), "", True)


# ---- what the script does: requests, hosts, load -----------------------------------------------------------------

_VAR = re.compile(r"\$\{([^${}]+)\}")
_PROP_FN = re.compile(r"^__(?:P|property)\(\s*([^,)]*)\s*(?:,\s*([^,)]*)\s*)?(?:,\s*([^,)]*)\s*)?\)$")


def substitute(text: str, variables: dict[str, str]) -> str:
    """Resolve ${var} and ${__P(name,default)} the way JMeter would without -J properties; leave the rest."""
    def one(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        if inner in variables:
            return variables[inner]
        fn = _PROP_FN.match(inner)
        if fn:
            # __P(name, default) and __property(name, var, default)
            default = fn.group(3) if inner.startswith("__property") else fn.group(2)
            if default is not None and default != "":
                return default
        return m.group(0)
    prev = None
    for _ in range(5):  # variables that refer to other variables
        if prev == text:
            break
        prev, text = text, _VAR.sub(one, text)
    return text


def _int(value: str, variables: dict[str, str]) -> int | None:
    v = substitute(value or "", variables).strip()
    try:
        return int(float(v)) if v else None
    except ValueError:
        return None


def summarize(root: ET.Element) -> dict[str, Any]:
    """The requests, hosts, thread groups and load of a script, as far as they can be known before it runs.
    Disabled elements (and everything under them) are left out, as JMeter does."""
    top = _top(root)
    pairs = _pairs(top)
    tp_el, tp_ht = pairs[0] if pairs else (None, None)
    variables: dict[str, str] = {}
    if tp_el is not None:
        for a in _arguments(_args_collection(tp_el, "TestPlan.user_defined_variables")):
            if a["name"]:
                variables[a["name"]] = a["value"]

    requests: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    plugins: list[str] = []
    listeners: list[str] = []
    csv_files: list[str] = []
    unsized: list[str] = []
    seen_plugins: set[str] = set()

    def collect_vars(container: ET.Element) -> None:  # Arguments elements anywhere in scope count as variables
        for el, ht in _pairs(container):
            if not enabled(el):
                continue
            if el.tag == "Arguments":
                for a in _arguments(_collection(el, "Arguments.arguments")):
                    if a["name"]:
                        variables.setdefault(a["name"], a["value"])
            if ht is not None:
                collect_vars(ht)

    if tp_ht is not None:
        collect_vars(tp_ht)

    def defaults_of(container: ET.Element, inherited: dict[str, str]) -> dict[str, str]:
        out = dict(inherited)
        for el, _ in _pairs(container):
            if enabled(el) and el.tag == "ConfigTestElement":
                for k, v in simple_props(el).items():
                    if k.startswith("HTTPSampler.") and v:
                        out[k] = v
        return out

    def walk(container: ET.Element, defaults: dict[str, str], group: str | None, txn: str | None) -> None:
        here = defaults_of(container, defaults)
        for el, ht in _pairs(container):
            if not enabled(el):
                continue
            label = el.get("testname") or el.tag
            if is_plugin(el) and el.tag not in seen_plugins:
                seen_plugins.add(el.tag)
                plugins.append(el.get("testclass") or el.tag)
            if el.tag in LISTENERS:
                listeners.append(label)
            if el.tag == "CSVDataSet":
                f = simple_props(el).get("filename", "")
                if f:
                    csv_files.append(substitute(f, variables))
            if el.tag in UNSIZED_GROUPS:
                unsized.append(label)
            if el.tag in THREAD_GROUPS:
                p = simple_props(el)
                sched = p.get("ThreadGroup.scheduler", "false").lower() == "true"
                loops = _int(p.get("ThreadGroup.main_controller/LoopController.loops", "1"), variables)
                forever = p.get("ThreadGroup.main_controller/LoopController.continue_forever", "false").lower() == "true"
                groups.append({
                    "name": label, "tag": el.tag, "users": _int(p.get("ThreadGroup.num_threads", ""), variables),
                    "ramp_up_s": _int(p.get("ThreadGroup.ramp_time", "0"), variables) or 0,
                    "scheduler": sched,
                    "duration_s": _int(p.get("ThreadGroup.duration", ""), variables) if sched else None,
                    "delay_s": (_int(p.get("ThreadGroup.delay", "0"), variables) or 0) if sched else 0,
                    "loops": loops, "forever": (loops is not None and loops < 0) or (forever and loops is None)})
                if ht is not None:
                    walk(ht, here, label, None)
                continue
            if el.tag in ("HTTPSamplerProxy", "HTTPSampler", "HTTPSampler2"):
                p = {**{k: v for k, v in here.items()}, **{k: v for k, v in simple_props(el).items() if v}}
                url = substitute(_sampler_url(p), variables)
                host = ""
                if url.lower().startswith(("http://", "https://")):
                    authority = url.split("://", 1)[1].split("/", 1)[0].split("?", 1)[0]
                    if "${" not in authority and "$" not in authority:
                        try:
                            host = (urlsplit(url).hostname or "").lower()
                        except ValueError:  # e.g. a port that is not a number
                            host = ""
                requests.append({"name": label, "method": (p.get("HTTPSampler.method") or "GET").upper(),
                                 "url": url, "host": host, "thread_group": group, "transaction": txn})
            if ht is not None:
                walk(ht, here, group, label if el.tag == "TransactionController" else txn)

    if tp_ht is not None:
        walk(tp_ht, {}, None, None)

    main = [g for g in groups if g["tag"] == "ThreadGroup"]
    duration_known = bool(main) and all(g["scheduler"] and g["duration_s"] is not None for g in main)
    total = max(((g["delay_s"] or 0) + (g["duration_s"] or 0) for g in main), default=0) if duration_known else None

    def users_of(tag: str) -> int:
        return sum(g["users"] or 0 for g in groups if g["tag"] == tag)
    # setUp and tearDown groups run before and after the main groups, never together with them
    peak = max(users_of("ThreadGroup"), users_of("SetupThreadGroup"), users_of("PostThreadGroup"))
    hosts = sorted({r["host"] for r in requests if r["host"]})
    return {"requests": requests, "thread_groups": groups, "peak_users": peak, "duration_known": duration_known,
            "total_duration_s": total, "hosts": hosts,
            "unresolved_urls": [r["url"] for r in requests if not r["host"]],
            "variables": variables, "plugins": plugins, "listeners": listeners, "csv_files": csv_files,
            "unsized_groups": unsized}


def problems(root: ET.Element, summary: dict[str, Any] | None = None) -> list[str]:
    """What would make JMeter reject the plan, or make the run unsafe or empty."""
    from .preflight import validate_jmx  # local import: preflight imports this module's users
    s = summary or summarize(root)
    out = validate_jmx(dump(root))
    if s["plugins"]:
        out.append("The script uses JMeter plugins, which this application does not install: "
                   + ", ".join(sorted(set(s["plugins"]))) + ". Replace them with built-in elements.")
    for g in s["thread_groups"]:
        if g["users"] is None:
            out.append(f"Thread group \"{g['name']}\": the number of users is not a number "
                       "(or uses a variable without a default).")
        if g["tag"] == "ThreadGroup" and g["forever"] and not g["scheduler"]:
            out.append(f"Thread group \"{g['name']}\" loops forever without a duration. "
                       "Tick \"Use duration\" and set a duration.")
    if s["unsized_groups"]:
        out.append("Open Model thread groups are not supported yet (they arrive with the workload milestone): "
                   + ", ".join(s["unsized_groups"]) + ".")
    if not any(g["tag"] == "ThreadGroup" for g in s["thread_groups"]) and s["thread_groups"]:
        out.append("The script has only setUp or tearDown thread groups.")
    return out


@functools.lru_cache(maxsize=8)  # scripts can be large: keep only the few being edited or run
def analyze_cached(text: str) -> dict[str, Any]:
    root = parse(text)
    s = summarize(root)
    return {"summary": s, "problems": problems(root, s)}


def analyze(text: str) -> dict[str, Any]:
    """Tree, summary and problems for the editor."""
    root = parse(text)
    s = summarize(root)
    return {"tree": tree(root), "summary": s, "problems": problems(root, s)}


def thread_group_specs(text: str) -> list[ThreadGroupSpec]:
    """The script's main thread groups in the shape the run snapshot and checks use. duration_s is 0 when the
    duration depends on loop counts (duration_known is then False)."""
    s = analyze_cached(text)["summary"]
    return [ThreadGroupSpec(name=g["name"], users=g["users"] or 0, ramp_up_s=g["ramp_up_s"], delay_s=g["delay_s"],
                            duration_s=g["duration_s"] or 0, duration_known=bool(g["scheduler"] and g["duration_s"]))
            for g in s["thread_groups"]]


def for_run(text: str) -> tuple[str, int]:
    """The copy JMeter runs: listeners are disabled, because results are written by the run itself (-l) and GUI
    listeners only use memory in non-GUI mode. Returns the text and how many were disabled. The saved script is
    unchanged."""
    root = parse(text)
    count = 0
    for el in root.iter():
        if _is_element(el) and el.tag in LISTENERS and enabled(el):
            el.set("enabled", "false")
            count += 1
    return dump(root), count


# ---- edits ------------------------------------------------------------------------------------------------------

def _set_simple(el: ET.Element, name: str, value: Any) -> None:
    if "/" in name:  # elementProp/propName
        outer, inner = name.split("/", 1)
        ep = _find_prop(el, outer)
        if ep is None or ep.tag != "elementProp":
            raise _err(f"The element has no property {outer}.")
        _set_simple(ep, inner, value)
        return
    p = _find_prop(el, name)
    text = ("true" if value else "false") if isinstance(value, bool) else str(value if value is not None else "")
    if p is None:
        tag = "boolProp" if isinstance(value, bool) else "stringProp"
        p = ET.SubElement(el, tag, {"name": name})
    elif p.tag not in SIMPLE_PROPS:
        raise _err(f"{name} is not a simple property; edit it in the Raw JMX view.")
    if p.tag == "boolProp":
        text = "true" if str(value).lower() in ("true", "1", "yes", "on") else "false"
    elif p.tag in ("intProp", "longProp") and text.strip():
        if not re.fullmatch(r"-?\d+", text.strip()):
            raise _err(f"{LABELS.get(name, name)} must be a whole number.")
    p.text = text


def _arg_element(name: str, value: str, *, http: bool) -> ET.Element:
    ep = ET.Element("elementProp", {"name": name, "elementType": "HTTPArgument" if http else "Argument"})
    if http:
        ET.SubElement(ep, "boolProp", {"name": "HTTPArgument.always_encode"}).text = "false"
    ET.SubElement(ep, "stringProp", {"name": "Argument.name"}).text = name
    ET.SubElement(ep, "stringProp", {"name": "Argument.value"}).text = value
    ET.SubElement(ep, "stringProp", {"name": "Argument.metadata"}).text = "="
    if http:
        ET.SubElement(ep, "boolProp", {"name": "HTTPArgument.use_equals"}).text = "true"
    return ep


def _replace_args(coll: ET.Element, items: list[dict[str, str]], *, http: bool) -> None:
    for k in list(coll):
        coll.remove(k)
    for it in items:
        if not isinstance(it, dict):
            raise _err("Each variable or parameter needs a name and a value.")
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        coll.append(_arg_element(name, str(it.get("value") or ""), http=http))


def _args_coll_for(el: ET.Element, element_prop: str, *, http: bool) -> ET.Element:
    ep = _find_prop(el, element_prop)
    if ep is None:
        attrs = {"name": element_prop, "elementType": "Arguments"}
        if element_prop == "HTTPsampler.Arguments":
            attrs.update(guiclass="HTTPArgumentsPanel", testclass="Arguments", testname="User Defined Variables")
        ep = ET.SubElement(el, "elementProp", attrs)
    coll = _collection(ep, "Arguments.arguments")
    if coll is None:
        coll = ET.SubElement(ep, "collectionProp", {"name": "Arguments.arguments"})
    return coll


def _set_node(el: ET.Element, op: dict[str, Any]) -> None:
    if "name" in op:
        name = str(op["name"] or "").strip()
        if not name:
            raise _err("The name cannot be empty.")
        el.set("testname", name[:300])
    if "enabled" in op:
        if el.tag == "TestPlan" and not op["enabled"]:
            raise _err("The Test Plan cannot be disabled.")
        el.set("enabled", "true" if op["enabled"] else "false")
    if "comments" in op:
        _set_simple(el, "TestPlan.comments", op["comments"] or "")
    for k, v in (op.get("props") or {}).items():
        _set_simple(el, k, v)
    if "url" in op and op["url"] is not None and el.tag in ("HTTPSamplerProxy", "HTTPSampler", "HTTPSampler2"):
        _set_url(el, str(op["url"]))
    if "variables" in op:
        if el.tag == "TestPlan":
            _replace_args(_args_coll_for(el, "TestPlan.user_defined_variables", http=False), op["variables"], http=False)
        elif el.tag == "Arguments":
            coll = _get_or_add_collection(el, "Arguments.arguments")
            _replace_args(coll, op["variables"], http=False)
    if "params" in op and el.tag in ("HTTPSamplerProxy", "HTTPSampler", "HTTPSampler2"):
        _set_simple(el, "HTTPSampler.postBodyRaw", False)
        _replace_args(_args_coll_for(el, "HTTPsampler.Arguments", http=True), op["params"], http=True)
    if "body" in op and el.tag in ("HTTPSamplerProxy", "HTTPSampler", "HTTPSampler2"):
        body = op["body"]
        _set_simple(el, "HTTPSampler.postBodyRaw", True)
        coll = _args_coll_for(el, "HTTPsampler.Arguments", http=True)
        for k in list(coll):
            coll.remove(k)
        if body:
            arg = _arg_element("", str(body), http=True)
            arg.remove(arg.find("stringProp[@name='Argument.name']"))  # a raw body has no name
            coll.append(arg)
    if "headers" in op and el.tag == "HeaderManager":
        coll = _get_or_add_collection(el, "HeaderManager.headers")
        for k in list(coll):
            coll.remove(k)
        for h in op["headers"] or []:
            name = str(h.get("name") or "").strip()
            if name:
                ep = ET.SubElement(coll, "elementProp", {"name": name, "elementType": "Header"})
                ET.SubElement(ep, "stringProp", {"name": "Header.name"}).text = name
                ET.SubElement(ep, "stringProp", {"name": "Header.value"}).text = str(h.get("value") or "")
    if "patterns" in op and el.tag == "ResponseAssertion":
        coll = _get_or_add_collection(el, "Asserion.test_strings")
        for k in list(coll):
            coll.remove(k)
        for pat in op["patterns"] or []:
            if str(pat):
                ET.SubElement(coll, "stringProp", {"name": str(java_hash(str(pat)))}).text = str(pat)


def java_hash(text: str) -> int:
    """Java's String.hashCode, which JMeter uses to name assertion patterns (stable between saves)."""
    h = 0
    for unit in text.encode("utf-16-be").hex(" ", 2).split():
        h = (31 * h + int(unit, 16)) & 0xFFFFFFFF
    return h - (1 << 32) if h >= (1 << 31) else h


def _set_url(el: ET.Element, url: str) -> None:
    url = url.strip()
    if "${" in url and not url.lower().startswith(("http://", "https://")):
        _set_simple(el, "HTTPSampler.path", url)
        return
    if not url.lower().startswith(("http://", "https://")):
        if url.startswith("/"):  # a path only: the server comes from HTTP Request Defaults
            _set_simple(el, "HTTPSampler.path", url)
            return
        raise _err("The URL must start with http:// or https://, or be a path starting with /.")
    u = urlsplit(url)
    _set_simple(el, "HTTPSampler.protocol", u.scheme)
    _set_simple(el, "HTTPSampler.domain", u.hostname or "")
    _set_simple(el, "HTTPSampler.port", str(u.port or ""))
    _set_simple(el, "HTTPSampler.path", (u.path or "/") + (f"?{u.query}" if u.query else ""))


# Element templates the editor can add. Each is (category, XML builder from values). Only built-in JMeter elements.
def _tpl_http(v: dict[str, Any]) -> str:
    from .jmx import _sampler
    from .models import HttpRequest
    url = str(v.get("url") or "")
    path_only = url.startswith("/")
    req = HttpRequest(name=str(v.get("name") or "HTTP request"), method=str(v.get("method") or "GET").upper(),
                      url=("http://placeholder.invalid" + url) if path_only else (url or "https://example.invalid/"),
                      body=v.get("body") or None)
    xml = _sampler(req)
    if path_only:  # the server comes from HTTP Request Defaults
        xml = xml.replace(_s("HTTPSampler.domain", "placeholder.invalid"), _s("HTTPSampler.domain", "")) \
                 .replace(_s("HTTPSampler.protocol", "http"), _s("HTTPSampler.protocol", ""))
    return xml.split("<hashTree>")[0]


def _tpl_assert(v: dict[str, Any]) -> str:
    field = ASSERT_FIELDS.get(str(v.get("field") or "code"), "Assertion.response_code")
    test_type = ASSERT_TYPES.get(str(v.get("type") or "contains"), 2) | (4 if v.get("negate") else 0)
    pattern = str(v.get("pattern") or ("200" if field == "Assertion.response_code" else ""))
    return (f'<ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" '
            f'testname="{_a(v.get("name") or "Response assertion")}">'
            f'<collectionProp name="Asserion.test_strings"><stringProp name="0">{_a(pattern)}</stringProp>'
            f'</collectionProp>{_s("Assertion.custom_message", "")}{_s("Assertion.test_field", field)}'
            f'{_b("Assertion.assume_success", False)}<intProp name="Assertion.test_type">{test_type}</intProp>'
            f'</ResponseAssertion>')


DATASET_FILE = re.compile(r"^datasets/(DS-[A-Z0-9]{4,20})\.csv$")
SHARE_MODES = {"all": "shareMode.all", "group": "shareMode.group", "thread": "shareMode.thread"}


def _tpl_csv(v: dict[str, Any]) -> str:
    """A CSV Data Set Config that reads a data set of this application. The file is written into the run folder for
    each run (datasets/<id>.csv next to plan.jmx), so the script carries no path of this PC."""
    ds = str(v.get("dataset_id") or "")
    if not re.fullmatch(r"DS-[A-Z0-9]{4,20}", ds):
        raise _err("Choose a data set.")
    names = [str(n) for n in (v.get("variables") or [])]
    if not names:
        raise _err("The data set has no columns.")
    share = SHARE_MODES.get(str(v.get("share") or "all"), "shareMode.all")
    return (f'<CSVDataSet guiclass="TestBeanGUI" testclass="CSVDataSet" '
            f'testname="{_a(v.get("name") or "Test data")}">{_s("filename", f"datasets/{ds}.csv")}'
            f'{_s("fileEncoding", "UTF-8")}{_s("variableNames", ",".join(names))}'
            f'{_b("ignoreFirstLine", True)}{_s("delimiter", ",")}{_b("quotedData", True)}'
            f'{_b("recycle", bool(v.get("recycle", True)))}{_b("stopThread", bool(v.get("stop_thread", False)))}'
            f'{_s("shareMode", share)}</CSVDataSet>')


TEMPLATES: dict[str, tuple[str, str, Any]] = {
    "thread_group": ("thread_group", "Thread group", lambda v: (
        f'<ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="{_a(v.get("name") or "Users")}">'
        f'{_s("ThreadGroup.on_sample_error", "continue")}'
        f'<elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" '
        f'testclass="LoopController">{_b("LoopController.continue_forever", False)}{_s("LoopController.loops", -1)}'
        f'</elementProp>{_s("ThreadGroup.num_threads", int(v.get("users") or 1))}'
        f'{_s("ThreadGroup.ramp_time", int(v.get("ramp_up_s") or 0))}{_b("ThreadGroup.scheduler", True)}'
        f'{_s("ThreadGroup.duration", int(v.get("duration_s") or 60))}{_s("ThreadGroup.delay", 0)}'
        f'{_b("ThreadGroup.same_user_on_next_iteration", True)}</ThreadGroup>')),
    "http_request": ("sampler", "HTTP request", _tpl_http),
    "transaction": ("controller", "Transaction", lambda v: (
        f'<TransactionController guiclass="TransactionControllerGui" testclass="TransactionController" '
        f'testname="{_a(v.get("name") or "Transaction")}">{_b("TransactionController.includeTimers", False)}'
        f'{_b("TransactionController.parent", bool(v.get("parent")))}</TransactionController>')),
    "regex_extractor": ("extractor", "Regular expression extractor", lambda v: (
        f'<RegexExtractor guiclass="RegexExtractorGui" testclass="RegexExtractor" '
        f'testname="{_a(v.get("name") or "Extract " + str(v.get("variable") or "value"))}">'
        f'{_s("RegexExtractor.useHeaders", v.get("field") or "false")}'
        f'{_s("RegexExtractor.refname", v.get("variable") or "value")}{_s("RegexExtractor.regex", v.get("expression") or "")}'
        f'{_s("RegexExtractor.template", "$1$")}{_s("RegexExtractor.default", v.get("default") or "NOT_FOUND")}'
        f'{_b("RegexExtractor.default_empty_value", False)}{_s("RegexExtractor.match_number", v.get("match") or 1)}'
        f'</RegexExtractor>')),
    "json_extractor": ("extractor", "JSON extractor", lambda v: (
        f'<JSONPostProcessor guiclass="JSONPostProcessorGui" testclass="JSONPostProcessor" '
        f'testname="{_a(v.get("name") or "Extract " + str(v.get("variable") or "value"))}">'
        f'{_s("JSONPostProcessor.referenceNames", v.get("variable") or "value")}'
        f'{_s("JSONPostProcessor.jsonPathExprs", v.get("expression") or "$.id")}'
        f'{_s("JSONPostProcessor.match_numbers", v.get("match") or 1)}'
        f'{_s("JSONPostProcessor.defaultValues", v.get("default") or "NOT_FOUND")}</JSONPostProcessor>')),
    "boundary_extractor": ("extractor", "Boundary extractor", lambda v: (
        f'<BoundaryExtractor guiclass="BoundaryExtractorGui" testclass="BoundaryExtractor" '
        f'testname="{_a(v.get("name") or "Extract " + str(v.get("variable") or "value"))}">'
        f'{_s("BoundaryExtractor.useHeaders", v.get("field") or "false")}'
        f'{_s("BoundaryExtractor.refname", v.get("variable") or "value")}'
        f'{_s("BoundaryExtractor.lboundary", v.get("left") or "")}{_s("BoundaryExtractor.rboundary", v.get("right") or "")}'
        f'{_s("BoundaryExtractor.default", v.get("default") or "NOT_FOUND")}'
        f'{_b("BoundaryExtractor.default_empty_value", False)}{_s("BoundaryExtractor.match_number", v.get("match") or 1)}'
        f'</BoundaryExtractor>')),
    "response_assertion": ("assertion", "Response assertion", _tpl_assert),
    "duration_assertion": ("assertion", "Duration assertion", lambda v: (
        f'<DurationAssertion guiclass="DurationAssertionGui" testclass="DurationAssertion" '
        f'testname="{_a(v.get("name") or "Duration assertion")}">'
        f'{_s("DurationAssertion.duration", int(v.get("duration_ms") or 2000))}</DurationAssertion>')),
    "json_assertion": ("assertion", "JSON assertion", lambda v: (
        f'<JSONPathAssertion guiclass="JSONPathAssertionGui" testclass="JSONPathAssertion" '
        f'testname="{_a(v.get("name") or "JSON assertion")}">{_s("JSON_PATH", v.get("expression") or "$")}'
        f'{_s("EXPECTED_VALUE", v.get("expected") or "")}{_b("JSONVALIDATION", bool(v.get("expected")))}'
        f'{_b("EXPECT_NULL", False)}{_b("INVERT", False)}{_b("ISREGEX", False)}</JSONPathAssertion>')),
    "constant_timer": ("timer", "Constant timer (think time)", lambda v: (
        f'<ConstantTimer guiclass="ConstantTimerGui" testclass="ConstantTimer" '
        f'testname="{_a(v.get("name") or "Think time")}">{_s("ConstantTimer.delay", int(v.get("delay_ms") or 1000))}'
        f'</ConstantTimer>')),
    "uniform_timer": ("timer", "Uniform random timer", lambda v: (
        f'<UniformRandomTimer guiclass="UniformRandomTimerGui" testclass="UniformRandomTimer" '
        f'testname="{_a(v.get("name") or "Random think time")}">{_s("ConstantTimer.delay", int(v.get("delay_ms") or 1000))}'
        f'{_s("RandomTimer.range", float(v.get("range_ms") or 1000))}</UniformRandomTimer>')),
    "header_manager": ("config", "HTTP header manager", lambda v: (
        f'<HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="{_a(v.get("name") or "Headers")}">'
        f'<collectionProp name="HeaderManager.headers"/></HeaderManager>')),
    "http_defaults": ("config", "HTTP request defaults", lambda v: (
        f'<ConfigTestElement guiclass="HttpDefaultsGui" testclass="ConfigTestElement" '
        f'testname="{_a(v.get("name") or "HTTP request defaults")}">'
        f'<elementProp name="HTTPsampler.Arguments" elementType="Arguments" guiclass="HTTPArgumentsPanel" '
        f'testclass="Arguments" testname="User Defined Variables"><collectionProp name="Arguments.arguments"/>'
        f'</elementProp>{_s("HTTPSampler.domain", urlsplit(str(v.get("url") or "")).hostname or "")}'
        f'{_s("HTTPSampler.port", urlsplit(str(v.get("url") or "")).port or "")}'
        f'{_s("HTTPSampler.protocol", urlsplit(str(v.get("url") or "")).scheme or "https")}'
        f'{_s("HTTPSampler.path", "")}</ConfigTestElement>')),
    "cookie_manager": ("config", "HTTP cookie manager", lambda v: (
        f'<CookieManager guiclass="CookiePanel" testclass="CookieManager" testname="{_a(v.get("name") or "Cookies")}">'
        f'<collectionProp name="CookieManager.cookies"/>{_b("CookieManager.clearEachIteration", True)}'
        f'</CookieManager>')),
    "csv_dataset": ("config", "Test data (CSV data set)", lambda v: _tpl_csv(v)),
    "user_variables": ("config", "User defined variables", lambda v: (
        f'<Arguments guiclass="ArgumentsPanel" testclass="Arguments" testname="{_a(v.get("name") or "Variables")}">'
        f'<collectionProp name="Arguments.arguments"/></Arguments>')),
}

# Where each kind of element may go (by the parent's category)
ALLOWED_PARENTS = {
    "thread_group": {"plan"},
    "sampler": {"thread_group", "controller"},
    "controller": {"thread_group", "controller"},
    "extractor": {"sampler", "controller", "thread_group", "plan"},
    "assertion": {"sampler", "controller", "thread_group", "plan"},
    "timer": {"sampler", "controller", "thread_group", "plan"},
    "config": {"sampler", "controller", "thread_group", "plan"},
}


def templates() -> list[dict[str, Any]]:
    return [{"type": k, "category": c, "label": label, "parents": sorted(ALLOWED_PARENTS[c])}
            for k, (c, label, _) in TEMPLATES.items()]


def _add(root: ET.Element, op: dict[str, Any]) -> str:
    kind = op.get("type")
    if kind not in TEMPLATES:
        raise _err(f"Unknown element type {kind}.")
    cat, label, build = TEMPLATES[kind]
    parent, parent_ht, container = locate(root, op.get("parent") or "0")
    pcat = category(parent)
    if pcat not in ALLOWED_PARENTS[cat]:
        raise _err(f"A {label.lower()} cannot go under a {pcat.replace('_', ' ')} ({parent.get('testname')}).")
    try:
        el = ET.fromstring(build(op.get("values") or {}))
    except (ValueError, ET.ParseError) as exc:
        raise _err(f"The {label.lower()} could not be created: {exc}") from exc
    if kind == "header_manager" and (op.get("values") or {}).get("headers"):
        _set_node(el, {"headers": op["values"]["headers"]})
    ht = parent_ht if parent_ht is not None else _ensure_tree(container, parent)
    # Config elements, timers and pre-processors read best before the requests they apply to
    index = len([k for k in ht if _is_element(k)])
    if cat == "config":
        index = 0
    kids = list(ht)
    pos = len(kids) if index >= len(kids) else index
    ht.insert(pos, ET.Element("hashTree"))
    ht.insert(pos, el)
    parent_id = op.get("parent") or "0"
    i = [p[0] for p in _pairs(ht)].index(el)
    return f"{parent_id}/{i}"


def _delete(root: ET.Element, node_id: str) -> None:
    el, ht, container = locate(root, node_id)
    if el.tag == "TestPlan":
        raise _err("The Test Plan cannot be deleted.")
    container.remove(el)
    if ht is not None:
        container.remove(ht)


def _move(root: ET.Element, node_id: str, direction: str) -> str:
    el, ht, container = locate(root, node_id)
    pairs = _pairs(container)
    i = [p[0] for p in pairs].index(el)
    j = i - 1 if direction == "up" else i + 1
    if j < 0 or j >= len(pairs):
        return node_id
    other = pairs[j][0]
    for part in (el, ht):
        if part is not None:
            container.remove(part)
    kids = list(container)
    oi = kids.index(other)
    if direction == "up":
        pos = oi
    else:
        other_ht = pairs[j][1]
        pos = kids.index(other_ht) + 1 if other_ht is not None else oi + 1
    if ht is not None:
        container.insert(pos, ht)
    container.insert(pos, el)
    prefix = node_id.rsplit("/", 1)[0] + "/" if "/" in node_id else ""
    return f"{prefix}{j}"


def _duplicate(root: ET.Element, node_id: str) -> str:
    el, ht, container = locate(root, node_id)
    if el.tag == "TestPlan":
        raise _err("The Test Plan cannot be duplicated.")
    kids = list(container)
    pos = kids.index(ht) + 1 if ht is not None else kids.index(el) + 1
    new_el = copy.deepcopy(el)
    new_el.set("testname", f"{el.get('testname') or el.tag} (copy)")
    container.insert(pos, copy.deepcopy(ht) if ht is not None else ET.Element("hashTree"))
    container.insert(pos, new_el)
    prefix = node_id.rsplit("/", 1)[0] + "/" if "/" in node_id else ""
    return f"{prefix}{[p[0] for p in _pairs(container)].index(new_el)}"


def apply(text: str, ops: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply editor operations in order, all or nothing. Returns the new text, the analysis and the node selected by
    the last operation (the added, moved or duplicated element)."""
    root = parse(text)
    selected: str | None = None
    for op in ops or []:
        kind = op.get("op")
        if kind == "set":
            el, _, _ = locate(root, op["node"])
            _set_node(el, op)
            selected = op["node"]
        elif kind == "add":
            selected = _add(root, op)
        elif kind == "delete":
            _delete(root, op["node"])
            selected = None
        elif kind == "move":
            selected = _move(root, op["node"], op.get("direction") or "up")
        elif kind == "duplicate":
            selected = _duplicate(root, op["node"])
        elif kind == "bind":
            bind_field(root, op)
            selected = op["node"]
        else:
            raise _err(f"Unknown edit {kind}.")
    out = dump(root)
    result = analyze(out)
    result.update(script=out, selected=selected)
    return result


def node_details(text: str, node_id: str) -> dict[str, Any]:
    el, _, _ = locate(parse(text), node_id)
    return {"id": node_id, **details(el)}


def diff(a: str, b: str, a_label: str, b_label: str) -> str:
    return "".join(difflib.unified_diff(a.splitlines(keepends=True), b.splitlines(keepends=True),
                                        fromfile=a_label, tofile=b_label, n=3))


# ---- 23.3.25: test data bound to request fields ------------------------------------------------------------------

SAMPLER_TAGS = ("HTTPSamplerProxy", "HTTPSampler", "HTTPSampler2")


def data_sets(root: ET.Element) -> list[dict[str, Any]]:
    """The CSV Data Set Config elements of the script, with the data set of this application each one reads."""
    out = []

    def walk(container: ET.Element, prefix: str, parent_on: bool, scope: str) -> None:
        for i, (el, ht) in enumerate(_pairs(container)):
            nid = f"{prefix}{i}"
            on = enabled(el) and parent_on
            here = el.get("testname") or el.tag if el.tag in THREAD_GROUPS else scope
            if el.tag == "CSVDataSet":
                p = simple_props(el)
                m = DATASET_FILE.match(p.get("filename", "").replace("\\", "/"))
                out.append({"node": nid, "name": el.get("testname") or "", "enabled": on, "scope": scope,
                            "dataset_id": m.group(1) if m else None, "filename": p.get("filename", ""),
                            "variables": [x.strip() for x in p.get("variableNames", "").split(",") if x.strip()],
                            "share": next((k for k, v in SHARE_MODES.items() if v == p.get("shareMode")), "all"),
                            "recycle": p.get("recycle", "true").lower() == "true",
                            "stop_thread": p.get("stopThread", "false").lower() == "true"})
            if ht is not None:
                walk(ht, nid + "/", on, here)
    walk(_top(root), "", True, "whole test plan")
    return out


def _raw_body(el: ET.Element) -> tuple[ET.Element | None, str | None]:
    if simple_props(el).get("HTTPSampler.postBodyRaw", "false").lower() != "true":
        return None, None
    coll = _args_collection(el, "HTTPsampler.Arguments")
    for ep in (coll if coll is not None else []):
        if _is_element(ep):
            for q in ep:
                if _is_element(q) and q.get("name") == "Argument.value":
                    return q, q.text or ""
    return None, None


def _form_pairs(body: str) -> list[tuple[str, str]] | None:
    """key=value&... pairs of a form body, or None when the body is not a form. ${...} is kept whole."""
    if not body or body.lstrip()[:1] in "{[<" or "=" not in body:
        return None
    parts, depth, cur = [], 0, ""
    for ch in body:
        depth += ch == "{" and cur.endswith("$")
        if ch == "}" and depth:
            depth -= 1
        if ch == "&" and not depth:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    out = []
    for p in parts:
        if "=" not in p:
            return None
        k, v = p.split("=", 1)
        if not re.fullmatch(r"[A-Za-z0-9_.\-\[\]%]+", k):
            return None
        out.append((k, v))
    return out


def _json_leaves(data: Any, path: str = "$") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if isinstance(data, dict):
        for k, v in data.items():
            out += _json_leaves(v, f"{path}.{k}" if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(k))
                                else f"{path}[{json_quote(str(k))}]")
    elif isinstance(data, list):
        for i, v in enumerate(data):
            out += _json_leaves(v, f"{path}[{i}]")
    else:
        out.append((path, data))
    return out


_EXPR = "__QA_EXPR_{}__"
_GROOVY_JSON = r"\$\{__groovy\(groovy\.json\.JsonOutput\.toJson\(vars\.get\('([^']+)'\)\)\)\}"


def _json_template_load(body: str) -> tuple[Any, list[str]]:
    """Parse a JSON body in which JMeter expressions such as ${__groovy(...)} or ${count} stand in value positions
    (outside quotes). Each one becomes a placeholder string; _json_template_dump puts them back."""
    import json
    out, exprs, i, in_str = [], [], 0, False
    while i < len(body):
        ch = body[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < len(body):
                out.append(body[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if body.startswith("${", i):
            depth, j = 0, i
            while j < len(body):
                if body.startswith("${", j):
                    depth += 1
                    j += 2
                    continue
                if body[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            exprs.append(body[i:j + 1])
            out.append(json.dumps(_EXPR.format(len(exprs) - 1)))
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return json.loads("".join(out)), exprs


def _json_template_dump(data: Any, exprs: list[str]) -> str:
    import json
    text = json.dumps(data, ensure_ascii=False)
    for n, e in enumerate(exprs):
        text = text.replace(json.dumps(_EXPR.format(n)), e)
    return text


def json_quote(text: str) -> str:
    import json
    return json.dumps(text, ensure_ascii=False)


def _unquote(v: str) -> str:
    from urllib.parse import unquote_plus
    return v if "${" in v else unquote_plus(v)


def fields(text: str) -> list[dict[str, Any]]:
    """Request values that can be bound to a data column: form and query parameters, JSON body values, headers."""
    import json
    root = parse(text)
    out: list[dict[str, Any]] = []

    def add(nid: str, sampler: str, kind: str, key: str, label: str, value: Any) -> None:
        out.append({"node": nid, "sampler": sampler, "kind": kind, "key": key, "label": label,
                    "value": value if isinstance(value, str) else json.dumps(value),
                    "bound": isinstance(value, str) and "${" in value})

    def walk(container: ET.Element, prefix: str, parent_on: bool) -> None:
        for i, (el, ht) in enumerate(_pairs(container)):
            nid = f"{prefix}{i}"
            on = enabled(el) and parent_on
            if on and el.tag in SAMPLER_TAGS:
                name = el.get("testname") or el.tag
                props = simple_props(el)
                path = props.get("HTTPSampler.path", "")
                if "?" in path:
                    for k, v in (_form_pairs(path.split("?", 1)[1]) or []):
                        add(nid, name, "query", k, f"query {k}", _unquote(v))
                _, body = _raw_body(el)
                if body is not None:
                    pairs = _form_pairs(body)
                    if pairs is not None:
                        for k, v in pairs:
                            add(nid, name, "form", k, f"form {_unquote(k)}", _unquote(v))
                    else:
                        try:
                            data, exprs = _json_template_load(body)
                        except ValueError:
                            data, exprs = None, []
                        if data is not None:
                            for p, v in _json_leaves(data):
                                if v is None or isinstance(v, (dict, list)):
                                    continue
                                m = re.fullmatch(r"__QA_EXPR_(\d+)__", v) if isinstance(v, str) else None
                                expr = exprs[int(m.group(1))] if m else v
                                g = re.fullmatch(_GROOVY_JSON, expr) if m else None
                                add(nid, name, "json", p, f"JSON {p}", "${" + g.group(1) + "}" if g else expr)
                else:
                    for a in _arguments(_args_collection(el, "HTTPsampler.Arguments")):
                        if a["name"]:
                            add(nid, name, "param", a["name"], f"parameter {a['name']}", a["value"])
                for cel, _ in (_pairs(ht) if ht is not None else []):
                    if cel.tag == "HeaderManager" and enabled(cel):
                        for h in _headers(cel):
                            if h["name"].lower() not in ("content-type", "accept", "user-agent", "accept-language"):
                                add(nid, name, "header", h["name"], f"header {h['name']}", h["value"])
            if ht is not None:
                walk(ht, nid + "/", on)
    walk(_top(root), "", True)
    return out


def _json_set(data: Any, path: str, value: Any) -> Any:
    import json
    parts = re.findall(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]|\[(\"(?:[^\"\\\\]|\\\\.)*\")\]", path[1:])
    if not path.startswith("$") or not parts:
        raise _err(f"Unknown JSON path {path}.")
    cur = data
    for n, (key, idx, quoted) in enumerate(parts):
        k: Any = key or (int(idx) if idx else json.loads(quoted))
        last = n == len(parts) - 1
        try:
            if last:
                cur[k] = value
            else:
                cur = cur[k]
        except (KeyError, IndexError, TypeError) as exc:
            raise _err(f"The request body has no value at {path}.") from exc
    return data


def bind_field(root: ET.Element, op: dict[str, Any]) -> None:
    """Replace one request value with ${variable}: op {node, kind, key, var, raw}. raw (numbers, true/false) puts the
    variable without quotes into a JSON body. Form and query values are URL-encoded when the request is sent."""
    import json
    var = str(op.get("var") or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,59}", var):
        raise _err("Choose the data column to use.")
    el, ht, _ = locate(root, op["node"])
    if el.tag not in SAMPLER_TAGS:
        raise _err("Only values of HTTP requests can be bound.")
    kind, key = op.get("kind"), str(op.get("key") or "")
    ref = "${" + var + "}"
    enc = "${__urlencode(" + ref + ")}"
    if kind in ("form", "query"):
        if kind == "form":
            q, body = _raw_body(el)
            source = body
        else:
            props = simple_props(el)
            q, source = None, props.get("HTTPSampler.path", "")
            if "?" not in source:
                raise _err("The request has no query parameters.")
        head, query = (source.split("?", 1) if kind == "query" else ("", source))
        pairs = _form_pairs(query or "")
        if not pairs or key not in [k for k, _ in pairs]:
            raise _err(f"The request has no {kind} value named {key}.")
        query = "&".join(f"{k}={enc if k == key else v}" for k, v in pairs)
        if kind == "form":
            assert q is not None
            q.text = query
        else:
            _set_simple(el, "HTTPSampler.path", f"{head}?{query}")
        return
    if kind == "json":
        q, body = _raw_body(el)
        if q is None:
            raise _err("The request has no JSON body.")
        marker = "__QA_BIND_7f3a__"
        try:
            data, exprs = _json_template_load(body or "null")
        except ValueError as exc:
            raise _err("The request body is not JSON.") from exc
        data = _json_set(data, key, marker)
        # A text value is written by JMeter's built-in Groovy JSON encoder, so quotes, backslashes and line breaks
        # in the data stay valid JSON; numbers and true/false (raw) go in as they are
        value = ref if op.get("raw") else "${__groovy(groovy.json.JsonOutput.toJson(vars.get('" + var + "')))}"
        q.text = _json_template_dump(data, exprs).replace(f'"{marker}"', value)
        return
    if kind == "param":
        coll = _args_collection(el, "HTTPsampler.Arguments")
        for ep in (coll if coll is not None else []):
            if not _is_element(ep):
                continue
            vals = {x.get("name"): x for x in ep if _is_element(x)}
            n = vals.get("Argument.name")
            if n is not None and (n.text or "") == key:
                vals["Argument.value"].text = ref
                if "HTTPArgument.always_encode" in vals:
                    vals["HTTPArgument.always_encode"].text = "true"
                return
        raise _err(f"The request has no parameter named {key}.")
    if kind == "header":
        for cel, _ in (_pairs(ht) if ht is not None else []):
            if cel.tag == "HeaderManager":
                coll = _collection(cel, "HeaderManager.headers")
                for ep in (coll if coll is not None else []):
                    vals = {x.get("name"): x for x in ep if _is_element(x)}
                    if (vals.get("Header.name") is not None and (vals["Header.name"].text or "").lower() == key.lower()
                            and vals.get("Header.value") is not None):
                        vals["Header.value"].text = ref
                        return
        raise _err(f"The request has no header named {key}.")
    raise _err(f"Unknown field kind {kind}.")
