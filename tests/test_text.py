"""Tests for shared.text — safety, slugification, line counting, CSV splitting."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_memory.shared.text import (
    atomic_write_text,
    ensure_safe_text,
    line_count,
    redact_secrets,
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
        key = "sk-" + "abc123def456ghi789jkl012mno"
        with pytest.raises(SystemExit, match="secret"):
            ensure_safe_text(f"api_key={key}")

    def test_secret_value_assignment_raises(self) -> None:
        with pytest.raises(SystemExit, match="secret"):
            ensure_safe_text("secret=synthetic-value")

    def test_secret_bearer_token_raises(self) -> None:
        key = "sk-" + "abc123def456ghi789jkl012mno"
        with pytest.raises(SystemExit, match="secret"):
            ensure_safe_text(f"Authorization: Bearer {key}")

    def test_secret_password_raises(self) -> None:
        with pytest.raises(SystemExit, match="secret"):
            ensure_safe_text("password=SuperSecret123!")

    def test_secret_private_key_raises(self) -> None:
        with pytest.raises(SystemExit, match="secret"):
            ensure_safe_text("-----BEGIN RSA PRIVATE KEY-----")

    def test_harmless_text_with_word_key_passes(self) -> None:
        ensure_safe_text("the keyboard shortcut is Ctrl+C")

    def test_operational_secret_scanner_note_passes(self) -> None:
        ensure_safe_text(
            "Updated doctor to enforce the codescan structured_json contract for "
            "dead/sec/secrets/arch/all/capabilities."
        )

    def test_secret_shaped_fixture_note_passes(self) -> None:
        ensure_safe_text(
            "Cleaned scrubber test fixtures. Secret-shaped fixtures are now constructed "
            "from synthetic parts so gitleaks reports 0 findings."
        )

    def test_conceptual_token_note_passes(self) -> None:
        ensure_safe_text("Documented auth token refresh behavior without raw values.")

    def test_generic_token_assignment_raises(self) -> None:
        key_name = "to" + "ken"
        with pytest.raises(SystemExit, match="secret"):
            ensure_safe_text(f"{key_name}=synthetic-value")


class TestRedactSecrets:
    def test_redacts_every_match_and_preserves_context(self) -> None:
        key = "sk-" + "abc123def456ghi789jkl012mno"
        text = f"deploy staging with api_key={key} and password=synthetic-value"

        redacted = redact_secrets(text)

        assert redacted == "deploy staging with [REDACTED] and [REDACTED]"
        assert key not in redacted

    def test_redacts_complete_authorization_header(self) -> None:
        key = "sk-" + "abc123def456ghi789jkl012mno"

        redacted = redact_secrets(f"use Authorization: Bearer {key} for the migration")

        assert redacted == "use [REDACTED] for the migration"

    def test_redacts_complete_quoted_value_with_spaces(self) -> None:
        key_name = "pass" + "word"

        redacted = redact_secrets(f'use {key_name}="synthetic words only" for the test')

        assert redacted == "use [REDACTED] for the test"

    def test_harmless_security_vocabulary_is_untouched(self) -> None:
        text = "Documented token refresh and secret scanner behavior."
        assert redact_secrets(text) == text


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


class TestAtomicWriteText:
    """atomic_write_text must leave either the old or the new file, never a
    truncated/partial one — that is the corruption fix for the markdown banks."""

    def test_writes_new_file(self, tmp_path: Path) -> None:
        target = tmp_path / "f.md"
        atomic_write_text(target, "body\n")
        assert target.read_text() == "body\n"

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "f.md"
        target.write_text("old", encoding="utf-8")
        atomic_write_text(target, "new")
        assert target.read_text() == "new"

    def test_no_tmp_residue_on_success(self, tmp_path: Path) -> None:
        target = tmp_path / "f.md"
        atomic_write_text(target, "x")
        assert not list(tmp_path.glob(".*.tmp"))

    def test_target_untouched_if_replace_fails(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        # The atomicity guarantee: if os.replace raises (simulated crash), the
        # target keeps its prior content and the tmp is cleaned — no truncation.
        target = tmp_path / "f.md"
        target.write_text("original", encoding="utf-8")

        def boom(_src: str, _dst: str) -> None:
            raise OSError("simulated mid-replace failure")

        monkeypatch.setattr("agent_memory.shared.text.os.replace", boom)
        with pytest.raises(OSError):
            atomic_write_text(target, "new-content")
        assert target.read_text() == "original"
        assert not list(tmp_path.glob(".*.tmp"))

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "deep" / "f.md"
        atomic_write_text(target, "x")
        assert target.read_text() == "x"

    def test_repeated_writes_no_tmp_leak(self, tmp_path: Path) -> None:
        # Two writes to the same target (unique tmp per call) leave no litter.
        target = tmp_path / "f.md"
        atomic_write_text(target, "a")
        atomic_write_text(target, "b")
        assert target.read_text() == "b"
        assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize(
    "token",
    [
        pytest.param("ghp_" + "a" * 36, id="github-classic"),
        pytest.param("github_pat_" + "B" * 50, id="github-finegrained"),
        pytest.param("xoxb-" + "0123456789-cdefghijkl", id="slack-bot"),
        pytest.param("AKIA" + "ABCDEFGHIJKLMNOP", id="aws-access-key"),
    ],
)
class TestSecretTokenLiterals:
    """Provider-scoped token literals (GitHub/Slack/AWS) must be treated as
    secrets on both the redaction path (hooks) and the reject path (CLI writes)."""

    def test_redact_replaces_provider_token(self, token: str) -> None:
        redacted = redact_secrets(f"use token {token} for the deploy")
        assert token not in redacted
        assert "[REDACTED]" in redacted

    def test_ensure_safe_text_rejects_provider_token(self, token: str) -> None:
        with pytest.raises(SystemExit, match="secret"):
            ensure_safe_text(f"use token {token} for the deploy")


def test_prose_mentioning_token_concepts_stays_safe() -> None:
    """Naming credential concepts without a real value must not trip the guard."""
    prose = "Rotated the GitHub and Slack credentials without exposing any value."
    assert redact_secrets(prose) == prose
    ensure_safe_text(prose)
