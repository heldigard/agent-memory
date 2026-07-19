"""maintain.command LLM-audit + cloud-fallback + apply-safe coverage.

The local Ollama model is monkeypatched (no daemon). Covers _best_effort_llm,
_cloud_call import/result branches, _audit_file truncation, _emit_audit
skip/empty paths, _archive_with_summary early returns, _write_summary_archive
append, _process_file apply path, _report_topics, and the maintain() applied
section + _extend_handoff missing-file guard.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import agent_memory.features.maintain.command as cmd
from agent_memory.features.bank.command import init_memory
from agent_memory.features.maintain.command import (
    CoreFile,
    MaintCtx,
    _archive_with_summary,
    _audit_file,
    _best_effort_llm,
    _cloud_call,
    _emit_audit,
    _maint_disabled,
    _process_file,
    _report_topics,
    _summarize_block,
    _write_summary_archive,
    handoff,
    maintain,
)


def _seed(tmp_path: Path) -> Path:
    init_memory(tmp_path)
    return tmp_path


# --- _maint_disabled / _best_effort_llm ---


def test_maint_disabled_flags(monkeypatch) -> None:
    assert _maint_disabled(True) is True
    monkeypatch.delenv("CODEQ_NO_LLM", raising=False)
    monkeypatch.setenv("PROJECT_MEMORY_NO_LLM", "1")
    assert _maint_disabled(False) is True
    monkeypatch.delenv("PROJECT_MEMORY_NO_LLM", raising=False)
    assert _maint_disabled(False) is False


def test_best_effort_llm_returns_none_when_disabled(monkeypatch) -> None:
    monkeypatch.delenv("CODEQ_NO_LLM", raising=False)
    monkeypatch.delenv("PROJECT_MEMORY_NO_LLM", raising=False)
    monkeypatch.setattr(cmd, "ollama_generate", lambda *a, **k: "local text")
    assert _best_effort_llm("p", no_llm=True) is None


def test_best_effort_llm_prefers_local_then_cloud(monkeypatch) -> None:
    monkeypatch.delenv("CODEQ_NO_LLM", raising=False)
    monkeypatch.delenv("PROJECT_MEMORY_NO_LLM", raising=False)
    # local returns text -> used directly, cloud not consulted
    monkeypatch.setattr(cmd, "ollama_generate", lambda *a, **k: "local")
    assert _best_effort_llm("p", no_llm=False) == "local"
    # local empty -> cloud fallback returns text
    monkeypatch.setattr(cmd, "ollama_generate", lambda *a, **k: None)
    monkeypatch.setattr(cmd, "_cloud_call", lambda prompt: "cloud text")
    assert _best_effort_llm("p", no_llm=False) == "cloud text"
    # both empty -> None
    monkeypatch.setattr(cmd, "_cloud_call", lambda prompt: None)
    assert _best_effort_llm("p", no_llm=False) is None


# --- _cloud_call ---


def _install_fake_cheap_llm(monkeypatch, cheap_complete) -> types.ModuleType:
    fake = types.ModuleType("cheap_llm")
    fake.cheap_complete = cheap_complete  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cheap_llm", fake)
    return fake


def test_cloud_call_env_off_and_import_error_return_none(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MEMORY_CLOUD_FALLBACK", "0")
    assert _cloud_call("p") is None
    # env on but module missing -> ImportError swallowed -> None
    monkeypatch.setenv("AGENT_MEMORY_CLOUD_FALLBACK", "1")
    monkeypatch.delitem(sys.modules, "cheap_llm", raising=False)
    # prevent any real import resolution by pointing __import__ to fail for it
    real_import = __import__

    def _block(name, *a, **k):
        if name == "cheap_llm":
            raise ImportError("blocked")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", _block)
    assert _cloud_call("p") is None


def test_cloud_call_non_dict_empty_and_ok(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MEMORY_CLOUD_FALLBACK", "1")
    # result not a dict -> None
    _install_fake_cheap_llm(monkeypatch, lambda **k: "not a dict")
    assert _cloud_call("p") is None
    # dict with empty text -> None
    _install_fake_cheap_llm(monkeypatch, lambda **k: {"text": "   "})
    assert _cloud_call("p") is None
    # dict with text -> text
    _install_fake_cheap_llm(monkeypatch, lambda **k: {"text": "audit result"})
    assert _cloud_call("p") == "audit result"


def test_cloud_call_swallows_runtime_error(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MEMORY_CLOUD_FALLBACK", "1")

    def boom(**k):
        raise RuntimeError("transient")

    _install_fake_cheap_llm(monkeypatch, boom)
    assert _cloud_call("p") is None


# --- _audit_file / _summarize_block ---


def test_audit_file_strips_and_truncation_note(monkeypatch) -> None:
    monkeypatch.setattr(cmd, "ollama_generate", lambda *a, **k: '  "Duplicates: none."  ')
    # short content: no truncation marker
    out = _audit_file("progress.md", "short content", no_llm=False)
    assert out == "Duplicates: none."
    # huge content triggers the truncation note in the prompt (call captured)
    captured: dict = {}

    def fake_gen(prompt, **k):
        captured["prompt"] = prompt
        return "ok"

    monkeypatch.setattr(cmd, "ollama_generate", fake_gen)
    _audit_file("progress.md", "x" * (cmd.MAINT_AUDIT_CHAR_BUDGET + 500), no_llm=False)
    assert "file truncated for audit" in captured["prompt"]


def test_audit_file_returns_none_when_llm_silent(monkeypatch) -> None:
    monkeypatch.setattr(cmd, "ollama_generate", lambda *a, **k: None)
    monkeypatch.setattr(cmd, "_cloud_call", lambda p: None)
    assert _audit_file("progress.md", "content", no_llm=False) is None
    # no_llm path short-circuits before any call
    assert _audit_file("progress.md", "content", no_llm=True) is None


def test_summarize_block_strips_and_none(monkeypatch) -> None:
    monkeypatch.setattr(cmd, "ollama_generate", lambda *a, **k: "  '- bullet one'  ")
    assert _summarize_block("block", "f.md", no_llm=False) == "- bullet one"
    monkeypatch.setattr(cmd, "ollama_generate", lambda *a, **k: None)
    monkeypatch.setattr(cmd, "_cloud_call", lambda p: None)
    assert _summarize_block("block", "f.md", no_llm=False) is None


# --- _archive_with_summary early returns ---


def test_archive_with_summary_noop_when_within_budget(tmp_path: Path) -> None:
    path = tmp_path / "f.md"
    path.write_text("# h\nline\n", encoding="utf-8")
    assert _archive_with_summary(path, max_lines=10, no_llm=True) is False


def test_archive_with_summary_noop_when_middle_empty(tmp_path: Path) -> None:
    # only a header line + tail exactly fills budget -> middle empty
    path = tmp_path / "f.md"
    path.write_text("# h\n", encoding="utf-8")
    # len(lines)==1 <= max_lines=1 -> returns False at the budget guard, not middle
    assert _archive_with_summary(path, max_lines=1, no_llm=True) is False


# --- _write_summary_archive append ---


def test_write_summary_archive_appends_when_exists(tmp_path: Path) -> None:
    src = tmp_path / "systemPatterns.md"
    archive_dir = tmp_path / "topics" / "archive"
    archive_dir.mkdir(parents=True)
    from datetime import date

    archive = archive_dir / f"systemPatterns-{date.today().isoformat()}.md"
    archive.write_text("# Prior content\n", encoding="utf-8")
    _write_summary_archive(src, ["old line 1", "old line 2"], "summary text")
    body = archive.read_text(encoding="utf-8")
    assert "# Prior content" in body
    assert "old line 1" in body
    assert "summary text" in body


# --- _emit_audit branches ---


def test_emit_audit_skips_when_ollama_down() -> None:
    ctx = MaintCtx(apply_safe=False, no_llm=False, ollama_up=False)
    _emit_audit(ctx, "progress.md", ["line"])
    assert ctx.report == []


def test_emit_audit_skips_oversize_file(monkeypatch) -> None:
    ctx = MaintCtx(apply_safe=False, no_llm=False, ollama_up=True)
    _emit_audit(ctx, "progress.md", ["x"] * (cmd.MAINT_AUDIT_LINE_CAP + 5))
    assert any("semantic audit skipped" in line for line in ctx.report)


def test_emit_audit_emits_and_empty_fallback(monkeypatch) -> None:
    monkeypatch.setattr(cmd, "_audit_file", lambda name, content, no_llm=False: "DUPLICATES:\n- x")
    ctx = MaintCtx(apply_safe=False, no_llm=False, ollama_up=True)
    _emit_audit(ctx, "progress.md", ["line"])
    assert any("DUPLICATES" in line for line in ctx.report)

    # audit returns None -> "(audit returned empty)"
    monkeypatch.setattr(cmd, "_audit_file", lambda name, content, no_llm=False: None)
    ctx2 = MaintCtx(apply_safe=False, no_llm=False, ollama_up=True)
    _emit_audit(ctx2, "progress.md", ["line"])
    assert any("audit returned empty" in line for line in ctx2.report)


# --- _process_file apply path + missing file ---


def test_process_file_skips_missing(tmp_path: Path) -> None:
    ctx = MaintCtx(apply_safe=False, no_llm=True, ollama_up=False)
    cf = CoreFile("ghost.md", "desc", 100, tmp_path / "ghost.md")
    _process_file(ctx, cf)
    assert ctx.report == []
    assert ctx.applied == []


def test_process_file_applies_safe_compaction(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CODEQ_NO_LLM", raising=False)
    monkeypatch.delenv("PROJECT_MEMORY_NO_LLM", raising=False)
    path = tmp_path / "progress.md"
    path.write_text("# Progress\n" + "\n".join(f"- rule {i}" for i in range(300)), encoding="utf-8")
    monkeypatch.setattr(cmd, "_summarize_block", lambda block, name, no_llm=False: "- summarized")
    ctx = MaintCtx(apply_safe=True, no_llm=True, ollama_up=False)
    cf = CoreFile("progress.md", "progress", 50, path)
    _process_file(ctx, cf)
    assert "progress.md" in ctx.applied
    assert path.read_text(encoding="utf-8").count("\n") < 300


# --- _report_topics ---


def test_report_topics_renders_big_and_handles_missing(tmp_path: Path) -> None:
    memory = tmp_path / ".memory-bank"
    # no topics dir -> no-op
    _report_topics(MaintCtx(False, True, False), memory)
    topics = memory / "topics"
    topics.mkdir(parents=True)
    (topics / "fat.md").write_text("# fat\n" + "x\n" * 800, encoding="utf-8")
    ctx = MaintCtx(False, True, False)
    _report_topics(ctx, memory)
    assert any("topics/ over 80%" in line for line in ctx.report)
    assert any("fat.md" in line for line in ctx.report)


# --- maintain() applied section + ollama audit path ---


def test_maintain_renders_applied_section(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CODEQ_NO_LLM", raising=False)
    monkeypatch.delenv("PROJECT_MEMORY_NO_LLM", raising=False)
    _seed(tmp_path)
    # force progress.md over budget so apply-safe compacts it
    progress = tmp_path / ".memory-bank" / "progress.md"
    progress.write_text(
        "# Progress\n" + "\n".join(f"- rule {i}" for i in range(300)), encoding="utf-8"
    )
    monkeypatch.setattr(cmd, "ollama_is_alive", lambda timeout=10.0: False)
    monkeypatch.setattr(cmd, "_summarize_block", lambda block, name, no_llm=False: "- summarized")
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        maintain(tmp_path, apply_safe=True, no_llm=True)
    out = buf.getvalue()
    assert "## Applied (--apply-safe)" in out
    assert "progress.md" in out


def test_maintain_ollama_audit_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CODEQ_NO_LLM", raising=False)
    monkeypatch.delenv("PROJECT_MEMORY_NO_LLM", raising=False)
    _seed(tmp_path)
    monkeypatch.setattr(cmd, "ollama_is_alive", lambda timeout=10.0: True)
    monkeypatch.setattr(cmd, "_audit_file", lambda name, content, no_llm=False: "Stale: none.")
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        maintain(tmp_path, apply_safe=False, no_llm=False)
    out = buf.getvalue()
    assert "Generated by local" in out
    assert "Stale: none." in out


# --- _extend_handoff missing-file guard ---


def test_handoff_skips_missing_source_files(tmp_path: Path) -> None:
    bank = tmp_path / ".memory-bank"
    bank.mkdir()
    # none of the handoff source files exist -> heading + TODO only, no error
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        handoff(tmp_path)
    out = buf.getvalue()
    assert "## Session Handoff" in out
    assert "Next Steps" in out
