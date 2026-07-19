# vs-soft-allow: nesting_depth — atomic append-with-trailing-newline guard (try/if/with/if);
# one responsibility (append a recuerda note); flattening would split a 4-line safe-write.
"""Recuerda auto-append hook.

On UserPromptSubmit, detect Spanish "recuerda" / English "remember" / "don't forget"
phrases. If found, append the note to the project's .memory-bank/activeContext.md.

Idempotent: appends only the new note, not the trigger word itself.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime

from agent_memory.shared.paths import hook_root
from agent_memory.shared.text import redact_secrets

TRIGGERS: list[str] = [
    r"\brecuerd[ae]\b",  # recuerda, recordá
    r"\bremember\b",  # English
    r"\bdon'?t forget\b",  # English
    r"\bno olvides\b",  # Spanish
    r"\bno olvid[áa]r\b",
    r"\bnota:?\s",  # "nota: ..." or "nota ..."
    r"\bnote:?\s",  # English
    r"\bimportante:?\s",
    r"\bimportant:?\s",
]


def load_prompt() -> str:
    try:
        raw_data = sys.stdin.read()
        if not raw_data.strip():
            return ""
        data = json.loads(raw_data)
        if isinstance(data, dict):
            val = data.get("prompt", "")
            return str(val).strip()
    except Exception:
        pass
    return ""


def main() -> int:
    prompt = load_prompt()
    if not prompt:
        return 0

    # Only fire for explicit remember/recuerda phrases
    if not any(re.search(p, prompt, re.IGNORECASE) for p in TRIGGERS):
        return 0

    # Don't double-log if memory-inject is doing it
    if "[NO_MEM_APPEND]" in prompt:
        return 0

    bank = hook_root() / ".memory-bank"
    active = bank / "activeContext.md"

    if not active.exists():
        # Bank not initialized; let agent-memory skill handle it
        return 0

    # Extract the note (strip trigger phrase, keep the actual content)
    note = prompt.strip()
    # Remove the trigger phrase itself, keep the rest
    for pat in TRIGGERS:
        note = re.sub(pat, "", note, count=1, flags=re.IGNORECASE)
    note = note.strip(" :,-.\n\t")
    if not note or len(note) < 5:
        return 0

    # Collapse whitespace/newlines so a pasted multi-line dump becomes ONE bounded line.
    note = re.sub(r"\s+", " ", note).strip()

    # Automatic capture keeps useful context but must never bypass the secret
    # rejection applied to explicit CLI writes. Redact before truncation so a
    # long credential cannot survive as a partial value.
    note = redact_secrets(note)
    useful_note = note.replace("[REDACTED]", "").strip(" :,-.\n\t")
    if len(useful_note) < 5:
        return 0

    # Cap length: a recuerda note is short.
    MAX_NOTE = 300
    if len(note) > MAX_NOTE:
        note = note[: MAX_NOTE - 28].rstrip() + " […] (nota truncada; contexto largo → topics/)"

    # Append with timestamp
    today = datetime.now().strftime("%Y-%m-%d")
    entry = f"- {today}: {note}\n"

    try:
        if active.exists() and active.stat().st_size > 0:
            with active.open("r+b") as f:
                f.seek(-1, 2)
                if f.read(1) != b"\n":
                    f.write(b"\n")
        with active.open("a", encoding="utf-8") as f:
            f.write(entry)
    except OSError:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
