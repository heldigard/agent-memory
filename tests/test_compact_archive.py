"""compact.command archive/ref/index branches (coverage).

Over-budget archiving, protected-only skip, topic compaction, dangling-ref
detection, index removal, and archive_topic move semantics.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_memory.features.bank.command import init_memory
from agent_memory.features.compact.command import (
    _compact_topics,
    _print_dangling_refs,
    _remove_topic_from_index,
    _write_archive,
    archive_old_lines,
    archive_topic,
    compact_memory,
    find_refs_to_slug,
)


def _bank(tmp_path: Path) -> Path:
    init_memory(tmp_path)
    return tmp_path / ".memory-bank"


# --- archive_old_lines: protected-only skip + mixed message ---


def test_archive_old_lines_all_protected_skips(tmp_path: Path, capsys) -> None:
    active = "- 2026-07-18T00:00:00Z | status:active | in-flight deploy work here"
    lines = ["# progress", active, active, active, active, active, active]
    result = archive_old_lines(tmp_path / "progress.md", lines, 3)
    assert result is lines  # unchanged: nothing archivable
    assert "Protected" in capsys.readouterr().out
    # no archive dir created when skipped
    assert not (tmp_path / "topics" / "archive").exists()


def test_archive_old_lines_mixed_keeps_protected_and_notes(tmp_path: Path, capsys) -> None:
    active = "- 2026-07-18T00:00:00Z | status:active | keep me inline please"
    old = [f"- 2019-01-01T00:00:00Z stale entry number {i}" for i in range(8)]
    # active placed in the MIDDLE (not the tail) so it is detected as protected
    lines = ["# progress", active, *old]
    result = archive_old_lines(tmp_path / "progress.md", lines, 4)
    assert active in result
    out = capsys.readouterr().out
    assert "protected 1 active/live" in out
    assert "Archived" in out


def test_archive_old_lines_no_middle_returns_unchanged(tmp_path: Path) -> None:
    # header + tail exactly fills budget so middle is empty -> no-op
    lines = ["# h", "only one body line that is short"]
    result = archive_old_lines(tmp_path / "f.md", lines, 3)
    assert result is lines


# --- _write_archive append ---


def test_write_archive_appends_to_existing(tmp_path: Path) -> None:
    # _write_archive derives the archive dir from path.parent, so the source
    # path must live inside the bank for the archive to land beside it.
    memory = tmp_path / ".memory-bank"
    archive_dir = memory / "topics" / "archive"
    archive_dir.mkdir(parents=True)
    from datetime import date

    archive = archive_dir / f"progress-{date.today().isoformat()}.md"
    archive.write_text("# Prior archive body\n", encoding="utf-8")
    _write_archive(memory / "progress.md", ["- a", "- b"], protected_count=0)
    body = archive.read_text(encoding="utf-8")
    assert "# Prior archive body" in body
    assert "- a" in body and "- b" in body


def test_write_archive_notes_protected_count(tmp_path: Path) -> None:
    memory = tmp_path / ".memory-bank"
    archive_dir = memory / "topics" / "archive"
    _write_archive(memory / "progress.md", ["- a"], protected_count=3)
    archive = next(archive_dir.glob("progress-*.md"))
    assert "3 active/live entries kept inline" in archive.read_text(encoding="utf-8")


# --- _compact_topics + compact_memory include_topics ---


def test_compact_topics_compacts_oversize_topic(tmp_path: Path) -> None:
    bank = _bank(tmp_path)
    fat = bank / "topics" / "fat.md"
    fat.write_text(
        "# fat\n" + "\n".join(f"- 2019-01-01 e{i}" for i in range(900)), encoding="utf-8"
    )
    changed = _compact_topics(bank, target_ratio=1.0)
    assert "topics/fat.md" in changed
    assert len(fat.read_text(encoding="utf-8").splitlines()) < 900


def test_compact_memory_include_topics(tmp_path: Path, capsys) -> None:
    bank = _bank(tmp_path)
    (bank / "topics" / "big.md").write_text(
        "# big\n" + "\n".join(f"- 2019-01-01 e{i}" for i in range(900)), encoding="utf-8"
    )
    compact_memory(tmp_path, include_topics=True, target_ratio=1.0)
    out = capsys.readouterr().out
    assert "topics/big.md" in out


# --- find_refs_to_slug ---


def test_find_refs_finds_and_excludes_self_and_index(tmp_path: Path, monkeypatch) -> None:
    bank = _bank(tmp_path)
    topics = bank / "topics"
    (topics / "auth.md").write_text("# Auth\n", encoding="utf-8")
    (bank / "progress.md").write_text("- see auth work and also [[auth]]\n", encoding="utf-8")
    refs = find_refs_to_slug(tmp_path, "auth")
    relpaths = {r[0] for r in refs}
    assert any("progress.md" in p for p in relpaths)
    # the topic file itself and _index.md are excluded
    assert not any(r[0] == "topics/auth.md" for r in refs)
    assert not any("_index.md" in r[0] for r in refs)


def test_find_refs_swallows_oserror(tmp_path: Path, monkeypatch) -> None:
    _bank(tmp_path)

    def boom(self, *a, **k):
        raise OSError("denied")

    monkeypatch.setattr(Path, "read_text", boom)
    assert find_refs_to_slug(tmp_path, "auth") == []


# --- _remove_topic_from_index ---


def test_remove_topic_from_index_missing_and_no_match(tmp_path: Path) -> None:
    idx = tmp_path / "_index.md"
    assert _remove_topic_from_index(idx, "auth") is False  # missing file
    idx.write_text("# Topics\n- [Other](other.md)\n", encoding="utf-8")
    assert _remove_topic_from_index(idx, "auth") is False  # no matching marker


def test_remove_topic_from_index_removes_entry(tmp_path: Path) -> None:
    idx = tmp_path / "_index.md"
    idx.write_text("# Topics\n- [Auth](auth.md)\n- [Other](other.md)\n", encoding="utf-8")
    assert _remove_topic_from_index(idx, "auth") is True
    assert "(auth.md)" not in idx.read_text(encoding="utf-8")
    assert "(other.md)" in idx.read_text(encoding="utf-8")


# --- _print_dangling_refs ---


def test_print_dangling_refs_truncates_over_ten(capsys) -> None:
    refs = [(f"f{i}.md", i, f"snippet {i}") for i in range(12)]
    _print_dangling_refs("auth", refs)
    out = capsys.readouterr().out
    assert "ABORT" in out
    assert "12 active reference" in out
    assert "2 more" in out
    assert "rerun with --force" in out


# --- archive_topic ---


def test_archive_topic_not_found(tmp_path: Path) -> None:
    _bank(tmp_path)
    with pytest.raises(SystemExit, match="Topic not found"):
        archive_topic(tmp_path, "ghost")


def test_archive_topic_aborts_on_dangling_refs(tmp_path: Path, capsys) -> None:
    bank = _bank(tmp_path)
    (bank / "topics" / "auth.md").write_text("# Auth\n", encoding="utf-8")
    (bank / "progress.md").write_text("- depends on (auth.md) heavily\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        archive_topic(tmp_path, "auth")
    assert "ABORT" in capsys.readouterr().out
    # source untouched on abort
    assert (bank / "topics" / "auth.md").exists()


def test_archive_topic_force_moves_and_clears_index(tmp_path: Path, capsys) -> None:
    bank = _bank(tmp_path)
    topics = bank / "topics"
    (topics / "legacy.md").write_text("# Legacy\nbody\n", encoding="utf-8")
    (topics / "_index.md").write_text("# Topics\n- [Legacy](legacy.md)\n", encoding="utf-8")
    archive_topic(tmp_path, "legacy", force=True)
    out = capsys.readouterr().out
    assert "Archived topic: legacy.md" in out
    assert "Removed entry from topics/_index.md" in out
    assert not (topics / "legacy.md").exists()
    archived = list((topics / "archive").glob("legacy-*.md"))
    assert archived and "body" in archived[0].read_text(encoding="utf-8")
    assert "(legacy.md)" not in (topics / "_index.md").read_text(encoding="utf-8")


def test_archive_topic_force_appends_to_existing_archive(tmp_path: Path) -> None:
    bank = _bank(tmp_path)
    topics = bank / "topics"
    archive_dir = topics / "archive"
    archive_dir.mkdir(parents=True)
    from datetime import date

    dst = archive_dir / f"legacy-{date.today().isoformat()}.md"
    dst.write_text("# Prior legacy archive\n", encoding="utf-8")
    (topics / "legacy.md").write_text("# Legacy\nfresh body\n", encoding="utf-8")
    archive_topic(tmp_path, "legacy", force=True)
    body = dst.read_text(encoding="utf-8")
    assert "# Prior legacy archive" in body
    assert "fresh body" in body


def test_archive_topic_no_index_entry_message(tmp_path: Path, capsys) -> None:
    bank = _bank(tmp_path)
    topics = bank / "topics"
    (topics / "orphan.md").write_text("# Orphan\n", encoding="utf-8")
    # _index.md has no entry for orphan.md
    archive_topic(tmp_path, "orphan", force=True)
    assert "no _index.md entry found" in capsys.readouterr().out


def test_write_archive_topic_file_lands_in_topics_archive(tmp_path: Path) -> None:
    """A topic file already lives under <bank>/topics/ — its archive must land
    in topics/archive/, not a double-nested topics/topics/archive/ (the same
    bug commit 8099ae4 fixed in maintain)."""
    bank = _bank(tmp_path)
    topic = bank / "topics" / "deep.md"
    topic.write_text("# deep\n", encoding="utf-8")
    _write_archive(topic, ["- 2019-01-01T00:00:00Z old line"], 0)
    assert (bank / "topics" / "archive").is_dir()
    assert not (bank / "topics" / "topics").exists()


def test_add_entry_topic_uses_topic_soft_limit(tmp_path: Path) -> None:
    """`add --file topics/foo.md` must compact at TOPIC_SOFT_LIMIT, not the
    generic 60-line FILES fallback — a 100-line topic stays intact."""
    from agent_memory.features.bank.command import add_entry
    from agent_memory.shared.config import TOPIC_SOFT_LIMIT

    bank = _bank(tmp_path)
    topic = bank / "topics" / "deep.md"
    topic.write_text(
        "# deep\n" + "\n".join(f"- 2026-01-01T00:00:00Z line {i}" for i in range(100)) + "\n",
        encoding="utf-8",
    )
    assert TOPIC_SOFT_LIMIT > 101  # premise: 101 lines only trip the 60-line fallback
    add_entry(tmp_path, "topics/deep.md", "one more line", status="completed")
    lines = topic.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 102  # header + 100 + new entry; nothing archived
    assert not (bank / "topics" / "archive").exists()
