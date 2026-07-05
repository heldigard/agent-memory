"""project_root resolution: explicit ``--root`` precedence, git-toplevel climb,
and the nested-bank guard (a project under a git-rooted parent resolves its OWN
bank, never the parent's)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agent_memory.shared.paths import bank_dir, file_name, iter_memory_files, project_root


def test_project_root_with_explicit_bank(tmp_path: Path) -> None:
    """An explicit path holding a bank returns that path without git climbing."""
    (tmp_path / ".memory-bank").mkdir()
    assert project_root(tmp_path) == tmp_path.resolve()


def test_project_root_falls_back_to_cwd_without_git(tmp_path: Path, monkeypatch) -> None:
    """No bank + no git ancestor → the start dir itself."""
    monkeypatch.chdir(tmp_path)
    assert project_root(None) == tmp_path.resolve()


def test_project_root_climbs_to_git_toplevel(tmp_path: Path) -> None:
    """A bank-less dir inside a git repo resolves to the git toplevel."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    sub = tmp_path / "pkg" / "inner"
    sub.mkdir(parents=True)
    (tmp_path / "file").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    assert project_root(sub).resolve() == tmp_path.resolve()


def test_nested_bank_does_not_climb_to_git_parent(tmp_path: Path) -> None:
    """A nested project with its own bank under a git-rooted parent keeps its own bank."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / ".memory-bank").mkdir()
    assert project_root(nested).resolve() == nested.resolve()


def test_file_name_resolves_aliases_and_bare_slugs() -> None:
    assert file_name("memory") == "MEMORY.md"
    assert file_name("MEMORY.md") == "MEMORY.md"
    assert file_name("notes") == "notes.md"
    assert file_name("systempatterns") == "systemPatterns.md"


def test_iter_memory_files_lists_core_and_topics_not_archive(tmp_path: Path) -> None:
    bank = bank_dir(tmp_path)
    (bank / "topics").mkdir(parents=True)
    (bank / "MEMORY.md").write_text("# m\n", encoding="utf-8")
    (bank / "topics" / "auth.md").write_text("# a\n", encoding="utf-8")
    archive = bank / "topics" / "archive"
    archive.mkdir()
    (archive / "old.md").write_text("# o\n", encoding="utf-8")
    rels = [p.name for p in iter_memory_files(bank)]
    assert "MEMORY.md" in rels
    assert "auth.md" in rels
    assert "old.md" not in rels  # glob is non-recursive → archive excluded
