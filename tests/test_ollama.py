"""Ollama HTTP client — covers the network + cache paths via monkeypatched ``urlopen``.

No real daemon is contacted. We assert: ``is_alive`` True/False, ``generate``
caches on a second identical call (no second HTTP), ``generate`` returns None
when the daemon is unreachable, ``embed`` halves + retries on failure, and the
``<think>`` stripping is applied to raw model output.
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import agent_memory.shared.ollama as ollama


class _FakeResp:
    """Minimal context-manager stand-in for ``urlopen``'s return value."""

    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


def _patch_post(monkeypatch, handler) -> None:
    """Redirect ``urlopen`` inside the ollama module to ``handler(req, timeout)``."""
    monkeypatch.setattr(ollama.urllib.request, "urlopen", handler)


def test_is_alive_true(monkeypatch) -> None:
    _patch_post(monkeypatch, lambda req, timeout=None: _FakeResp({}, status=200))
    assert ollama.is_alive(timeout=1.0) is True


def test_is_alive_false_on_url_error(monkeypatch) -> None:
    def _raise(req, timeout=None):
        raise urllib.error.URLError("no daemon")

    _patch_post(monkeypatch, _raise)
    assert ollama.is_alive(timeout=1.0) is False


def test_is_alive_false_on_invalid_scheme(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MEMORY_OLLAMA_URL", "file:///etc/passwd")

    def _unexpected(req, timeout=None):
        raise AssertionError("urlopen should not run for non-http Ollama URLs")

    _patch_post(monkeypatch, _unexpected)
    assert ollama.is_alive(timeout=1.0) is False


def test_embed_ready_true_on_vector(monkeypatch) -> None:
    _patch_post(
        monkeypatch,
        lambda req, timeout=None: _FakeResp({"embedding": [0.1, 0.2]}),
    )
    assert ollama.embed_ready(timeout=1.0) is True


def test_embed_ready_false_on_empty_or_error(monkeypatch) -> None:
    _patch_post(monkeypatch, lambda req, timeout=None: _FakeResp({"embedding": []}))
    assert ollama.embed_ready(timeout=1.0) is False

    def _raise(req, timeout=None):
        raise urllib.error.URLError("no daemon")

    _patch_post(monkeypatch, _raise)
    assert ollama.embed_ready(timeout=1.0) is False


def test_generate_returns_response_and_caches(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ollama, "OLLAMA_CACHE_DIR", tmp_path)
    calls = {"n": 0}

    def handler(req, timeout=None):
        calls["n"] += 1
        return _FakeResp({"response": "hello world"})

    _patch_post(monkeypatch, handler)
    # temperature <= CACHE_MAX_TEMP (0.3) → cacheable
    out1 = ollama.generate("prompt", model="m", temperature=0.0)
    out2 = ollama.generate("prompt", model="m", temperature=0.0)
    assert out1 == "hello world"
    assert out2 == "hello world"
    assert calls["n"] == 1, "second identical call must hit the cache, not HTTP"


def test_generate_returns_none_when_daemon_unreachable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ollama, "OLLAMA_CACHE_DIR", tmp_path)

    def _raise(req, timeout=None):
        raise ollama.OllamaUnavailable("down")

    _patch_post(monkeypatch, _raise)
    assert ollama.generate("prompt", model="m") is None


def test_generate_strips_think_tags(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ollama, "OLLAMA_CACHE_DIR", tmp_path)
    _patch_post(
        monkeypatch,
        lambda req, timeout=None: _FakeResp({"response": "<think>internal</think>real answer"}),
    )
    assert ollama.generate("p", model="m", temperature=0.0) == "real answer"


def test_strip_think_handles_visible_reasoning_wrappers() -> None:
    cases = [
        ("<reflection>internal</reflection>real answer", "real answer"),
        ("<output>real answer</output>", "real answer"),
        ("Thinking process: inspect memory\nFinal answer: real answer", "real answer"),
        ("<|channel|>thought<|channel|>real answer", "real answer"),
        ("<reasoning>unterminated", ""),
    ]
    for raw, expected in cases:
        assert ollama._strip_think(raw) == expected


def test_embed_retries_on_failure_then_halves(monkeypatch) -> None:
    """A long text whose full embed fails yields a half-text embed retry."""
    calls: list[str] = []

    def handler(req, timeout=None):
        payload = json.loads(req.data.decode("utf-8"))
        text = payload["prompt"]
        calls.append(text)
        if len(text) > 512 and len(text) == len(long_text):
            raise ollama.OllamaUnavailable("too long")
        return _FakeResp({"embedding": [0.1, 0.2, 0.3]})

    long_text = "x" * 600
    _patch_post(monkeypatch, handler)
    vec = ollama.embed(long_text)
    assert vec == [0.1, 0.2, 0.3]
    assert len(calls) == 2
    assert calls[1] == long_text[: len(long_text) // 2]  # retry used the first half


def test_embed_returns_none_when_both_attempts_fail(monkeypatch) -> None:
    def _raise(req, timeout=None):
        raise ollama.OllamaUnavailable("down")

    _patch_post(monkeypatch, _raise)
    assert ollama.embed("x" * 600) is None


def test_request_error_carries_status(monkeypatch) -> None:
    def _http_err(req, timeout=None):
        raise urllib.error.HTTPError("u", 500, "err", {}, None)  # type: ignore[arg-type]

    _patch_post(monkeypatch, _http_err)
    try:
        ollama._post("/api/generate", {}, 1.0)
    except ollama.OllamaRequestError as exc:
        assert exc.status == 500
        return
    raise AssertionError("expected OllamaRequestError on HTTP 500")


def test_post_rejects_invalid_scheme(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MEMORY_OLLAMA_URL", "file:///etc/passwd")

    def _unexpected(req, timeout=None):
        raise AssertionError("urlopen should not run for non-http Ollama URLs")

    _patch_post(monkeypatch, _unexpected)
    try:
        ollama._post("/api/generate", {}, 1.0)
    except ollama.OllamaUnavailable:
        return
    raise AssertionError("expected OllamaUnavailable for invalid scheme")


def test_embed_ready_retries_with_longer_timeout_after_fast_failure(monkeypatch) -> None:
    """Cold embed models can exceed the fast probe timeout; embed_ready must
    retry once with a longer budget before declaring inference broken."""
    calls: list[float] = []

    def _flaky(path, payload, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise ollama.OllamaRequestError(408, "timeout: cold load")
        return {"embedding": [0.1, 0.2]}

    monkeypatch.setattr(ollama, "_post", _flaky)
    monkeypatch.delenv("AGENT_MEMORY_EMBED_READY_TIMEOUT", raising=False)
    assert ollama.embed_ready(timeout=1.0) is True
    assert len(calls) == 2
    assert calls[1] > calls[0]


def test_embed_ready_false_when_both_probes_fail(monkeypatch) -> None:
    def _always_fail(path, payload, timeout):
        raise ollama.OllamaUnavailable("no daemon")

    monkeypatch.setattr(ollama, "_post", _always_fail)
    assert ollama.embed_ready(timeout=1.0) is False
