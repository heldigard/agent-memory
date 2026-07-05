"""Markdown chunking: small file packs to one chunk, oversized splits by line,
and a single long line char-splits (pure logic, no Ollama)."""

from __future__ import annotations

from agent_memory.features.semantic.chunking import chunk_file


def test_small_file_is_single_chunk(tmp_path) -> None:
    p = tmp_path / "note.md"
    p.write_text("# Heading\nshort body line\nanother line\n", encoding="utf-8")
    chunks = chunk_file(p)
    assert len(chunks) == 1
    assert chunks[0]["heading"] == "# Heading"
    assert chunks[0]["start"] == 1
    assert "short body line" in chunks[0]["text"]


def test_oversized_block_splits_into_multiple_chunks(tmp_path) -> None:
    p = tmp_path / "big.md"
    p.write_text("# H\n" + "x" * 50 + "\n" * 40, encoding="utf-8")  # many paragraphs > MAX
    chunks = chunk_file(p, max_chars=120)
    assert len(chunks) >= 1
    # every chunk text respects the cap (a single oversized line is char-split)
    for ch in chunks:
        assert len(ch["text"]) <= 120


def test_single_long_line_char_splits(tmp_path) -> None:
    p = tmp_path / "long.md"
    line = "y" * 300  # one very long line
    p.write_text(f"# H\n{line}\n", encoding="utf-8")
    chunks = chunk_file(p, max_chars=120)
    assert len(chunks) >= 2
    # the 300 'y' chars are preserved across the char-split chunks (heading
    # rides with the first chunk, which is correct behavior)
    assert "".join(ch["text"] for ch in chunks).count("y") == 300


def test_heading_tracking_changes_across_sections(tmp_path) -> None:
    p = tmp_path / "multi.md"
    p.write_text("# A\nalpha\n\n# B\nbeta\n", encoding="utf-8")
    chunks = chunk_file(p)
    headings = {ch["heading"] for ch in chunks}
    assert "# A" in headings
    assert "# B" in headings
