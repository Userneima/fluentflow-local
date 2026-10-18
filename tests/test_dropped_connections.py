"""A connection the network drops halfway is tried again, and if it keeps
dropping the user reads what happened, not the raw SSL error.

Found 2026-10-18 on a Windows machine behind a proxy: a Douyin link failed with
"<urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING] ...>" shown on the page as
it was; the same link resolved three times out of three right after.
"""

from __future__ import annotations

import ssl
import urllib.error

import pytest

import backend.core.video_source as vs
from backend.core.local_error_diagnostics import diagnose_error

DROPPED = urllib.error.URLError(ssl.SSLEOFError(8, "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol"))


class _Opener:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def open(self, request, timeout):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def opener(monkeypatch):
    holder = {}

    def build(*_handlers):
        return holder["opener"]

    monkeypatch.setattr(vs.urllib.request, "build_opener", build)
    monkeypatch.setattr(vs, "validate_public_http_url", lambda *a, **k: None)
    monkeypatch.setattr(vs, "_CONNECTION_RETRY_SECONDS", 0)
    return holder


def test_a_dropped_connection_is_tried_again(opener):
    opener["opener"] = _Opener([DROPPED, "response"])
    assert vs.open_public_http_url("https://sph.miuistore.com/x", timeout=5) == "response"
    assert opener["opener"].calls == 2


def test_it_gives_up_after_three_tries(opener):
    opener["opener"] = _Opener([DROPPED, DROPPED, DROPPED])
    with pytest.raises(urllib.error.URLError):
        vs.open_public_http_url("https://sph.miuistore.com/x", timeout=5)
    assert opener["opener"].calls == 3


def test_an_answer_from_the_server_is_not_asked_again(opener):
    refused = urllib.error.HTTPError("https://sph.miuistore.com/x", 500, "Internal Server Error", {}, None)
    opener["opener"] = _Opener([refused])
    with pytest.raises(urllib.error.HTTPError):
        vs.open_public_http_url("https://sph.miuistore.com/x", timeout=5)
    assert opener["opener"].calls == 1


def test_the_user_reads_what_happened_not_the_ssl_error():
    diagnosis = diagnose_error(f"<urlopen error {DROPPED.reason}>")
    assert diagnosis["code"] == "network_connection_dropped"
    assert "SSL" not in diagnosis["detail"] and "代理" in diagnosis["next_action"]
