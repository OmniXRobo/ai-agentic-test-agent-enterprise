"""23.3.26: workload shapes, the request-rate model and the one-user validation run.

A workload is kept apart from the script. Its shapes (load, stress, spike, soak) are built from plain JMeter thread
groups, so no plugin is needed: the main thread groups of the script are copied once per phase of the shape, with the
users, ramp-up, delay and duration of that phase. The rate model adds the built-in Precise Throughput Timer to each
copy: it holds a request-rate target with a fixed pool of users. Users are never added to reach a rate, so if responses
slow down the achieved rate drops (a closed model) and the results show the target next to what was achieved.

The validation run runs every thread group once with one user, one after another, and adds checks that each value a
request's extractor should find was found (correlation). Both only change the copy JMeter runs; the saved script is
unchanged. This module is pure: it works on JMX text."""

from __future__ import annotations

import base64
import copy
import json
import math
from typing import Any
from xml.etree import ElementTree as ET

from ..errors import QAError
from .models import Workload
from . import script as js

SHAPES = {
    "script": "As set in the script (its own thread groups)",
    "load": "Load: ramp up to the expected users, then hold",
    "stress": "Stress: add users in steps to find the limit",
    "spike": "Spike: a sudden burst on top of a steady baseline",
    "soak": "Soak: a steady load for hours (memory leaks, slow degradation)",
}
MODELS = {
    "users": "Concurrent users (each user repeats the flow; the request rate follows from the response times)",
    "rate": "Request-rate target (a fixed pool of users is paced to a number of requests per second)",
}
RATE_SHAPES = ("load", "soak")
THROUGHPUT_TIMERS = ("ConstantThroughputTimer", "PreciseThroughputTimer")
EXTRACTORS = ("RegexExtractor", "JSONPostProcessor", "BoundaryExtractor")


def _err(message: str, code: str = "PERF_WORKLOAD_INVALID", status: int = 422) -> QAError:
    return QAError(code, message, status=status)


# ---- the shape ----------------------------------------------------------------------------------------------------

def groups(w: Workload) -> list[dict[str, Any]]:
    """The phases of the shape as thread groups: name, users, ramp-up, delay, duration and (rate model) the rate."""
    rate = w.target_rps if w.model == "rate" else None
    if w.shape in ("load", "soak"):
        return [{"name": "Users", "users": w.users, "ramp_up_s": w.ramp_up_s, "delay_s": 0,
                 "duration_s": w.ramp_up_s + w.hold_s, "rate_rps": rate}]
    if w.shape == "stress":
        total = w.steps * w.step_s
        return [{"name": f"Step {i + 1}", "users": w.users, "ramp_up_s": min(w.ramp_up_s, w.step_s),
                 "delay_s": i * w.step_s, "duration_s": total - i * w.step_s, "rate_rps": None}
                for i in range(w.steps)]
    if w.shape == "spike":
        return [{"name": "Baseline", "users": w.users, "ramp_up_s": w.ramp_up_s, "delay_s": 0,
                 "duration_s": w.ramp_up_s + w.hold_s, "rate_rps": None},
                {"name": "Spike", "users": w.spike_users, "ramp_up_s": min(w.spike_ramp_s, w.spike_s),
                 "delay_s": w.spike_at_s, "duration_s": w.spike_s, "rate_rps": None}]
    return []


def _users_at(gs: list[dict[str, Any]], t: float) -> float:
    total = 0.0
    for g in gs:
        start, end = g["delay_s"], g["delay_s"] + g["duration_s"]
        if t < start or t >= end:
            continue
        ramp = g["ramp_up_s"]
        total += g["users"] if ramp <= 0 or t >= start + ramp else g["users"] * (t - start) / ramp
    return total


def curve(w: Workload) -> list[list[float]]:
    """[seconds, users] points of the planned users over time, for the preview chart."""
    gs = groups(w)
    if not gs:
        return []
    marks = {0.0}
    for g in gs:
        s, e = g["delay_s"], g["delay_s"] + g["duration_s"]
        marks.update({s, s + g["ramp_up_s"], e})
    out: list[list[float]] = []
    for t in sorted(marks):
        if t > 0:
            out.append([t, round(_users_at(gs, t - 1e-6), 2)])  # just before a change: the end of a level
        out.append([t, round(_users_at(gs, t), 2)])
    return out


def peak(w: Workload) -> int:
    pts = curve(w)
    return int(math.ceil(max((u for _, u in pts), default=0)))


def duration(w: Workload) -> int:
    return max((g["delay_s"] + g["duration_s"] for g in groups(w)), default=0)


def check(w: Workload) -> list[str]:
    """What makes the workload unusable."""
    out = []
    if w.shape == "spike" and w.spike_at_s + w.spike_s > w.ramp_up_s + w.hold_s:
        out.append("The spike must end before the baseline ends (ramp-up + hold).")
    if w.shape == "stress" and w.ramp_up_s > w.step_s:
        out.append("The ramp-up of a step cannot be longer than the step.")
    if w.model == "rate":
        if w.shape not in RATE_SHAPES:
            out.append("A request-rate target works with the load and soak shapes (a steady rate). Use concurrent "
                       "users for stress and spike tests.")
        if not w.target_rps:
            out.append("Enter the target in requests per second.")
    if w.shape != "script" and duration(w) > 7 * 86400:
        out.append("A test can last at most 7 days.")
    return out


def capacity(w: Workload, validation: dict[str, Any] | None) -> dict[str, Any] | None:
    """How many requests per second the user pool can make at most, from the last validation run (one user's pace:
    requests in one iteration over the time that iteration took, think time included). Only an estimate: under
    load the responses are usually slower."""
    if not validation or validation.get("status") != "PASS":
        return None
    reqs, secs = validation.get("requests") or 0, validation.get("iteration_s") or 0
    if not reqs or not secs:
        return None
    per_user = reqs / secs
    out: dict[str, Any] = {"per_user_rps": round(per_user, 3), "pool_max_rps": round(per_user * w.users, 2),
                           "from_version": validation.get("script_version")}
    if w.model == "rate" and w.target_rps:
        out["users_needed"] = int(math.ceil(w.target_rps / per_user))
        out["enough"] = per_user * w.users >= w.target_rps
    return out


def notes(w: Workload, validation: dict[str, Any] | None = None) -> list[str]:
    out = []
    if w.shape == "soak" and w.hold_s < 3600:
        out.append("A soak test usually holds the load for hours; a short one may not show slow degradation.")
    cap = capacity(w, validation)
    if w.model == "rate" and cap:
        if cap["enough"]:
            out.append(f"From the validation run one user makes about {cap['per_user_rps']} requests per second, so "
                       f"{w.users} users can reach about {cap['pool_max_rps']} (target {w.target_rps}).")
        else:
            out.append(f"From the validation run one user makes about {cap['per_user_rps']} requests per second, so "
                       f"{w.users} users reach at most about {cap['pool_max_rps']}, below the target of "
                       f"{w.target_rps}. About {cap['users_needed']} users are needed (more if responses slow down).")
    elif w.model == "rate":
        out.append("Run the one-user validation run to estimate how many users the target rate needs.")
    return out


def describe(w: Workload | None) -> str:
    if not w or w.shape == "script":
        return "closed (concurrent virtual users, as set in the script; not a request-rate target)"
    if w.model == "rate":
        return (f"request-rate target {w.target_rps} req/s with a pool of at most {w.users} concurrent users "
                "(Precise Throughput Timer); a closed model: if responses slow down, the achieved rate drops "
                "instead of users being added")
    return f"closed (concurrent virtual users, {w.shape} shape; not a request-rate target)"


def preview(w: Workload, validation: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"workload": w.model_dump(), "groups": groups(w), "curve": curve(w), "peak_users": peak(w),
            "duration_s": duration(w), "problems": check(w), "notes": notes(w, validation),
            "capacity": capacity(w, validation), "load_model": describe(w)}


# ---- applying it to the copy JMeter runs --------------------------------------------------------------------------

def _main_groups(root: ET.Element) -> tuple[ET.Element, list[tuple[ET.Element, ET.Element | None]]]:
    top = js._top(root)
    pairs = js._pairs(top)
    if not pairs:
        raise _err("The script has no Test Plan.")
    _, tp_ht = pairs[0]
    if tp_ht is None:
        raise _err("The Test Plan has no thread groups.")
    return tp_ht, [(el, ht) for el, ht in js._pairs(tp_ht) if el.tag == "ThreadGroup" and js.enabled(el)]


def _loop(el: ET.Element) -> ET.Element:
    ep = js._find_prop(el, "ThreadGroup.main_controller")
    if ep is None or ep.tag != "elementProp":
        ep = ET.SubElement(el, "elementProp", {"name": "ThreadGroup.main_controller", "elementType": "LoopController",
                                                "guiclass": "LoopControlPanel", "testclass": "LoopController"})
    return ep


def _set_group(el: ET.Element, *, users: int, ramp: int, scheduler: bool, duration_s: int | str,
               delay_s: int | str, loops: int) -> None:
    js._set_simple(el, "ThreadGroup.num_threads", users)
    js._set_simple(el, "ThreadGroup.ramp_time", ramp)
    js._set_simple(el, "ThreadGroup.scheduler", scheduler)
    js._set_simple(el, "ThreadGroup.duration", duration_s)
    js._set_simple(el, "ThreadGroup.delay", delay_s)
    loop = _loop(el)
    js._set_simple(loop, "LoopController.loops", loops)
    js._set_simple(loop, "LoopController.continue_forever", False)


def _split(total: int, weights: list[int]) -> list[int]:
    """`total` users over groups in proportion to their weights (largest remainder, so the sum stays `total`)."""
    s = sum(weights) or len(weights)
    raw = [total * (wt or 1) / s for wt in weights] if sum(weights) else [total / len(weights)] * len(weights)
    out = [int(math.floor(r)) for r in raw]
    for i in sorted(range(len(raw)), key=lambda i: -(raw[i] - out[i]))[: total - sum(out)]:
        out[i] += 1
    return out


def _ptt(rate_rps: float, duration_s: int) -> ET.Element:
    """Precise Throughput Timer: `rate_rps` samples per second from all samplers of its thread group."""
    per_min = round(rate_rps * 60, 4)
    xml = (f'<PreciseThroughputTimer guiclass="TestBeanGUI" testclass="PreciseThroughputTimer" '
           f'testname="Request rate {js._a(round(rate_rps, 4))}/s (added by the workload)">'
           f'<doubleProp><name>throughput</name><value>{per_min}</value><savedValue>0.0</savedValue></doubleProp>'
           f'<intProp name="throughputPeriod">60</intProp><longProp name="duration">{int(duration_s)}</longProp>'
           f'<intProp name="batchSize">1</intProp><intProp name="batchThreadDelay">0</intProp>'
           f'<longProp name="randomSeed">0</longProp><longProp name="exactLimit">10000</longProp>'
           f'<doubleProp><name>allowedThroughputSurplus</name><value>1.0</value><savedValue>0.0</savedValue>'
           f'</doubleProp></PreciseThroughputTimer>')
    return ET.fromstring(xml)


def apply(text: str, w: Workload | None) -> tuple[str, dict[str, Any]]:
    """The script with the workload in place of its main thread groups' settings. Returns the text and what was
    applied. With no workload (or "script") the text is returned unchanged."""
    if not w or w.shape == "script":
        return text, {"applied": False}
    problems = check(w)
    if problems:
        raise _err(" ".join(problems))
    root = js.parse(text)
    tp_ht, mains = _main_groups(root)
    if not mains:
        raise _err("The script has no enabled thread group to apply the workload to.")
    summary = js.summarize(root)
    weights_by_name = {g["name"]: max(1, g["users"] or 1) for g in summary["thread_groups"] if g["tag"] == "ThreadGroup"}
    weights = [weights_by_name.get(el.get("testname") or el.tag, 1) for el, _ in mains]
    applied, skipped = [], []
    phases = groups(w)
    for (el, ht), wt in zip(mains, weights):
        kids = list(tp_ht)
        pos = kids.index(el)
        tp_ht.remove(el)
        if ht is not None:
            tp_ht.remove(ht)
        base = el.get("testname") or "Thread Group"
        for ph in phases:
            users = _split(ph["users"], weights)[[e for e, _ in mains].index(el)]
            if users < 1:
                skipped.append(f"{base} · {ph['name']}")
                continue
            new_el = copy.deepcopy(el)
            new_ht = copy.deepcopy(ht) if ht is not None else ET.Element("hashTree")
            new_el.set("testname", base if len(phases) == 1 else f"{base} · {ph['name']}")
            _set_group(new_el, users=users, ramp=ph["ramp_up_s"], scheduler=True, duration_s=ph["duration_s"],
                       delay_s=ph["delay_s"], loops=-1)
            rate = None
            if ph["rate_rps"]:
                rate = ph["rate_rps"] * wt / sum(weights)
                new_ht.insert(0, ET.Element("hashTree"))
                new_ht.insert(0, _ptt(rate, ph["duration_s"]))
            tp_ht.insert(pos, new_ht)
            tp_ht.insert(pos, new_el)
            pos += 2
            applied.append({"name": new_el.get("testname"), "users": users, "ramp_up_s": ph["ramp_up_s"],
                            "delay_s": ph["delay_s"], "duration_s": ph["duration_s"],
                            "rate_rps": round(rate, 4) if rate else None})
    info: dict[str, Any] = {"applied": True, "shape": w.shape, "model": w.model, "thread_groups": applied}
    if skipped:
        info["skipped"] = skipped
        info["note"] = (f"Too few users to give every thread group one: {', '.join(skipped)} did not run. "
                        "Raise the users.")
    return js.dump(root), info


# ---- the one-user validation run ----------------------------------------------------------------------------------

def _extractor_checks(el: ET.Element) -> list[list[str]]:
    """[variable, default] pairs an extractor sets when it finds nothing. Extractors that collect all matches
    (match number -1) are left out: they set name_matchNr instead."""
    p = js.simple_props(el)
    if el.tag == "JSONPostProcessor":
        names = [x.strip() for x in p.get("JSONPostProcessor.referenceNames", "").split(";")]
        defaults = p.get("JSONPostProcessor.defaultValues", "").split(";")
        matches = p.get("JSONPostProcessor.match_numbers", "").split(";")
        out = []
        for i, n in enumerate(names):
            m = (matches[i] if i < len(matches) else (matches[0] if matches else "")).strip()
            if n and "${" not in n and m != "-1":
                out.append([n, defaults[i] if i < len(defaults) else ""])
        return out
    prefix = "RegexExtractor" if el.tag == "RegexExtractor" else "BoundaryExtractor"
    name = p.get(f"{prefix}.refname", "").strip()
    if not name or "${" in name or p.get(f"{prefix}.match_number", "1").strip() == "-1":
        return []
    default = "" if p.get(f"{prefix}.default_empty_value", "false").lower() == "true" else p.get(f"{prefix}.default", "")
    return [[name, default]]


def correlation_checks(root: ET.Element) -> dict[str, list[list[str]]]:
    """Request name -> the values its own extractors must find. Extractors on a controller or thread group are left
    out: they read every response in their scope, so a miss on one response is not an error."""
    out: dict[str, list[list[str]]] = {}

    def walk(container: ET.Element) -> None:
        for el, ht in js._pairs(container):
            if not js.enabled(el):
                continue
            if el.tag in js.SAMPLER_TAGS and ht is not None:
                for x, _ in js._pairs(ht):
                    if js.enabled(x) and x.tag in EXTRACTORS:
                        out.setdefault(el.get("testname") or el.tag, []).extend(_extractor_checks(x))
            if ht is not None:
                walk(ht)

    walk(js._top(root))
    return {k: v for k, v in out.items() if v}


_GROOVY = """def checks = new groovy.json.JsonSlurper().parseText(new String(java.util.Base64.decoder.decode('{b64}'), 'UTF-8'))
def wanted = checks[SampleResult.getSampleLabel()]
if (wanted != null && SampleResult.isSuccessful()) {{
    def missing = wanted.findAll {{ c -> def v = vars.get(c[0]); v == null || v == c[1] }}
    if (missing) {{
        AssertionResult.setFailure(true)
        AssertionResult.setFailureMessage('Correlation: ' + missing.collect {{ it[0] }}.join(', ') + ' not found in the response (the extractor used its default value)')
    }}
}}"""


def _correlation_assertion(checks: dict[str, list[list[str]]]) -> ET.Element:
    b64 = base64.b64encode(json.dumps(checks, ensure_ascii=False).encode("utf-8")).decode("ascii")
    el = ET.Element("JSR223Assertion", {"guiclass": "TestBeanGUI", "testclass": "JSR223Assertion",
                                         "testname": "Correlation check (added by the validation run)"})
    for name, value in (("scriptLanguage", "groovy"), ("parameters", ""), ("filename", ""), ("cacheKey", "true"),
                        ("script", _GROOVY.format(b64=b64))):
        ET.SubElement(el, "stringProp", {"name": name}).text = value
    return el


def for_validation(text: str) -> tuple[str, dict[str, Any]]:
    """The copy for the one-user validation run: every thread group runs once with one user, one after another;
    throughput timers are switched off (they would pace a single user); correlation checks are added."""
    root = js.parse(text)
    top = js._top(root)
    pairs = js._pairs(top)
    if not pairs:
        raise _err("The script has no Test Plan.")
    tp, tp_ht = pairs[0]
    js._set_simple(tp, "TestPlan.serialize_threadgroups", True)
    if tp_ht is None:
        raise _err("The Test Plan has no thread groups.")
    group_names, timers_off = [], 0
    for el, _ in js._pairs(tp_ht):
        if el.tag in js.THREAD_GROUPS and js.enabled(el):
            _set_group(el, users=1, ramp=0, scheduler=False, duration_s="", delay_s="", loops=1)
            js._set_simple(el, "ThreadGroup.same_user_on_next_iteration", True)
            group_names.append(el.get("testname") or el.tag)
    for el in root.iter():
        if js._is_element(el) and el.tag in THROUGHPUT_TIMERS and js.enabled(el):
            el.set("enabled", "false")
            timers_off += 1
    checks = correlation_checks(root)
    if checks:
        tp_ht.insert(0, ET.Element("hashTree"))
        tp_ht.insert(0, _correlation_assertion(checks))
    return js.dump(root), {"thread_groups": group_names, "throughput_timers_off": timers_off,
                           "correlation_checks": {k: [c[0] for c in v] for k, v in checks.items()}}


def validation_result(rows: list[dict[str, str]], transactions: set[str]) -> dict[str, Any]:
    """PASS when every request succeeded (status, assertions and correlation checks). Samples of transaction
    controllers are listed but not counted as requests."""
    items, first, last = [], None, None
    for r in rows:
        if r.get("timeStamp") in (None, "", "timeStamp"):
            continue
        ts, el = int(float(r.get("timeStamp") or 0)), int(float(r.get("elapsed") or 0))
        first = ts if first is None else min(first, ts)
        last = ts + el if last is None else max(last, ts + el)
        label = r.get("label") or ""
        ok = (r.get("success") or "").lower() == "true"
        msg = (r.get("failureMessage") or "").strip()
        items.append({"label": label, "transaction": label in transactions, "ok": ok,
                      "code": (r.get("responseCode") or "").strip(), "message": (r.get("responseMessage") or "")[:200],
                      "failure": msg[:500], "elapsed_ms": el, "thread": r.get("threadName") or "",
                      "correlation": msg.startswith("Correlation:")})
    reqs = [i for i in items if not i["transaction"]]
    failed = [i for i in reqs if not i["ok"]]
    return {"status": "PASS" if reqs and not failed else "FAIL", "requests": len(reqs), "failed": len(failed),
            "correlation_failures": sum(1 for i in failed if i["correlation"]),
            "iteration_s": round(((last or 0) - (first or 0)) / 1000.0, 3), "samples": items[:1000],
            "truncated": len(items) > 1000}


def achieved_rate(results: Any, transactions: set[str], ramp_up_s: int) -> tuple[float | None, int]:
    """Requests per second in a JTL (CSV) after the ramp-up, transaction samples left out. Streams the file."""
    import csv
    from array import array
    first = last = None
    rows = array("q")  # compact: a soak run can have millions of samples
    with open(results, newline="", encoding="utf-8", errors="replace") as fh:
        for r in csv.DictReader(fh):
            try:
                ts = int(float(r.get("timeStamp") or ""))
            except ValueError:
                continue
            first = ts if first is None else min(first, ts)
            last = ts if last is None else max(last, ts)
            if (r.get("label") or "") not in transactions:
                rows.append(ts)
    if first is None or last is None:
        return None, 0
    start = first + ramp_up_s * 1000
    counted = sum(1 for ts in rows if ts >= start)
    span = (last - start) / 1000.0
    return (round(counted / span, 3) if span >= 1 else None), counted
