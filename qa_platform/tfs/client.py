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
    ) -> Any:
        query = {"api-version": api_version or self.api_version, **(params or {})}
        headers = {"Content-Type": content_type} if content_type else None
        try:
            resp = self._http.request(method, self._url(path, project_scoped), params=query, json=json,
                                      headers=headers)
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

    def work_item_type_fields(self, type_name: str) -> list[dict[str, Any]]:
        return self.request("GET", f"_apis/wit/workitemtypes/{quote(type_name, safe='')}/fields").get("value", [])

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
