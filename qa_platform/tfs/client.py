"""Azure DevOps / TFS REST client.

One client serves both modes:
* Azure DevOps Online:  base_url = https://dev.azure.com/{organization}
* Local TFS / Azure DevOps Server: base_url = https://server/tfs/{collection}

Authentication uses a Personal Access Token (Basic auth with an empty user name),
supported by Azure DevOps Online and TFS 2017 or later.
"""

from __future__ import annotations

import base64
import ssl
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import httpx

from ..errors import QAError

WORK_ITEM_BATCH = 200


def tls_context(verify_tls: bool = True, ca_bundle: str | None = None) -> ssl.SSLContext | bool:
    """Final patch: the certificates TFS is checked against.

    Earlier versions trusted only the certifi list shipped with Python. A TFS server inside a company usually has a
    certificate from the company's own certificate authority, which Windows trusts (it is in the Windows certificate
    store) but certifi does not, so every HTTPS call failed with CERTIFICATE_VERIFY_FAILED although Edge opened the
    same server without a warning. The context now trusts the Windows certificate store as well as certifi, and an
    optional CA bundle file (Preferences > TFS Integration). Certificate checking is never switched off here; only the
    existing per-profile "verify TLS" choice for a local TFS server can do that.
    """
    if not verify_tls:
        return False
    ctx = ssl.create_default_context()
    try:  # the Windows certificate store on Windows, the system store elsewhere
        ctx.load_default_certs()
    except (OSError, ssl.SSLError):
        pass
    try:
        import certifi

        ctx.load_verify_locations(cafile=certifi.where())
    except (ImportError, OSError, ssl.SSLError):
        pass
    if ca_bundle:
        path = Path(ca_bundle).expanduser()
        if not path.is_file():
            raise QAError("TFS_CA_BUNDLE_MISSING", f"The CA certificate file {path} does not exist.", status=409)
        try:
            ctx.load_verify_locations(cafile=str(path))
        except (OSError, ssl.SSLError) as exc:
            raise QAError("TFS_CA_BUNDLE_INVALID", f"The CA certificate file {path} could not be read as PEM "
                          "certificates.", status=409, details=str(exc)) from exc
    return ctx


def connect_error(base_url: str, exc: Exception) -> QAError:
    """A readable reason for a failed connection, so a certificate problem is not reported as 'cannot connect'."""
    text = str(exc)
    low = text.lower()
    if "certificate_verify_failed" in low or "certificate verify failed" in low:
        why = ("self-signed" if "self-signed" in low or "self signed" in low else
               "issued by an authority this PC does not trust" if "unable to get local issuer" in low else
               "expired" if "expired" in low else "not trusted")
        return QAError("TFS_TLS_FAILED", f"HTTPS certificate check failed for {base_url}: the server's certificate is "
                       f"{why}. Install the company's root certificate in Windows (or set its CA file under "
                       "Preferences > TFS Integration). The connection was not made.", status=502, details=text)
    if "hostname mismatch" in low or "doesn't match" in low:
        return QAError("TFS_TLS_FAILED", f"The HTTPS certificate of {base_url} is for a different server name. Use "
                       "the server name written in its certificate.", status=502, details=text)
    if "wrong version number" in low or "unknown protocol" in low:
        return QAError("TFS_TLS_FAILED", f"{base_url} does not answer HTTPS on this port. Check http:// versus "
                       "https:// and the port in the server URL.", status=502, details=text)
    return QAError("TFS_UNREACHABLE", f"Cannot connect to {base_url}.", status=502, details=text)


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
        ca_bundle: str | None = None,
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
            verify=tls_context(verify_tls, ca_bundle) if transport is None else verify_tls,
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
            raise connect_error(self.base_url, exc) from exc
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
        if resp.status_code in (301, 307, 308) or (resp.status_code == 302 and
                                                   resp.headers.get("location", "").lower().startswith("https://")
                                                   and resp.request.url.scheme == "http"):
            target = resp.headers.get("location", "")
            raise QAError("TFS_REDIRECTED", f"The TFS server sends this address to {target or 'another address'}. "
                          "Use that address (usually https://) as the server URL in TFS Connection.", status=409,
                          details=target)
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
        if resp.status_code == 400 and "TF401320" in (_server_message(resp) or ""):
            from .fields import rule_error_fields

            message = _server_message(resp) or ""
            found = rule_error_fields(message)
            raise QAError("TFS_FIELD_RULE", "TFS refused the work item: "
                          + "; ".join(f"field '{f['name']}' ({f['codes'] or 'rule'})" for f in found)
                          + ". " + "Choose a valid value for it.", status=422,
                          details={"message": message, "fields": found})
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

    def create_work_item(self, type_name: str, patch: list[dict[str, Any]], *,
                         validate_only: bool = False) -> dict[str, Any]:
        """validate_only: TFS checks the work item against every rule of the project without saving it."""
        return self.request("POST", f"_apis/wit/workitems/${quote(type_name, safe='')}", json=patch,
                            content_type="application/json-patch+json",
                            params={"validateOnly": "true"} if validate_only else None)

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

    def delete_test_case(self, test_case_id: int) -> None:
        """Permanently deletes the test case (and its results). Test cases cannot be deleted with the work item
        API, only with the Test Plans API (Azure DevOps / Server 2020+) or the legacy Test API."""
        try:
            self.request("DELETE", f"_apis/testplan/testcases/{int(test_case_id)}")
        except QAError as exc:
            if exc.error_code != "TFS_NOT_FOUND":
                raise
            self.request("DELETE", f"_apis/test/testcases/{int(test_case_id)}", api_version="5.0-preview.1")

    def delete_work_item(self, item_id: int) -> None:
        """Moves the work item to the project's Recycle Bin (it can be restored there)."""
        self.request("DELETE", f"_apis/wit/workitems/{int(item_id)}", project_scoped=False)

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


    # ---- 23.3.21: test runs and outcomes -----------------------------------------------------

    def test_points_for_case(self, test_case_id: int) -> list[dict[str, Any]]:
        """Every test point (plan / suite / configuration) of a test case."""
        body = {"pointsFilter": {"testcaseIds": [int(test_case_id)]}}
        return self.request("POST", "_apis/test/points", json=body, api_version="5.0").get("points", []) or []

    def create_test_run(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "_apis/test/runs", json=body, api_version="5.0")

    def test_run_results(self, run_id: int) -> list[dict[str, Any]]:
        return self.request("GET", f"_apis/test/runs/{int(run_id)}/results", api_version="5.0").get("value", [])

    def add_test_results(self, run_id: int, results: list[dict[str, Any]]) -> Any:
        return self.request("POST", f"_apis/test/runs/{int(run_id)}/results", json=results, api_version="5.0")

    def update_test_results(self, run_id: int, results: list[dict[str, Any]]) -> Any:
        return self.request("PATCH", f"_apis/test/runs/{int(run_id)}/results", json=results, api_version="5.0")

    def update_test_run(self, run_id: int, changes: dict[str, Any]) -> dict[str, Any]:
        return self.request("PATCH", f"_apis/test/runs/{int(run_id)}", json=changes, api_version="5.0")


def _server_message(resp: httpx.Response) -> str | None:
    try:
        data = resp.json()
        return data.get("message") or data.get("Message")
    except ValueError:
        return None
