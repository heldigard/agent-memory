"""``agent-memory doctor`` implementation — proactive health check.

# vs-soft-allow — single responsibility (bank health check) expressed as a
# pipeline of independent checks; the "nesting" the shape guard counts is
# multi-line ``Finding(...)`` data literals inside guard clauses, not control
# flow. Each check is one flat ``_check_*`` helper.

Surfaces, in one read-only pass:
  * core/topic files over their budgets (reuses the budget-guard thresholds)
  * broken topic references (``[[slug]]`` / ``(slug.md)`` pointing at no file)
  * ``active``/``wip`` entries whose ``session:pid:N`` is no longer alive
  * a semantic index that is missing, shape-mismatched, or holds orphan chunks
  * chunk-hash collisions in the manifest (same sha, different text — the dedup
    key is truncated, so collisions are surfaced even though they are unlikely)

Every check returns a list of :class:`Finding` records; :func:`doctor` formats
them for humans or emits JSON. Mutates nothing.
"""

from __future__ import annotations

import contextlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from agent_memory.features.semantic.index import index_dir, load_index
from agent_memory.hooks.budget_guard import collect_warnings
from agent_memory.shared.config import TOPICS_DIR
from agent_memory.shared.entries import _pid_is_alive, _session_pid, parse_entry
from agent_memory.shared.ollama import is_alive as ollama_is_alive
from agent_memory.shared.paths import bank_dir, iter_memory_files

# Severity ordering used when rendering the human report.
_SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}

# ``[[slug]]`` wiki link OR ``(slug.md)`` paren ref. Slug charset matches
# ``slugify`` (lowercase ascii + digits + dash).
_WIKI_RE = re.compile(r"\[\[([a-z0-9][a-z0-9-]*)\]\]")
_PAREN_RE = re.compile(r"\(([a-z0-9][a-z0-9-]*)\.md\)")


@dataclass
class Finding:
    """One doctor finding: a problem (or an info note) with location + hint."""

    severity: str  # "error" | "warn" | "info"
    check: str
    detail: str
    hint: str = ""

    def as_line(self) -> str:
        """Render as one human-readable indented line."""
        tag = self.severity.upper()
        base = f"  [{tag}] {self.check}: {self.detail}"
        return f"{base}\n        → {self.hint}" if self.hint else base


def run_doctor(root: Path) -> list[Finding]:
    """Run every check against the bank at ``root``. Read-only; returns findings.

    Empty when the bank is healthy. Errors block trust in the bank (corrupt
    index, dead active work); warns are approaching-limit / drift nudges."""
    memory = bank_dir(root)
    if not memory.is_dir():
        return [
            Finding(
                severity="error",
                check="bank",
                detail=f"no memory bank at {memory}",
                hint="run `agent-memory init`",
            )
        ]
    findings: list[Finding] = []
    findings.extend(_check_budgets(memory))
    findings.extend(_check_broken_refs(memory))
    findings.extend(_check_dead_pids(memory))
    findings.extend(_check_index(root, memory))
    findings.extend(_check_harness_integration())
    findings.sort(key=lambda f: _SEVERITY_ORDER.get(f.severity, 9))
    return findings


def _check_budgets(memory: Path) -> list[Finding]:
    """Core/topic files at/over their line budgets (advisory thresholds)."""
    warnings = collect_warnings(memory)
    out: list[Finding] = []
    for line in warnings:
        # budget_guard formats lines as "  [RED]    name: ..." / "  [YELLOW] ..."
        stripped = line.strip()
        severity = "error" if stripped.startswith("[RED]") else "warn"
        out.append(
            Finding(
                severity=severity,
                check="budget",
                detail=stripped,
                hint="archive detail to topics/archive/ or split the file",
            )
        )
    return out


def _check_broken_refs(memory: Path) -> list[Finding]:
    """``[[slug]]`` / ``(slug.md)`` references with no matching topic file.

    Archives are excluded both as referrers and as targets (archived topics are
    allowed to dangle). Returns one finding per broken target slug (aggregated
    across referrers), capped for readability."""
    existing_topics = {p.stem for p in iter_memory_files(memory) if p.parent.name == TOPICS_DIR}
    # Referrers = core + active topic files (NOT archive/).
    referrers = [p for p in iter_memory_files(memory) if p.parent.name != "archive"]

    broken: dict[str, list[str]] = defaultdict(list)
    for path in referrers:
        for slug in _referenced_slugs(path):
            if slug in existing_topics:
                continue
            rel = str(path.relative_to(memory))
            if rel not in broken[slug]:
                broken[slug].append(rel)
    if not broken:
        return []
    return [
        Finding(
            severity="warn",
            check="broken-ref",
            detail=f"[[{slug}]] referenced by {', '.join(sorted(broken[slug]))} but "
            f"topics/{slug}.md is missing",
            hint="restore the topic, or drop the stale reference",
        )
        for slug in sorted(broken)[:20]
    ]


def _referenced_slugs(path: Path) -> set[str]:
    """Distinct ``[[slug]]`` / ``(slug.md)`` slugs referenced in one file."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return set(_WIKI_RE.findall(content)) | set(_PAREN_RE.findall(content))


def _check_dead_pids(memory: Path) -> list[Finding]:
    """``active``/``wip`` entries whose ``session:pid:N`` process is dead.

    A dead PID on an active entry means the in-flight work it tracked has ended
    without the entry being marked completed — a stale-active handoff risk."""
    out: list[Finding] = []
    for path in iter_memory_files(memory):
        rel = str(path.relative_to(memory))
        out.extend(_dead_pid_findings_in(path, rel))
    return out


def _dead_pid_findings_in(path: Path, rel: str) -> list[Finding]:
    """Dead-PID findings for one file (skips silently on read error)."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[Finding] = []
    for line in lines:
        info = parse_entry(line)
        if info["status"] not in {"active", "wip"}:
            continue
        pid = _session_pid(info["session"])
        if pid is None or _pid_is_alive(pid):
            continue
        snippet = (info["text"] or line.strip())[:80]
        out.append(
            Finding(
                severity="warn",
                check="dead-pid",
                detail=f"{rel}: active entry pid={pid} not running — {snippet!r}",
                hint="mark the entry completed, or restart the tracked process",
            )
        )
    return out


def _check_harness_integration() -> list[Finding]:
    """Verify that global harness shims and environment variables are healthy."""
    import shutil
    out: list[Finding] = []

    # 1. Check if agent-memory is on PATH
    if not shutil.which("agent-memory"):
        out.append(
            Finding(
                severity="warn",
                check="harness-path",
                detail="command 'agent-memory' not found on PATH",
                hint="run `uv pip install -e .` or verify shell path setup",
            )
        )

    # 2. Check for crucial shims in ~/.claude/
    home = Path.home()
    shims = {
        ".claude/hooks/memory-bank-budget-guard.py": "memory budget-guard hook",
        ".claude/hooks/decision-tracker.py": "decision-tracker hook",
        ".claude/hooks/recuerda-auto-append.py": "recuerda auto-append hook",
        ".claude/hooks/memory-inject.sh": "memory injection startup hook",
        ".claude/scripts/project-memory.py": "project-memory CLI backcompat shim",
        ".claude/scripts/memory-auto-maintain.py": "memory-auto-maintain CLI backcompat shim",
        ".claude/scripts/memory-semantic.py": "memory-semantic CLI backcompat shim",
    }
    for rel, desc in shims.items():
        p = home / rel
        if not p.is_file():
            out.append(
                Finding(
                    severity="warn",
                    check="harness-shim",
                    detail=f"global harness shim missing: {rel} ({desc})",
                    hint="re-run harness auto-setup or restore the shim file",
                )
            )
    return out


def _check_index(root: Path, memory: Path) -> list[Finding]:
    """Semantic index: existence, shape consistency, orphans, hash collisions."""
    idx = index_dir(root)
    vpath, mpath = idx / "vectors.npz", idx / "manifest.json"
    if not (vpath.exists() and mpath.exists()):
        return [
            Finding(
                severity="info",
                check="index",
                detail="no semantic index yet",
                hint="run `agent-memory semindex` to enable semantic recall",
            )
        ]
    vectors, manifest = load_index(idx)
    out: list[Finding] = []
    n_vec = vectors.shape[0] if vectors.ndim == 2 else 0
    if len(manifest) != n_vec:
        out.append(
            Finding(
                severity="error",
                check="index",
                detail=f"shape mismatch: manifest={len(manifest)} chunks vs vectors={n_vec} rows",
                hint="run `agent-memory semindex --rebuild` to regenerate",
            )
        )
    current_files = {str(p.relative_to(memory)) for p in iter_memory_files(memory)}
    orphans = [r.get("file", "?") for r in manifest if r.get("file") not in current_files]
    if orphans:
        out.append(
            Finding(
                severity="warn",
                check="index",
                detail=f"{len(orphans)} orphan chunk(s) for deleted files: "
                f"{', '.join(sorted(set(orphans))[:5])}",
                hint="run `agent-memory semindex` to drop them, or `semclean`",
            )
        )
    out.extend(_check_hash_collisions(manifest))

    # Check sidecar files for mismatched version/model
    from agent_memory.shared.config import EMBED_MODEL_FILE, INDEX_VERSION, VERSION_FILE
    from agent_memory.shared.ollama import DEFAULT_EMBED_MODEL

    stored_model = ""
    stored_version = ""
    with contextlib.suppress(OSError):
        stored_model = (idx / EMBED_MODEL_FILE).read_text(encoding="utf-8").strip()
    with contextlib.suppress(OSError):
        stored_version = (idx / VERSION_FILE).read_text(encoding="utf-8").strip()

    if stored_model and stored_model != DEFAULT_EMBED_MODEL:
        out.append(
            Finding(
                severity="warn",
                check="index-model",
                detail=(
                    f"mismatched embedding model: stored={stored_model} "
                    f"vs config={DEFAULT_EMBED_MODEL}"
                ),
                hint="run `agent-memory semindex --rebuild` to regenerate embeddings",
            )
        )
    if stored_version and stored_version != INDEX_VERSION:
        out.append(
            Finding(
                severity="warn",
                check="index-version",
                detail=(
                    f"mismatched index version: stored={stored_version} "
                    f"vs config={INDEX_VERSION}"
                ),
                hint="run `agent-memory semindex --rebuild` to regenerate index",
            )
        )

    if not ollama_is_alive():
        out.append(
            Finding(
                severity="info",
                check="index",
                detail="ollama daemon not reachable — semantic ops will degrade to keyword",
                hint=(
                    "start ollama (with `embeddinggemma` + "
                    "`cryptidbleh/gemma4-claude-opus-4.6:latest`) to re-enable"
                ),
            )
        )
    return out



def _check_hash_collisions(manifest: list[dict]) -> list[Finding]:
    """Same chunk sha256 mapping to different text (dedup-key collision risk).

    The dedup key is a truncated hash (16 hex), so a collision would make two
    distinct chunks share one vector. Vanishingly rare in practice; surfaced
    because the consequence (silent recall skew) is hard to otherwise detect."""
    by_hash: dict[str, set[str]] = defaultdict(set)
    for rec in manifest:
        h = rec.get("sha256")
        if h:
            by_hash[h].add(rec.get("text", "")[:120])
    collisions = [h for h, texts in by_hash.items() if len(texts) > 1]
    if not collisions:
        return []
    return [
        Finding(
            severity="error",
            check="index",
            detail=f"hash collision: sha256 {h} maps to {len(by_hash[h])} distinct texts",
            hint="rare truncation collision — rebuild with `semindex --rebuild`",
        )
        for h in collisions[:10]
    ]


def doctor(root: Path, *, json_out: bool = False) -> int:
    """Run :func:`run_doctor` and print findings (human report or JSON).

    Exit 0 when healthy or only info/warn, 1 when any error-severity finding is
    present (corrupt index, hash collision, missing bank). Hooks can rely on
    the exit code; humans get the prose either way."""
    findings = run_doctor(root)
    if json_out:
        print(json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=2))
    else:
        if not findings:
            print(f"✓ {bank_dir(root)} — no issues found")
        else:
            counts = Counter(f.severity for f in findings)
            summary = ", ".join(
                f"{counts[sev]} {sev}" for sev in ("error", "warn", "info") if counts[sev]
            )
            print(f"# Doctor report for {bank_dir(root)} — {summary}")
            for f in findings:
                print(f.as_line())
    return 1 if any(f.severity == "error" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(doctor(Path.cwd()))
