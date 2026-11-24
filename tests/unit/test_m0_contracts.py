from __future__ import annotations

import socket
from datetime import date

import pytest

from veritas.cli import build_parser
from veritas.config import t, validate_user_agent
from veritas.models import CheckResult


def test_network_is_blocked() -> None:
    with pytest.raises(RuntimeError):
        socket.create_connection(("www.sec.gov", 443))


def test_cli_help_builds() -> None:
    assert "analyze" in build_parser().format_help()


def test_user_agent_validation() -> None:
    assert validate_user_agent(None) is None
    assert validate_user_agent("  ") is None
    assert validate_user_agent("App/1 Name you@yourdomain.com") is None
    assert validate_user_agent("App/1 Name a@example.com") is None
    assert validate_user_agent("App/1 Name real@company.io") == "App/1 Name real@company.io"


def test_threshold_lookup() -> None:
    assert t("DSO_WARN_DAYS") == 5.0
    with pytest.raises(KeyError):
        t("NOPE")


def test_check_result_serializes_deterministically() -> None:
    c = CheckResult(
        check_id="A1_DSO",
        title="x",
        state="COMPUTED",
        severity="NO_FLAG",
        period_end=date(2025, 1, 26),
        inputs={"b": 1.0, "a": None},
        outputs={},
        formula="f",
        reason=None,
        annotations=(),
        citations=(),
        scored=True,
    )
    assert c.to_json() == c.to_json()
    assert list(c.to_dict()["inputs"].keys()) == ["a", "b"]
