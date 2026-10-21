"""JMX generation for JMeter 5.x using only built-in elements (no plugins)."""

from __future__ import annotations

from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from .models import HttpRequest, PerfPlan, ThreadGroupSpec


def _a(value: object) -> str:
    return escape(str(value), {'"': "&quot;"})


def _prop(kind: str, name: str, value: object) -> str:
    return f'<{kind}Prop name="{_a(name)}">{escape(str(value))}</{kind}Prop>'


def _s(name: str, value: object) -> str:
    return _prop("string", name, value)


def _b(name: str, value: bool) -> str:
    return _prop("bool", name, "true" if value else "false")


def _headers(req: HttpRequest) -> str:
    if not req.headers:
        return ""
    items = "".join(
        f'<elementProp name="{_a(k)}" elementType="Header">{_s("Header.name", k)}{_s("Header.value", v)}</elementProp>'
        for k, v in req.headers.items())
    return (f'<HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="Headers">'
            f'<collectionProp name="HeaderManager.headers">{items}</collectionProp></HeaderManager><hashTree/>')


def _assertions(req: HttpRequest) -> str:
    out = []
    for field, pattern, test_type in (("Assertion.response_code", req.expect_status, 1),
                                      ("Assertion.response_data", req.expect_text, 2)):
        if not pattern:
            continue
        # test_type: 1 = matches (regex), 2 = contains
        out.append(
            f'<ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" testname="Check {_a(field.split("_")[-1])}">'
            f'<collectionProp name="Asserion.test_strings"><stringProp name="0">{escape(pattern)}</stringProp></collectionProp>'
            f'{_s("Assertion.custom_message", "")}{_s("Assertion.test_field", field)}{_b("Assertion.assume_success", field == "Assertion.response_code")}'
            f'<intProp name="Assertion.test_type">{test_type}</intProp></ResponseAssertion><hashTree/>')
    return "".join(out)


def _sampler(req: HttpRequest) -> str:
    u = urlsplit(req.url)
    path = u.path or "/"
    if u.query:
        path += "?" + u.query
    port = u.port or ""
    body = ""
    if req.body is not None and req.method not in ("GET", "HEAD"):
        body = (f'{_b("HTTPSampler.postBodyRaw", True)}<elementProp name="HTTPsampler.Arguments" elementType="Arguments">'
                f'<collectionProp name="Arguments.arguments"><elementProp name="" elementType="HTTPArgument">'
                f'{_b("HTTPArgument.always_encode", False)}{_s("Argument.value", req.body)}{_s("Argument.metadata", "=")}'
                f'</elementProp></collectionProp></elementProp>')
    else:
        body = ('<elementProp name="HTTPsampler.Arguments" elementType="Arguments" guiclass="HTTPArgumentsPanel" '
                'testclass="Arguments" testname="User Defined Variables"><collectionProp name="Arguments.arguments"/></elementProp>')
    return (
        f'<HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="{_a(req.name)}">'
        f'{body}{_s("HTTPSampler.domain", u.hostname or "")}{_s("HTTPSampler.port", port)}'
        f'{_s("HTTPSampler.protocol", u.scheme)}{_s("HTTPSampler.path", path)}{_s("HTTPSampler.method", req.method)}'
        f'{_s("HTTPSampler.contentEncoding", "UTF-8")}{_b("HTTPSampler.follow_redirects", True)}'
        f'{_b("HTTPSampler.use_keepalive", True)}{_s("HTTPSampler.connect_timeout", 10000)}'
        f'{_s("HTTPSampler.response_timeout", 60000)}</HTTPSamplerProxy>'
        f'<hashTree>{_headers(req)}{_assertions(req)}</hashTree>')


def _thread_group(spec: ThreadGroupSpec, samplers: str, think_ms: int) -> str:
    timer = ""
    if think_ms:
        timer = (f'<ConstantTimer guiclass="ConstantTimerGui" testclass="ConstantTimer" testname="Think time">'
                 f'{_s("ConstantTimer.delay", think_ms)}</ConstantTimer><hashTree/>')
    return (
        f'<ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="{_a(spec.name)}">'
        f'{_s("ThreadGroup.on_sample_error", "continue")}'
        f'<elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" testclass="LoopController">'
        f'{_b("LoopController.continue_forever", False)}{_s("LoopController.loops", -1)}</elementProp>'
        f'{_s("ThreadGroup.num_threads", spec.users)}{_s("ThreadGroup.ramp_time", spec.ramp_up_s)}'
        f'{_b("ThreadGroup.scheduler", True)}{_s("ThreadGroup.duration", spec.duration_s)}'
        f'{_s("ThreadGroup.delay", spec.delay_s)}{_b("ThreadGroup.same_user_on_next_iteration", True)}'
        f'</ThreadGroup><hashTree>{timer}{samplers}</hashTree>')


def build_jmx(plan: PerfPlan) -> str:
    samplers = "".join(_sampler(r) for r in plan.requests)
    groups = "".join(_thread_group(g, samplers, plan.profile.think_time_ms) for g in plan.thread_groups())
    cookies = ('<CookieManager guiclass="CookiePanel" testclass="CookieManager" testname="Cookies">'
               '<collectionProp name="CookieManager.cookies"/>' + _b("CookieManager.clearEachIteration", False)
               + '</CookieManager><hashTree/>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3"><hashTree>'
        f'<TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="{_a(plan.name)}">'
        f'{_s("TestPlan.comments", f"{plan.kind} test generated by AI Agentic Test Agent Enterprise ({plan.id})")}'
        f'{_b("TestPlan.functional_mode", False)}{_b("TestPlan.serialize_threadgroups", False)}'
        '<elementProp name="TestPlan.user_defined_variables" elementType="Arguments"><collectionProp name="Arguments.arguments"/></elementProp>'
        f'</TestPlan><hashTree>{cookies}{groups}</hashTree></hashTree></jmeterTestPlan>\n')
