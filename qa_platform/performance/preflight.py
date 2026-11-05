"""23.3.22: checks before a performance run starts.

Each check is ``{"id", "name", "status": PASS|WARN|FAIL, "detail", "fix"}``. Any FAIL blocks the run; WARN is shown and
recorded. Nothing here sends traffic to the target: the one-user validation run arrives with the workload milestone.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from ..config import Settings
from . import tools
from .models import PerfEnvironment, PerfPlan

MIN_JMETER = (5, 5)
MIN_JAVA = 8
DEFAULT_MIN_FREE_MB = 1024
DEFAULT_MAX_USERS = 50  # used when a scenario has no environment
REQUIRED_PLUGINS: list[str] = []  # generated plans use built-in JMeter elements only
UNKNOWN_DURATION_TIMEOUT_S = 2 * 3600  # a script that runs a number of loops: end it after this long


def _check(cid: str, name: str, status: str, detail: str, fix: str = "") -> dict[str, Any]:
    return {"id": cid, "name": name, "status": status, "detail": detail, "fix": fix}


def jmeter_version(jmeter: str) -> str | None:
    """Read the version from ApacheJMeter_core.jar's manifest (no JVM start), else from the folder name."""
    home = Path(jmeter).resolve().parent.parent
    core = home / "lib" / "ext" / "ApacheJMeter_core.jar"
    if core.exists():
        try:
            with zipfile.ZipFile(core) as z:
                text = z.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
            m = re.search(r"^Implementation-Version:\s*(\S+)", text, re.M)
            if m:
                return m.group(1)
        except (OSError, KeyError, zipfile.BadZipFile):
            pass
    m = re.search(r"jmeter-(\d+\.\d+(?:\.\d+)?)", home.name, re.I)
    return m.group(1) if m else None


def _version_tuple(v: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


def java_major(version_line: str | None) -> int | None:
    m = re.search(r'version "?(\d+)(?:\.(\d+))?', version_line or "")
    if not m:
        return None
    major = int(m.group(1))
    return int(m.group(2) or 0) if major == 1 else major  # "1.8.0_x" is Java 8


def validate_jmx(text: str) -> list[str]:
    """Structural problems JMeter would reject or that would make an empty run."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return [f"The JMX is not valid XML: {exc}"]
    problems = []
    if root.tag != "jmeterTestPlan":
        problems.append("The root element is not jmeterTestPlan.")
    if root.find(".//TestPlan") is None:
        problems.append("The JMX has no Test Plan element.")
    groups = [e for e in root.iter() if e.tag.endswith("ThreadGroup")]
    if not groups:
        problems.append("The JMX has no thread group.")
    if not any(e.tag.endswith("SamplerProxy") or e.tag.endswith("Sampler") for e in root.iter()):
        problems.append("The JMX has no sampler (request).")
    return problems


def tool_checks(settings: Settings) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Java, JMeter and plugin checks, plus the versions found (recorded in the run snapshot)."""
    checks: list[dict[str, Any]] = []
    info: dict[str, Any] = {"java": None, "java_version": None, "jmeter": None, "jmeter_version": None,
                            "plugins_required": REQUIRED_PLUGINS}
    java = tools.find_java(settings)
    if not java:
        checks.append(_check("java", "Java", "FAIL", "Java was not found.",
                             "Set the Java home under Performance tool settings, or use the offline package (runtime\\jre)."))
    else:
        line = tools.java_version(java)
        major = java_major(line)
        info.update(java=java, java_version=line)
        if major is None:
            checks.append(_check("java", "Java", "WARN", f"Java found at {java}, but its version could not be read."))
        elif major < MIN_JAVA:
            checks.append(_check("java", "Java", "FAIL", f"Java {major} is too old for JMeter 5.6 (needs {MIN_JAVA}+).",
                                 "Point the Java home to Java 17 (bundled as runtime\\jre)."))
        else:
            checks.append(_check("java", "Java", "PASS", f"Java {major} ({java})"))
    jmeter = tools.find_jmeter(settings)
    if not jmeter:
        checks.append(_check("jmeter", "JMeter", "FAIL", "JMeter was not found.",
                             "Set the JMeter home under Performance tool settings, or use the offline package "
                             "(runtime\\apache-jmeter-5.6.3)."))
    else:
        version = jmeter_version(jmeter)
        info.update(jmeter=jmeter, jmeter_version=version)
        if version and _version_tuple(version) < MIN_JMETER:
            checks.append(_check("jmeter", "JMeter", "FAIL", f"JMeter {version} is older than 5.5.",
                                 "Use JMeter 5.6.3 (bundled with the offline package)."))
        else:
            checks.append(_check("jmeter", "JMeter", "PASS" if version else "WARN",
                                 f"JMeter {version or '(version not readable)'} ({jmeter})"))
    checks.append(_check("plugins", "JMeter plugins", "PASS",
                         "None needed: the plan uses built-in JMeter elements only." if not REQUIRED_PLUGINS
                         else ", ".join(REQUIRED_PLUGINS)))
    return checks, info


def disk_check(folder: Path, min_free_mb: int) -> dict[str, Any]:
    probe = folder
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free_mb = shutil.disk_usage(probe).free // (1024 * 1024)
    if free_mb < min_free_mb:
        return _check("disk", "Disk space", "FAIL", f"{free_mb:,} MB free where results are written; at least "
                      f"{min_free_mb:,} MB is required.", "Free disk space or delete old performance runs.")
    return _check("disk", "Disk space", "PASS", f"{free_mb:,} MB free")


def target_checks(plan: PerfPlan, env: PerfEnvironment | None) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    unresolved: list[str] = []
    if plan.script:
        from .script import analyze_cached
        summary = analyze_cached(plan.script)["summary"]
        hosts, unresolved = summary["hosts"], summary["unresolved_urls"]
    else:
        hosts = sorted({(urlsplit(r.url).hostname or "").lower() for r in plan.requests})
    peak = plan.peak_users()
    duration = plan.total_duration_s()
    if unresolved:
        # A host only known at run time (a variable without a default, or a -J property) cannot be checked
        strict = env is not None and bool(env.allowed_hosts)
        shown = ", ".join(sorted(set(unresolved))[:5])
        checks.append(_check("hosts_unresolved", "Request hosts", "FAIL" if strict else "WARN",
                             f"The host of {len(unresolved)} request(s) is not known before the run: {shown}.",
                             "Give the variable a value under the Test Plan's variables or HTTP Request Defaults, "
                             "so the host can be checked."))
    if duration is None:
        limited = env is not None and bool(env.max_duration_s)
        checks.append(_check("duration_unknown", "Duration", "FAIL" if limited else "WARN",
                             "The script's duration depends on loop counts, so it is not known before the run"
                             + (f"; {env.name} allows at most {env.max_duration_s} s." if limited
                                else f". The run is ended after {UNKNOWN_DURATION_TIMEOUT_S // 3600} hours."),
                             "Tick \"Use duration\" on the thread groups and set a duration."))
    if env is None:
        checks.append(_check("environment", "Target environment", "WARN",
                             "No environment is set on this scenario, so no host allowlist applies.",
                             "Choose an environment on the scenario to restrict hosts and load."))
        limit = DEFAULT_MAX_USERS
    else:
        limit = env.max_users
        if env.allowed_hosts:
            outside = [h for h in hosts if h not in env.allowed_hosts]
            if outside:
                checks.append(_check("hosts", "Allowed hosts", "FAIL",
                                     f"Requests go to hosts outside {env.name}: {', '.join(outside)}.",
                                     f"Add the host to {env.name}'s allowed hosts if you are authorized to test it, "
                                     "or remove those requests."))
            else:
                checks.append(_check("hosts", "Allowed hosts", "PASS", f"All requests go to {', '.join(hosts)}."))
        else:
            checks.append(_check("hosts", "Allowed hosts", "WARN",
                                 f"{env.name} has no allowed hosts, so requests may go to any host: {', '.join(hosts)}.",
                                 "List the hosts you are authorized to test in the environment."))
        if env.max_duration_s and duration is not None and duration > env.max_duration_s:
            checks.append(_check("duration", "Duration limit", "FAIL",
                                 f"The test lasts {duration} s; {env.name} allows at most {env.max_duration_s} s.",
                                 "Shorten the test or raise the environment's limit."))
        if env.third_party:
            checks.append(_check("third_party", "Third-party site", "WARN",
                                 f"{env.name} is a public site your team does not own. Keep load minimal.",
                                 "Use one or very few users against sites you do not own."))
    if peak > limit:
        where = env.name if env else "scenarios without an environment"
        checks.append(_check("users", "Load limit", "FAIL",
                             f"The test peaks at {peak} concurrent users; the limit for {where} is {limit}.",
                             "Lower the users, or raise the environment's limit if you are authorized to."))
    else:
        checks.append(_check("users", "Load limit", "PASS", f"Peak {peak} concurrent users (limit {limit})."))
    return checks


def summarize(checks: list[dict[str, Any]]) -> str:
    if any(c["status"] == "FAIL" for c in checks):
        return "FAIL"
    return "WARN" if any(c["status"] == "WARN" for c in checks) else "PASS"
