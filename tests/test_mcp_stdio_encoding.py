"""The MCP server speaks UTF-8 whatever the console code page is.

On a Chinese Windows the agent starts the server with stdin and stdout in GBK
(cp936): Chinese the server writes itself (a task title from the backend)
went out as GBK bytes, and an emoji raised inside the call."""

import io
import sys

from scripts import fluentflow_mcp_server as server


def _gbk_stream(data: bytes = b"") -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(data), encoding="cp936")


def test_stdio_is_switched_to_utf8(monkeypatch):
    incoming = '{"text":"把这一句换掉😀"}\n'.encode("utf-8")
    stdin, stdout, stderr = _gbk_stream(incoming), _gbk_stream(), _gbk_stream()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    server._use_utf8_stdio()

    assert stdin.readline() == '{"text":"把这一句换掉😀"}\n'
    server._respond({"jsonrpc": "2.0", "id": 1, "result": {"title": "第三课 访谈😀"}})
    written = stdout.buffer.getvalue()
    assert "第三课 访谈😀".encode("utf-8") in written
    assert written.endswith(b"}\n")
