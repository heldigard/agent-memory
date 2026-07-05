"""Compaction: over-budget lines archive to topics/archive/, and protected
(active/wip/live/recent-completed) entries are preserved inline."""

from __future__ import annotations

from agent_memory.features.compact.command import archive_old_lines, compact_file, compact_memory


def test_archive_old_lines_no_op_at_or_under_budget(tmp_path) -> None:
    lines = ["# h", "- one", "- two"]
    assert archive_old_lines(tmp_path / "x.md", lines, 3) is lines  # returned unchanged


def test_archive_old_lines_preserves_active_entry(tmp_path) -> None:
    active = "- 2026-07-04T00:00:00Z | status:active | in-flight deploy"
    lines = ["# progress", *[f"- 2026-01-01T00:00:00Z entry {i}" for i in range(10)], active]
    result = archive_old_lines(tmp_path / "progress.md", lines, 5)
    assert active in result, "active entry must be preserved inline through compaction"


def test_archive_old_lines_preserves_live_entry(tmp_path) -> None:
    live = "- 2026-07-04T00:00:00Z | status:live | runbook"
    lines = ["# ref", *[f"- 2026-01-01T00:00:00Z entry {i}" for i in range(10)], live]
    result = archive_old_lines(tmp_path / "ref.md", lines, 5)
    assert live in result


def test_compact_file_writes_reduced_file(tmp_path) -> None:
    p = tmp_path / "dead-ends.md"
    p.write_text(
        "# dead-ends\n" + "\n".join(f"- 2026-01-01 entry {i}" for i in range(20)) + "\n",
        encoding="utf-8",
    )
    assert compact_file(p, 5)
    remaining = p.read_text(encoding="utf-8").splitlines()
    assert len(remaining) <= 6  # header + compaction marker + tail within budget+1


def test_compact_memory_runs_over_all_core_files(tmp_path) -> None:
    # build a minimal bank (init not needed; compact tolerates missing files)
    bank = tmp_path / ".memory-bank"
    bank.mkdir()
    (bank / "progress.md").write_text(
        "# progress\n" + "\n".join(f"- 2026-01-01 e{i}" for i in range(50)) + "\n", encoding="utf-8"
    )
    compact_memory(tmp_path)  # should not raise; prints a summary line
