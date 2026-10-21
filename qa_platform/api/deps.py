from __future__ import annotations

from fastapi import Request

from ..runtime import Runtime


def runtime(request: Request) -> Runtime:
    return request.app.state.runtime
