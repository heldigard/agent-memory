"""Cloud-fallback for ``agent-memory maintain`` when local Ollama is down.

Covers the cheap_llm cascade bridge added to ``_best_effort_llm``: local-first,
cloud only when the daemon returned nothing, env-gated, and degrading to None
when cheap_llm is absent (standalone-portability contract).
"""

from __future__ import annotations

import sys
import types

from agent_memory.features.maintain import command as maint


def _fake_cheap_llm(text: str) -> types.ModuleType:
    mod = types.ModuleType("cheap_llm")
    calls: list[dict] = []

    def cheap_complete(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return {"text": text, "model": "fake-cloud", "tier": "T2"}

    mod.cheap_complete = cheap_complete  # type: ignore[attr-defined]
    mod._calls = calls  # type: ignore[attr-defined]
    return mod


def test_best_effort_returns_none_when_no_llm(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MEMORY_CLOUD_FALLBACK", "1")
    assert maint._best_effort_llm("prompt", no_llm=True) is None


def test_best_effort_prefers_ollama_when_available(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MEMORY_CLOUD_FALLBACK", "1")
    monkeypatch.setattr(maint, "ollama_generate", lambda *a, **k: "local-text")
    fake = _fake_cheap_llm("cloud-text")
    monkeypatch.setitem(sys.modules, "cheap_llm", fake)
    assert maint._best_effort_llm("prompt", no_llm=False) == "local-text"
    assert fake._calls == []  # cloud not invoked when local succeeded


def test_best_effort_falls_back_to_cloud_when_ollama_dead(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MEMORY_CLOUD_FALLBACK", "1")
    monkeypatch.setattr(maint, "ollama_generate", lambda *a, **k: None)
    fake = _fake_cheap_llm("cloud-proposal")
    monkeypatch.setitem(sys.modules, "cheap_llm", fake)
    assert maint._best_effort_llm("prompt", no_llm=False) == "cloud-proposal"
    assert len(fake._calls) == 1


def test_cloud_fallback_disabled_by_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MEMORY_CLOUD_FALLBACK", "0")
    monkeypatch.setattr(maint, "ollama_generate", lambda *a, **k: None)
    fake = _fake_cheap_llm("cloud-text")
    monkeypatch.setitem(sys.modules, "cheap_llm", fake)
    assert maint._best_effort_llm("prompt", no_llm=False) is None
    assert fake._calls == []  # env gate stopped the cloud call


def test_cloud_fallback_when_cheap_llm_absent(monkeypatch) -> None:
    """sys.modules[name] = None makes `import name` raise ImportError."""
    monkeypatch.setenv("AGENT_MEMORY_CLOUD_FALLBACK", "1")
    monkeypatch.setattr(maint, "ollama_generate", lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "cheap_llm", None)
    assert maint._best_effort_llm("prompt", no_llm=False) is None
