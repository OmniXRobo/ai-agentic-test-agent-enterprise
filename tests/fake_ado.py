"""A minimal in-memory Azure DevOps REST double for tests (test fixture only)."""

from __future__ import annotations

import base64
import json
import re

import httpx

PAT = "valid-pat"


class FakeAzureDevOps:
    def __init__(self, base="https://dev.azure.com/acme", project="Shop", bug_type="Bug"):
        self.base = base
        self.project = project
        self.bug_type = bug_type
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

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _wi(self, i):
        return {"id": i, "rev": 1, "fields": {**self.items[i], "System.Id": i},
                "url": f"{self.base}/_apis/wit/workItems/{i}"}

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
            types = ["User Story", "Test Case", self.bug_type, "Task"]
            return httpx.Response(200, json={"value": [{"name": t, "states": [
                {"name": "Active", "category": "InProgress"}, {"name": "Closed", "category": "Completed"}]} for t in types]})
        if path == f"{proj}/_apis/wit/workitemtypecategories":
            return httpx.Response(200, json={"value": [
                {"referenceName": "Microsoft.RequirementCategory", "defaultWorkItemType": {"name": "User Story"}, "workItemTypes": [{"name": "User Story"}]},
                {"referenceName": "Microsoft.BugCategory", "defaultWorkItemType": {"name": self.bug_type}, "workItemTypes": [{"name": self.bug_type}]},
                {"referenceName": "Microsoft.TestCaseCategory", "defaultWorkItemType": {"name": "Test Case"}, "workItemTypes": [{"name": "Test Case"}]},
            ]})
        m = re.match(rf"{re.escape(proj)}/_apis/wit/workitemtypes/([^/]+)/fields", path)
        if m:
            refs = ["System.Title", "System.Description", "System.State", "System.AssignedTo", "System.Tags",
                    "System.IterationPath", "System.AreaPath", "Microsoft.VSTS.Common.Priority"]
            if m.group(1) == self.bug_type:
                refs.append("Microsoft.VSTS.TCM.ReproSteps")
            if m.group(1) == "Test Case":
                refs.append("Microsoft.VSTS.TCM.Steps")
            return httpx.Response(200, json={"value": [{"referenceName": r} for r in refs]})
        if path == f"{proj}/_apis/wit/classificationnodes/iterations":
            return httpx.Response(200, json={"path": f"\\{self.project}\\Iteration", "children": [
                {"path": f"\\{self.project}\\Iteration\\Sprint 4"}]})
        if path == f"{proj}/_apis/wit/classificationnodes/areas":
            return httpx.Response(200, json={"path": f"\\{self.project}\\Area"})
        if path == f"{proj}/_apis/wit/wiql":
            query = json.loads(request.content)["query"]
            ids = [i for i, f in self.items.items() if self._match(query, i, f)]
            return httpx.Response(200, json={"workItems": [{"id": i} for i in ids]})
        if path == "/acme/_apis/wit/workitems":
            ids = [int(x) for x in request.url.params["ids"].split(",")]
            return httpx.Response(200, json={"value": [self._wi(i) for i in ids if i in self.items]})
        m = re.match(r"/acme/_apis/wit/workitems/(\d+)$", path)
        if m:
            i = int(m.group(1))
            if i not in self.items:
                return httpx.Response(404, json={"message": "not found"})
            if request.method == "PATCH":
                for op in json.loads(request.content):
                    self.items[i][op["path"].split("/")[-1]] = op["value"]
            wi = self._wi(i)
            wi["relations"] = [{"rel": r, "url": f"{self.base}/_apis/wit/workItems/{t}"} for r, t in self.relations.get(i, [])]
            return httpx.Response(200, json=wi)
        m = re.match(rf"{re.escape(proj)}/_apis/wit/workitems/\$(.+)$", path)
        if m and request.method == "POST":
            assert request.headers["content-type"] == "application/json-patch+json"
            i = self.next_id
            self.next_id += 1
            self.items[i] = {"System.WorkItemType": m.group(1), "System.State": "New"}
            for op in json.loads(request.content):
                if op["path"] == "/relations/-":
                    target = int(op["value"]["url"].rsplit("/", 1)[1])
                    self.relations.setdefault(i, []).append((op["value"]["rel"], target))
                    continue
                self.items[i][op["path"].split("/")[-1]] = op["value"]
            return httpx.Response(200, json=self._wi(i))
        if path == f"{proj}/_apis/testplan/plans":
            return httpx.Response(200, json={"value": [{"id": 7, "name": "Sprint 4 Plan", "state": "Active"}]})
        if path == f"{proj}/_apis/testplan/Plans/7/suites":
            return httpx.Response(200, json={"value": [{"id": 8, "name": "Checkout", "suiteType": "staticTestSuite"}]})
        if path == f"{proj}/_apis/testplan/Plans/7/Suites/8/TestCase" and request.method == "POST":
            self.suite_cases = [w["workItem"]["id"] for w in json.loads(request.content)]
            return httpx.Response(200, json={"value": []})
        return httpx.Response(404, json={"message": f"no route {path}"})

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
        m = re.search(r"\[System.State\] NOT IN \(([^)]*)\)", query)
        if m and f["System.State"] in re.findall(r"'([^']*)'", m.group(1)):
            return False
        return True
