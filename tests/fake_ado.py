"""A minimal in-memory Azure DevOps REST double for tests (test fixture only)."""

from __future__ import annotations

import base64
import json
import re

import httpx

PAT = "valid-pat"


class FakeAzureDevOps:
    def __init__(self, base="https://dev.azure.com/acme", project="Shop", bug_type="Bug", severity=False,
                 legacy_test_api=False, hierarchy=False):
        self.base = base
        self.legacy_test_api = legacy_test_api
        self.plans = {7: {"id": 7, "name": "Sprint 4 Plan", "state": "Active", "rootSuite": {"id": 70}}}
        self.suites = {7: [{"id": 70, "name": "Sprint 4 Plan", "suiteType": "staticTestSuite"},
                           {"id": 8, "name": "Checkout", "suiteType": "staticTestSuite", "parentSuite": {"id": 70}}]}
        self.suite_members: dict[int, list[int]] = {}
        self.suite_cases: list[int] = []
        self.project = project
        self.bug_type = bug_type
        self.severity = severity
        self.attachments: dict[str, bytes] = {}
        self.items = {
            118: {"System.Title": "Checkout with saved card", "System.WorkItemType": "User Story",
                  "System.State": "Active", "System.IterationPath": "Shop\\Sprint 4",
                  "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>Pay with saved card</li></ul>"},
            1280: {"System.Title": "Checkout proceeds to payment", "System.WorkItemType": "Test Case",
                   "System.State": "Design", "System.IterationPath": "Shop\\Sprint 4"},
            1420: {"System.Title": "Checkout button unresponsive", "System.WorkItemType": bug_type,
                   "System.State": "Active", "System.IterationPath": "Shop\\Sprint 4"},
        }
        self.relations = {118: [("Microsoft.VSTS.Common.TestedBy-Forward", 1280), ("System.LinkTypes.Related", 1420)]}
        self.next_id = 2000
        self.requests: list[httpx.Request] = []
        self.unreachable = False
        self.hierarchy = hierarchy
        if hierarchy:
            self._add_hierarchy()

    def _add_hierarchy(self):
        """Epic E10 -> Features F100, F101 -> User Stories -> Test Cases (Agile process with Epics and Features)."""
        steps = ('<steps id="0" last="5">'
                 '<step id="2" type="ActionStep"><parameterizedString isformatted="true">&lt;P&gt;Open the application&lt;/P&gt;</parameterizedString><parameterizedString isformatted="true">&lt;P&gt;Login page is displayed&lt;/P&gt;</parameterizedString></step>'
                 '<step id="3" type="ActionStep"><parameterizedString isformatted="true">&lt;P&gt;Enter Username&lt;/P&gt;</parameterizedString><parameterizedString isformatted="true"></parameterizedString></step>'
                 '<step id="4" type="ActionStep"><parameterizedString isformatted="true">&lt;P&gt;Enter Password&lt;/P&gt;</parameterizedString><parameterizedString isformatted="true"></parameterizedString></step>'
                 '<step id="5" type="ActionStep"><parameterizedString isformatted="true">&lt;P&gt;Click Login&lt;/P&gt;</parameterizedString><parameterizedString isformatted="true">&lt;P&gt;Products are displayed&lt;/P&gt;</parameterizedString></step>'
                 '</steps>')
        cart_steps = ('<steps id="0" last="3">'
                      '<step id="2" type="ActionStep"><parameterizedString isformatted="true">&lt;P&gt;Click Add to cart&lt;/P&gt;</parameterizedString><parameterizedString isformatted="true"></parameterizedString></step>'
                      '<step id="3" type="ActionStep"><parameterizedString isformatted="true">&lt;P&gt;Click Cart&lt;/P&gt;</parameterizedString><parameterizedString isformatted="true">&lt;P&gt;Your Cart page lists the product&lt;/P&gt;</parameterizedString></step>'
                      '</steps>')
        it = "Shop\\Sprint 4"
        self.items.update({
            10: {"System.Title": "E-Commerce Platform", "System.WorkItemType": "Epic", "System.State": "Active", "System.IterationPath": "Shop"},
            100: {"System.Title": "Online Purchase", "System.WorkItemType": "Feature", "System.State": "Active", "System.IterationPath": it},
            101: {"System.Title": "Returns", "System.WorkItemType": "Feature", "System.State": "New", "System.IterationPath": "Shop\\Sprint 5"},
            201: {"System.Title": "Customer logs in", "System.WorkItemType": "User Story", "System.State": "Active", "System.IterationPath": it,
                  "System.Description": "<p>The customer signs in on the Login page.</p>",
                  "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>Customer can login successfully</li><li>Customer sees an error for a wrong password</li></ul>"},
            202: {"System.Title": "Customer manages cart", "System.WorkItemType": "User Story", "System.State": "Active", "System.IterationPath": it,
                  "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>Customer can add product to cart</li><li>Customer can apply a discount coupon</li></ul>"},
            203: {"System.Title": "Customer starts a return", "System.WorkItemType": "User Story", "System.State": "New", "System.IterationPath": "Shop\\Sprint 5",
                  "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>Customer can request a return</li></ul>"},
            1268: {"System.Title": "Customer logs in successfully", "System.WorkItemType": "Test Case", "System.State": "Design", "System.IterationPath": it,
                   "Microsoft.VSTS.TCM.Steps": steps},
            1269: {"System.Title": "Customer adds a product to the cart", "System.WorkItemType": "Test Case", "System.State": "Design", "System.IterationPath": it,
                   "Microsoft.VSTS.TCM.Steps": cart_steps},
            1270: {"System.Title": "Return form", "System.WorkItemType": "Test Case", "System.State": "Design", "System.IterationPath": "Shop\\Sprint 5"},
        })
        child, parent = "System.LinkTypes.Hierarchy-Forward", "System.LinkTypes.Hierarchy-Reverse"
        tested_by, tests = "Microsoft.VSTS.Common.TestedBy-Forward", "Microsoft.VSTS.Common.TestedBy-Reverse"
        links = {10: [(child, 100), (child, 101)], 100: [(parent, 10), (child, 201), (child, 202)],
                 101: [(parent, 10), (child, 203)], 201: [(parent, 100), (tested_by, 1268)],
                 202: [(parent, 100), (tested_by, 1269)], 203: [(parent, 101), (tested_by, 1270)],
                 1268: [(tests, 201)], 1269: [(tests, 202)], 1270: [(tests, 203)]}
        for k, v in links.items():
            self.relations.setdefault(k, []).extend(v)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _wi(self, i, relations=False):
        fields = {k: v for k, v in self.items[i].items() if not k.startswith("_")}
        wi = {"id": i, "rev": 1, "fields": {**fields, "System.Id": i}, "url": f"{self.base}/_apis/wit/workItems/{i}"}
        if relations:
            wi["relations"] = [{"rel": r, "url": t if isinstance(t, str) else f"{self.base}/_apis/wit/workItems/{t}"}
                               for r, t in self.relations.get(i, [])]
        return wi

    def _relate(self, i, value):
        url = value["url"]
        target = int(url.rsplit("/", 1)[1]) if "/workItems/" in url else url
        self.relations.setdefault(i, []).append((value["rel"], target))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.unreachable:
            raise httpx.ConnectError("connection refused", request=request)
        auth = request.headers.get("authorization", "")
        expected = "Basic " + base64.b64encode(f":{PAT}".encode()).decode()
        if auth != expected:
            return httpx.Response(203, headers={"content-type": "text/html"}, text="<html>Sign in</html>")
        path = request.url.path
        proj = f"/acme/{self.project}"
        if path == "/acme":
            return httpx.Response(200, json={})
        if path == "/acme/_apis/connectionData":
            return httpx.Response(200, json={"authenticatedUser": {"providerDisplayName": "QA Lead", "descriptor": "Microsoft.IdentityModel.Claims.ClaimsIdentity;x"}})
        if path == "/acme/_apis/projects":
            return httpx.Response(200, json={"value": [{"id": "p1", "name": self.project}]})
        if path == f"/acme/_apis/projects/{self.project}":
            return httpx.Response(200, json={"id": "p1", "name": self.project, "state": "wellFormed"})
        if path.startswith("/acme/_apis/projects/"):
            return httpx.Response(404, json={"message": "project not found"})
        if path == f"{proj}/_apis/wit/workitemtypes":
            types = ["User Story", "Test Case", self.bug_type, "Task"] + (["Epic", "Feature"] if self.hierarchy else [])
            return httpx.Response(200, json={"value": [{"name": t, "states": [
                {"name": "Active", "category": "InProgress"}, {"name": "Closed", "category": "Completed"}]} for t in types]})
        if path == f"{proj}/_apis/wit/workitemtypecategories":
            return httpx.Response(200, json={"value": [
                {"referenceName": "Microsoft.RequirementCategory", "defaultWorkItemType": {"name": "User Story"}, "workItemTypes": [{"name": "User Story"}]},
                {"referenceName": "Microsoft.BugCategory", "defaultWorkItemType": {"name": self.bug_type}, "workItemTypes": [{"name": self.bug_type}]},
                {"referenceName": "Microsoft.TestCaseCategory", "defaultWorkItemType": {"name": "Test Case"}, "workItemTypes": [{"name": "Test Case"}]},
            ] + ([{"referenceName": "Microsoft.EpicCategory", "defaultWorkItemType": {"name": "Epic"}, "workItemTypes": [{"name": "Epic"}]},
                  {"referenceName": "Microsoft.FeatureCategory", "defaultWorkItemType": {"name": "Feature"}, "workItemTypes": [{"name": "Feature"}]}]
                 if self.hierarchy else [])})
        m = re.match(rf"{re.escape(proj)}/_apis/wit/workitemtypes/([^/]+)/fields", path)
        if m:
            refs = ["System.Title", "System.Description", "System.State", "System.AssignedTo", "System.Tags",
                    "System.IterationPath", "System.AreaPath", "Microsoft.VSTS.Common.Priority"]
            if m.group(1) == self.bug_type:
                refs.append("Microsoft.VSTS.TCM.ReproSteps")
                if self.severity:
                    refs.append("Microsoft.VSTS.Common.Severity")
            if m.group(1) == "Test Case":
                refs += ["Microsoft.VSTS.TCM.Steps", "Microsoft.VSTS.TCM.Parameters", "Microsoft.VSTS.TCM.LocalDataSource"]
            allowed = {"Microsoft.VSTS.Common.Severity": ["1 - Critical", "2 - High", "3 - Medium", "4 - Low"]}
            expand = request.url.params.get("$expand") == "allowedValues"
            return httpx.Response(200, json={"value": [
                {"referenceName": r, **({"allowedValues": allowed.get(r, [])} if expand else {})} for r in refs]})
        if path == f"{proj}/_apis/wit/attachments" and request.method == "POST":
            aid = f"att-{len(self.attachments) + 1}"
            self.attachments[request.url.params["fileName"]] = request.content
            return httpx.Response(201, json={"id": aid, "url": f"{self.base}/_apis/wit/attachments/{aid}"})
        if path == f"{proj}/_apis/wit/classificationnodes/iterations":
            return httpx.Response(200, json={"path": f"\\{self.project}\\Iteration", "children": [
                {"path": f"\\{self.project}\\Iteration\\Sprint 4"}] + ([{"path": f"\\{self.project}\\Iteration\\Sprint 5"}]
                                                                   if self.hierarchy else [])})
        if path == f"{proj}/_apis/wit/classificationnodes/areas":
            return httpx.Response(200, json={"path": f"\\{self.project}\\Area"})
        if path == f"{proj}/_apis/wit/wiql":
            query = json.loads(request.content)["query"]
            ids = [i for i, f in self.items.items() if self._match(query, i, f)]
            return httpx.Response(200, json={"workItems": [{"id": i} for i in ids]})
        if path == "/acme/_apis/wit/workitems":
            ids = [int(x) for x in request.url.params["ids"].split(",")]
            expand = request.url.params.get("$expand") == "relations"
            return httpx.Response(200, json={"value": [self._wi(i, relations=expand) for i in ids if i in self.items]})
        m = re.match(r"/acme/_apis/wit/workitems/(\d+)$", path)
        if m:
            i = int(m.group(1))
            if i not in self.items:
                return httpx.Response(404, json={"message": "not found"})
            if request.method == "PATCH":
                for op in json.loads(request.content):
                    if op["path"] == "/relations/-":
                        self._relate(i, op["value"])
                    elif op["path"] == "/fields/System.History":
                        self.items[i].setdefault("_comments", []).append(op["value"])
                    else:
                        self.items[i][op["path"].split("/")[-1]] = op["value"]
            return httpx.Response(200, json=self._wi(i, relations=True))
        m = re.match(rf"{re.escape(proj)}/_apis/wit/workitems/\$(.+)$", path)
        if m and request.method == "POST":
            assert request.headers["content-type"] == "application/json-patch+json"
            i = self.next_id
            self.next_id += 1
            self.items[i] = {"System.WorkItemType": m.group(1), "System.State": "New"}
            for op in json.loads(request.content):
                if op["path"] == "/relations/-":
                    self._relate(i, op["value"])
                    continue
                self.items[i][op["path"].split("/")[-1]] = op["value"]
            return httpx.Response(200, json=self._wi(i))
        test = self._test_management(request, path, proj)
        if test is not None:
            return test
        return httpx.Response(404, json={"message": f"no route {path}"})

    def _new_suite(self, plan_id, name, parent):
        sid = self.next_id
        self.next_id += 1
        suite = {"id": sid, "name": name, "suiteType": "staticTestSuite", **({"parentSuite": {"id": parent}} if parent else {})}
        self.suites.setdefault(plan_id, []).append(suite)
        return suite

    def _test_management(self, request, path, proj):
        """Test Plans API (testplan, 7.x) or, with legacy_test_api, only the old Test API (test, 5.0)."""
        api = "test" if self.legacy_test_api else "testplan"
        if f"{proj}/_apis/{'testplan' if self.legacy_test_api else 'test'}/" in path + "/":
            return httpx.Response(404, json={"message": "API not available on this server"})
        if path.lower() == f"{proj}/_apis/{api}/plans".lower():
            if request.method == "POST":
                body = json.loads(request.content)
                pid = self.next_id
                self.next_id += 1
                root = self._new_suite(pid, body["name"], None)
                self.plans[pid] = {"id": pid, "name": body["name"], "state": "Active", "rootSuite": {"id": root["id"]},
                                   "iteration": body.get("iteration"),
                                   "areaPath": body.get("areaPath") or (body.get("area") or {}).get("name"),
                                   "description": body.get("description")}
                return httpx.Response(200, json=self.plans[pid])
            return httpx.Response(200, json={"value": list(self.plans.values())})
        m = re.match(rf"{re.escape(proj)}/_apis/{api}/plans/(\d+)/suites(?:/(\d+))?$", path, re.I)
        if m:
            pid = int(m.group(1))
            if pid not in self.plans:
                return httpx.Response(404, json={"message": "plan not found"})
            if request.method == "POST":
                body = json.loads(request.content)
                if self.legacy_test_api:
                    suite = self._new_suite(pid, body["name"], int(m.group(2)))
                    legacy = {k: v for k, v in suite.items() if k != "parentSuite"}
                    return httpx.Response(200, json={"value": [{**legacy, "parent": suite["parentSuite"]}]})
                suite = self._new_suite(pid, body["name"], body["parentSuite"]["id"])
                return httpx.Response(200, json=suite)
            suites = self.suites.get(pid, [])
            if self.legacy_test_api:
                suites = [{k: v for k, v in s.items() if k != "parentSuite"} | ({"parent": s["parentSuite"]} if "parentSuite" in s else {})
                          for s in suites]
            return httpx.Response(200, json={"value": suites})
        m = re.match(rf"{re.escape(proj)}/_apis/testplan/plans/(\d+)/suites/(\d+)/testcase$", path, re.I)
        if m and request.method == "POST":
            ids = [w["workItem"]["id"] for w in json.loads(request.content)]
            self.suite_members.setdefault(int(m.group(2)), []).extend(ids)
            self.suite_cases = ids
            return httpx.Response(200, json={"value": []})
        m = re.match(rf"{re.escape(proj)}/_apis/test/plans/(\d+)/suites/(\d+)/testcases/([\d,]+)$", path, re.I)
        if m and request.method == "POST":
            ids = [int(x) for x in m.group(3).split(",")]
            self.suite_members.setdefault(int(m.group(2)), []).extend(ids)
            self.suite_cases = ids
            return httpx.Response(200, json={"value": []})
        return None

    @staticmethod
    def _match(query, i, f):
        m = re.search(r"\[System.WorkItemType\] IN \(([^)]*)\)", query)
        if m and f["System.WorkItemType"] not in re.findall(r"'([^']*)'", m.group(1)):
            return False
        m = re.search(r"\[System.Id\] = (\d+)", query)
        if m and int(m.group(1)) != i:
            return False
        m = re.search(r"\[System.Title\] CONTAINS '([^']*)'", query)
        if m and m.group(1).lower() not in f["System.Title"].lower():
            return False
        m = re.search(r"\[System.IterationPath\] UNDER '([^']*)'", query)
        if m:
            path = f.get("System.IterationPath", "").lower()
            if not (path == m.group(1).lower() or path.startswith(m.group(1).lower() + "\\")):
                return False
        m = re.search(r"\[System.State\] NOT IN \(([^)]*)\)", query)
        if m and f["System.State"] in re.findall(r"'([^']*)'", m.group(1)):
            return False
        return True
