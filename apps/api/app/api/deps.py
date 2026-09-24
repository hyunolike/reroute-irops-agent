from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from app.container import Container


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.container.db.session() as s:
        yield s
