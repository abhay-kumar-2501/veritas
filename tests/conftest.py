"""Global test config: block all network access unless a test is marked `live` (PRD §11.1)."""

from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class _NetworkBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _block_network(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    if request.node.get_closest_marker("live"):
        yield
        return

    def guard(*_args: object, **_kwargs: object) -> socket.socket:
        raise _NetworkBlocked("Network access is disabled in unit tests (PRD §11.1)")

    monkeypatch.setattr(socket, "socket", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
    yield


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
