"""Azure DevOps / TFS REST client.

One client serves both modes:
* Azure DevOps Online:  base_url = https://dev.azure.com/{organization}
* Local TFS / Azure DevOps Server: base_url = https://server/tfs/{collection}

Authentication uses a Personal Access Token (Basic auth with an empty user name),
supported by Azure DevOps Online and TFS 2017 or later.
"""

from __future__ import annotations

import base64
from typing import Any, Iterable
from urllib.parse import quote

import httpx

from ..errors import QAError

WORK_ITEM_BATCH = 200


def wiql_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class AzureDevOpsClient:
    def __init__(
        self,
        base_url: str,
        project: str,
        pat: str,
        api_version: str = "7.1",
        *,
        verify_tls: bool = True,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise QAError("TFS_NOT_CONFIGURED", "No TFS / Azure DevOps server URL is configured.")
        self.base_url = base_url.rstrip("/")
        self.project = project
        self.api_version = api_version
        token = base64.b64encode(f":{pat}".encode()).decode()
        self._http = httpx.Client(
            headers={"Authorization": f"Basic {token}", "Accept": "application/json"},
            timeout=timeout,
            verify=verify_tls,
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    # ---- low level ----------------------------------------------------------

    def _url(self, path: str, project_scoped: bool) -> str:
        prefix = f"{self.base_url}/{quote(self.project, safe='')}" if project_scoped else self.base_url
        return f"{prefix}/{path.lstrip('/')}"

    def request(
        self,
        method: str,
        path: str,
        *,
        project_scoped: bool = True,
        params: dict[str, Any] | None = None,
        json: Any = None,
        content_type: str | None = None,
        api_version: str | None = None,
        content: bytes | None = None,
    ) -> Any:
        query = {"api-version": api_version or self.api_version, **(params or {})}
        headers = {"Content-Type": content_type} if content_type else None
        try:
            resp = self._http.request(method, self._url(path, project_scoped), params=query, json=json,
                                      content=content, headers=headers)
        except httpx.ConnectError as exc:
            raise QAError("TFS_UNREACHABLE", f"Cannot connect to {self.base_url}.", status=502,
                          details=str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise QAError("TFS_TIMEOUT", f"{self.base_url} did not respond in time.", status=504) from exc
        except httpx.HTTPError as exc:
            raise QAError("TFS_HTTP_ERROR", f"HTTP error talking to {self.base_url}.", status=502,
                          details=str(exc)) from exc
        return self._parse(resp)

    @staticmethod
    def _parse(resp: httpx.Response) -> Any:
        ctype = resp.headers.get("content-type", "")
        # Azure DevOps answers an invalid PAT with 203 + an HTML sign-in page, or a 302 to sign-in.
        if resp.status_code in (203, 302) or (resp.is_success and "json" not in ctype and resp.content):
            raise QAError("TFS_AUTH_FAILED", "Authentication failed: the server returned a sign-in page. "
                          "Check that the PAT is valid and not expired.", status=401)
        if resp.status_code == 401:
            raise QAError("TFS_AUTH_FAILED", "Authentication failed: the PAT was rejected.", status=401)
        if resp.status_code == 403:
            raise QAError("TFS_FORBIDDEN", "Access denied: the PAT lacks the required scope or permission.",
                          status=403, details=_server_message(resp))
        if resp.status_code == 404:
            raise QAError("TFS_NOT_FOUND", "Not found on the TFS server.", status=404,
                          details=_server_message(resp))
        if resp.status_code == 429:
            raise QAError("TFS_RATE_LIMITED", "The TFS server is throttling requests.", status=429,
                          details=resp.headers.get("retry-after"))
        if not resp.is_success:
            raise QAError("TFS_REQUEST_FAILED", f"TFS request failed with HTTP {resp.status_code}.",
                          status=502 if resp.status_code >= 500 else 400, details=_server_message(resp))
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise QAError("TFS_INVALID_RESPONSE", "The TFS server returned a non-JSON response.",
                          status=502) from exc

    # ---- organization / collection -----------------------------------------

    def connection_data(self) -> dict[str, Any]:
        return self.request("GET", "_apis/connectionData", project_scoped=False, api_version="5.0-preview")

    def list_projects(self) -> list[dict[str, Any]]:
        return self.request("GET", "_apis/projects", project_scoped=False, params={"$top": 500}).get("value", [])

    def get_project(self) -> dict[str, Any]:
        return self.request("GET", f"_apis/projects/{quote(self.project, safe='')}", project_scoped=False)

    # ---- work item tracking -------------------------------------------------

    def work_item_types(self) -> list[dict[str, Any]]:
        return self.request("GET", "_apis/wit/workitemtypes").get("value", [])

    def work_item_type_categories(self) -> list[dict[str, Any]]:
        return self.request("GET", "_apis/wit/workitemtypecategories").get("value", [])

    def work_item_type_fields(self, type_name: str, allowed_values: bool = False) -> list[dict[str, Any]]:
        params = {"$expand": "allowedValues"} if allowed_values else None
        return self.request("GET", f"_apis/wit/workitemtypes/{quote(type_name, safe='')}/fields",
                            params=params).get("value", [])

    def iterations(self, depth: int = 10) -> dict[str, Any]:
        return self.request("GET", "_apis/wit/classificationnodes/iterations", params={"$depth": depth})

    def areas(self, depth: int = 10) -> dict[str, Any]:
        return self.request("GET", "_apis/wit/classificationnodes/areas", params={"$depth": depth})

    def wiql(self, query: str, top: int = 200) -> list[int]:
        data = self.request("POST", "_apis/wit/wiql", params={"$top": top}, json={"query": query})
        return [w["id"] for w in data.get("workItems", [])]

    def work_items(self, ids: Iterable[int], fields: list[str] | None = None,
                   expand_relations: bool = False) -> list[dict[str, Any]]:
        ids = list(ids)
        out: list[dict[str, Any]] = []
        for start in range(0, len(ids), WORK_ITEM_BATCH):
            chunk = ids[start:start + WORK_ITEM_BATCH]
            params: dict[str, Any] = {"ids": ",".join(map(str, chunk)), "errorPolicy": "omit"}
            if expand_relations:
                params["$expand"] = "relations"
            elif fields:
                params["fields"] = ",".join(fields)
            data = self.request("GET", "_apis/wit/workitems", project_scoped=False, params=params)
            out.extend(w for w in data.get("value", []) if w)
        order = {i: n for n, i in enumerate(ids)}
        out.sort(key=lambda w: order.get(w["id"], 0))
        return out

    def work_item(self, item_id: int) -> dict[str, Any]:
        return self.request("GET", f"_apis/wit/workitems/{int(item_id)}", project_scoped=False,
                            params={"$expand": "relations"})

    def create_work_item(self, type_name: str, patch: list[dict[str, Any]]) -> dict[str, Any]:
        return self.request("POST", f"_apis/wit/workitems/${quote(type_name, safe='')}", json=patch,
                            content_type="application/json-patch+json")

    def update_work_item(self, item_id: int, patch: list[dict[str, Any]]) -> dict[str, Any]:
        return self.request("PATCH", f"_apis/wit/workitems/{int(item_id)}", project_scoped=False, json=patch,
                            content_type="application/json-patch+json")

    def upload_attachment(self, file_name: str, data: bytes) -> dict[str, Any]:
        """Returns {id, url}; link it to a work item with an AttachedFile relation."""
        return self.request("POST", "_apis/wit/attachments", params={"fileName": file_name}, content=data,
                            content_type="application/octet-stream")

    def add_comment(self, item_id: int, text: str) -> dict[str, Any]:
        return self.update_work_item(item_id, [{"op": "add", "path": "/fields/System.History", "value": text}])

    # ---- test management ------------------------------------------------------

    def test_plans(self) -> list[dict[str, Any]]:
        try:
            return self.request("GET", "_apis/testplan/plans").get("value", [])
        except QAError as exc:
            if exc.error_code != "TFS_NOT_FOUND":
                raise
            # Older servers only expose the legacy Test API.
            return self.request("GET", "_apis/test/plans", api_version="5.0").get("value", [])

    def test_suites(self, plan_id: int) -> list[dict[str, Any]]:
        try:
            return self.request("GET", f"_apis/testplan/Plans/{int(plan_id)}/suites").get("value", [])
        except QAError as exc:
            if exc.error_code != "TFS_NOT_FOUND":
                raise
            return self.request("GET", f"_apis/test/plans/{int(plan_id)}/suites", api_version="5.0").get("value", [])

    def create_test_plan(self, name: str, *, area_path: str | None = None, iteration: str | None = None,
                         description: str | None = None) -> dict[str, Any]:
        """Returns the plan including ``rootSuite`` (the suite every child suite hangs under)."""
        body = {k: v for k, v in {"name": name, "areaPath": area_path, "iteration": iteration,
                                  "description": description}.items() if v}
        try:
            return self.request("POST", "_apis/testplan/plans", json=body)
        except QAError as exc:
            if exc.error_code != "TFS_NOT_FOUND":
                raise
            legacy = {"name": name, **({"area": {"name": area_path}} if area_path else {}),
                      **({"iteration": iteration} if iteration else {}),
                      **({"description": description} if description else {})}
            return self.request("POST", "_apis/test/plans", json=legacy, api_version="5.0")

    def create_test_suite(self, plan_id: int, parent_suite_id: int, name: str) -> dict[str, Any]:
        try:
            return self.request("POST", f"_apis/testplan/Plans/{int(plan_id)}/suites", json={
                "suiteType": "staticTestSuite", "name": name, "parentSuite": {"id": int(parent_suite_id)}})
        except QAError as exc:
            if exc.error_code != "TFS_NOT_FOUND":
                raise
            created = self.request("POST", f"_apis/test/plans/{int(plan_id)}/suites/{int(parent_suite_id)}",
                                   json={"suiteType": "StaticTestSuite", "name": name}, api_version="5.0")
            # The legacy API answers with a list of the created suites.
            return (created.get("value") or [created])[0] if isinstance(created, dict) else created[0]

    def update_test_plan(self, plan_id: int, changes: dict[str, Any]) -> dict[str, Any]:
        """changes: name, iteration, areaPath, state, description (only the keys given are changed)."""
        try:
            return self.request("PATCH", f"_apis/testplan/plans/{int(plan_id)}", json=changes)
        except QAError as exc:
            if exc.error_code != "TFS_NOT_FOUND":
                raise
            legacy = {k: v for k, v in changes.items() if k != "areaPath"}
            if changes.get("areaPath"):
                legacy["area"] = {"name": changes["areaPath"]}
            return self.request("PATCH", f"_apis/test/plans/{int(plan_id)}", json=legacy, api_version="5.0")

    def delete_test_plan(self, plan_id: int) -> None:
        try:
            self.request("DELETE", f"_apis/testplan/plans/{int(plan_id)}")
        except QAError as exc:
            if exc.error_code != "TFS_NOT_FOUND":
                raise
            self.request("DELETE", f"_apis/test/plans/{int(plan_id)}", api_version="5.0")

    def update_test_suite(self, plan_id: int, suite_id: int, name: str) -> dict[str, Any]:
        try:
            return self.request("PATCH", f"_apis/testplan/Plans/{int(plan_id)}/suites/{int(suite_id)}",
                                json={"name": name})
        except QAError as exc:
            if exc.error_code != "TFS_NOT_FOUND":
                raise
            return self.request("PATCH", f"_apis/test/plans/{int(plan_id)}/suites/{int(suite_id)}",
                                json={"name": name}, api_version="5.0")

    def delete_test_suite(self, plan_id: int, suite_id: int) -> None:
        try:
            self.request("DELETE", f"_apis/testplan/Plans/{int(plan_id)}/suites/{int(suite_id)}")
        except QAError as exc:
            if exc.error_code != "TFS_NOT_FOUND":
                raise
            self.request("DELETE", f"_apis/test/plans/{int(plan_id)}/suites/{int(suite_id)}", api_version="5.0")

    def suite_test_cases(self, plan_id: int, suite_id: int) -> list[dict[str, Any]]:
        try:
            return self.request("GET", f"_apis/testplan/Plans/{int(plan_id)}/Suites/{int(suite_id)}/TestCase").get("value", [])
        except QAError as exc:
            if exc.error_code != "TFS_NOT_FOUND":
                raise
            return self.request("GET", f"_apis/test/plans/{int(plan_id)}/suites/{int(suite_id)}/testcases",
                                api_version="5.0").get("value", [])


    def add_test_cases_to_suite(self, plan_id: int, suite_id: int, ids: list[int]) -> Any:
        try:
            return self.request("POST", f"_apis/testplan/Plans/{int(plan_id)}/Suites/{int(suite_id)}/TestCase",
                                json=[{"workItem": {"id": int(i)}} for i in ids])
        except QAError as exc:
            if exc.error_code != "TFS_NOT_FOUND":
                raise
            joined = ",".join(str(int(i)) for i in ids)
            return self.request("POST", f"_apis/test/Plans/{int(plan_id)}/suites/{int(suite_id)}/testcases/{joined}",
                                api_version="5.0")


def _server_message(resp: httpx.Response) -> str | None:
    try:
        data = resp.json()
        return data.get("message") or data.get("Message")
    except ValueError:
        return None
