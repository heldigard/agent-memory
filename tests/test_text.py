"""Tests for shared.text — safety, slugification, line counting, CSV splitting."""

from __future__ import annotations

import pytest
from pathlib import Path

from agent_memory.shared.text import (
    ensure_safe_text,
    line_count,
    slugify,
    split_csv,
    write_if_missing,
)


class TestEnsureSafeText:
    def test_normal_text_passes(self) -> None:
        ensure_safe_text("deploy completed successfully")

    def test_oversized_text_raises(self) -> None:
        with pytest.raises(SystemExit, match="over"):
            ensure_safe_text("x" * 1201)

    def test_custom_max_chars(self) -> None:
        with pytest.raises(SystemExit, match="over"):
            ensure_safe_text("x" * 51, max_chars=50)

    def test_secret_api_key_raises(self) -> None:
        with pytest.raises(SystemExit, match="secret"):
            ensure_safe_text("api_key=sk-abc123def456ghi789jkl012mno")

    def test_secret_bearer_token_raises(self) -> None:
        with pytest.raises(SystemExit, match="secret"):
            ensure_safe_text("Authorization: Bearer sk-abc123def456ghi789jkl012mno")

    def test_secret_password_raises(self) -> None:
        with pytest.raises(SystemExit, match="secret"):
            ensure_safe_text("password=SuperSecret123!")

    def test_secret_private_key_raises(self) -> None:
        with pytest.raises(SystemExit, match="secret"):
            ensure_safe_text("-----BEGIN RSA PRIVATE KEY-----")

    def test_harmless_text_with_word_key_passes(self) -> None:
        ensure_safe_text("the keyboard shortcut is Ctrl+C")


class TestSlugify:
    def test_normal_name(self) -> None:
        assert slugify("Auth Flow") == "auth-flow"

    def test_special_chars(self) -> None:
        assert slugify("My Topic! @#$%") == "my-topic"

    def test_unicode(self) -> None:
        result = slugify("café résumé")
        assert result  # non-empty
        assert " " not in result

    def test_empty_slug_raises(self) -> None:
        with pytest.raises(SystemExit, match="empty"):
            slugify("!!!")

    def test_max_length(self) -> None:
        long_name = "a" * 200
        assert len(slugify(long_name)) <= 80

    def test_dots_preserved(self) -> None:
        assert slugify("v1.2.3") == "v1.2.3"


class TestWriteIfMissing:
    def test_creates_new_file(self, tmp_path: Path) -> None:
        target = tmp_path / "new.md"
        assert write_if_missing(target, "hello") is True
        assert target.read_text() == "hello"

    def test_skips_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "existing.md"
        target.write_text("original")
        assert write_if_missing(target, "overwritten") is False
        assert target.read_text() == "original"


class TestSplitCsv:
    def test_normal_csv(self) -> None:
        assert split_csv("a, b, c") == ["a", "b", "c"]

    def test_empty_returns_none(self) -> None:
        assert split_csv(None) is None
        assert split_csv("") is None

    def test_whitespace_only_returns_empty(self) -> None:
        assert split_csv("  ,  ,  ") == []

    def test_single_value(self) -> None:
        assert split_csv("hello") == ["hello"]


class TestLineCount:
    def test_normal_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("line1\nline2\nline3\n")
        assert line_count(f) == 3

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.md"
        f.write_text("")
        assert line_count(f) == 0

    def test_missing_file_returns_zero(self, tmp_path: Path) -> None:
        assert line_count(tmp_path / "nope.md") == 0
