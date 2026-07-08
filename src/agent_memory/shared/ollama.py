"""Self-contained local-Ollama HTTP client.

Standalone replacement for the ecosystem ``ollama_client``: same public API
(``is_alive``/``embed``/``generate``/``DEFAULT_EMBED_MODEL``) so semantic
indices stay compatible, plus the same on-disk cache directory
(``~/.claude/state/ollama-cache``) so cache entries are shared with the host
ecosystem when agent-memory runs alongside it.

Degrades gracefully: every public function returns ``None``/``False`` when the
daemon is unreachable — callers fall back to deterministic behavior. No hard
failures, because memory ops must never block on an optional LLM.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_URL = "http://localhost:11434"
# MUST match the ecosystem ollama_client defaults so existing indices stay
# valid and the cache is shared.
DEFAULT_GEN_MODEL = "qwen3.5:4b"
DEFAULT_EMBED_MODEL = "embeddinggemma"  # 768-dim — eval winner 2026-06-28
DEFAULT_TIMEOUT = 120
EMBED_TIMEOUT = 60
CACHE_MAX_TEMP = 0.3
CACHE_MAX_ENTRIES = 2000
# Pruning sorts every cache file by mtime; amortize across writes so a hot
# cache (maintain/audit prompts) doesn't pay the full sort on every store.
CACHE_PRUNE_EVERY = 50
# Mutable single-arg holder so the writer avoids a ``global`` statement.
_store_state: dict[str, int] = {"count": 0}
OLLAMA_CACHE_DIR = Path.home() / ".claude" / "state" / "ollama-cache"

_THINK_TAGS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<think\b[^>]*>.*?(</think\s*>|$)", re.S | re.I),
    re.compile(r"<reasoning\b[^>]*>.*?(</reasoning\s*>|$)", re.S | re.I),
    re.compile(r"<reflection\b[^>]*>.*?(</reflection\s*>|$)", re.S | re.I),
    re.compile(r"<\|think\|>.*?<\|/think\|>", re.S | re.I),
    re.compile(r"<\|channel>.*?<channel\|>", re.S | re.I),
    re.compile(r"<\|channel\|>.*?(<\|channel\|>|$)", re.S | re.I),
)
_OUTPUT_RE = re.compile(r"<output\b[^>]*>(.*?)</output\s*>", re.S | re.I)
_VISIBLE_REASONING_RE = re.compile(
    r"^\s*(thinking process|let me think)[: ].*?(final answer|answer|output)\s*:\s*",
    re.S | re.I,
)


class OllamaUnavailable(RuntimeError):
    """Daemon unreachable (connection refused / DNS / timeout-abort)."""


class OllamaRequestError(OllamaUnavailable):
    """Daemon was reachable but a single request failed (HTTP 4xx/5xx)."""

    def __init__(self, status: int, body: str = "") -> None:
        super().__init__(f"HTTP {status}: {body[:200]}" if body else f"HTTP {status}")
        self.status = status
        self.body = body


def _base_url() -> str:
    """Resolve the daemon URL from env (override) or the default."""
    return os.environ.get("AGENT_MEMORY_OLLAMA_URL", DEFAULT_URL).rstrip("/")


def _gen_timeout() -> float:
    """Generation timeout (default 120s); override via env for slow models."""
    try:
        return float(os.environ.get("AGENT_MEMORY_OLLAMA_TIMEOUT", DEFAULT_TIMEOUT))
    except ValueError:
        return DEFAULT_TIMEOUT


def is_alive(timeout: float = 2.0) -> bool:
    """True iff the daemon answers ``/api/tags`` quickly."""
    try:
        req = urllib.request.Request(f"{_base_url()}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return False


def _normalize(url: str) -> str:
    for suffix in ("/api/generate", "/api/chat", "/api/embeddings"):
        if url.endswith(suffix):
            return url[: -len(suffix)]
    return url


def _post(path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = f"{_normalize(_base_url())}{path}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except OSError:
            body = ""
        raise OllamaRequestError(exc.code, body) from exc
    except TimeoutError as exc:
        raise OllamaRequestError(408, f"timeout: {exc}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise OllamaUnavailable(str(exc)) from exc


def _strip_think(text: str) -> str:
    if not text:
        return ""
    for pattern in _THINK_TAGS:
        text = pattern.sub("", text)
    text = re.sub(r"^.*?</(?:think|reasoning|reflection)\s*>", "", text, flags=re.S | re.I)
    text = _OUTPUT_RE.sub(r"\1", text)
    visible = _VISIBLE_REASONING_RE.search(text)
    if visible:
        text = text[visible.end():]
    else:
        low = text.lstrip().lower()
        if low.startswith(("thinking process:", "let me think:")):
            parts = re.split(r"\n\s*\n", text.strip(), maxsplit=1)
            text = parts[1] if len(parts) == 2 else ""
    return text.strip()


def _cache_key(model: str, prompt: str, temperature: float, num_ctx: int | None) -> str:
    raw = f"{model}\x1f{temperature}\x1f{num_ctx}\x1f{prompt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_path(model: str, prompt: str, temperature: float, num_ctx: int | None) -> Path:
    return OLLAMA_CACHE_DIR / f"{_cache_key(model, prompt, temperature, num_ctx)}.txt"


def _load_cache(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _store_cache(path: Path, content: str) -> None:
    with contextlib.suppress(OSError):
        path.write_text(content, encoding="utf-8")


def _prune_cache(max_entries: int = CACHE_MAX_ENTRIES) -> None:
    try:
        files = sorted(OLLAMA_CACHE_DIR.glob("*.txt"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    for path in files[: max(0, len(files) - max_entries)]:
        with contextlib.suppress(OSError):
            path.unlink()


def generate(
    prompt: str,
    *,
    model: str = DEFAULT_GEN_MODEL,
    temperature: float = 0.2,
    num_ctx: int | None = None,
) -> str | None:
    """One-shot completion, cached when near-deterministic. Returns None if the
    daemon is down — memory maintenance degrades silently, never hard-fails."""
    cacheable = temperature <= CACHE_MAX_TEMP
    if cacheable:
        OLLAMA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached = _load_cache(_cache_path(model, prompt, temperature, num_ctx))
        if cached is not None:
            return cached
    options: dict[str, Any] = {"temperature": temperature}
    if num_ctx is not None:
        options["num_ctx"] = num_ctx
    try:
        data = _post(
            "/api/generate",
            {"model": model, "prompt": prompt, "stream": False, "options": options},
            _gen_timeout(),
        )
    except OllamaUnavailable:
        return None
    out = _strip_think(str(data.get("response", "")).strip()) or None
    if out is not None and cacheable:
        _store_state["count"] += 1
        _store_cache(_cache_path(model, prompt, temperature, num_ctx), out)
        # Sort is O(N log N) over up to CACHE_MAX_ENTRIES files; only run it
        # every CACHE_PRUNE_EVERY stores so steady-state cache writes stay cheap.
        if _store_state["count"] % CACHE_PRUNE_EVERY == 0:
            _prune_cache()
    return out


def _embed_once(text: str, model: str, timeout: float) -> list[float] | None:
    if not text.strip():
        return None
    try:
        data = _post("/api/embeddings", {"model": model, "prompt": text}, timeout)
    except OllamaUnavailable:
        return None
    vec = data.get("embedding")
    return [float(x) for x in vec] if isinstance(vec, list) and vec else None


def embed(
    text: str,
    *,
    model: str = DEFAULT_EMBED_MODEL,
    timeout: float = EMBED_TIMEOUT,
) -> list[float] | None:
    """Embedding vector for one text. Halves + retries on failure so a long
    token-dense chunk still yields a (partial) vector rather than being
    dropped — better for recall than no embedding at all."""
    vec = _embed_once(text, model, timeout)
    if vec is not None:
        return vec
    if len(text) > 512:
        return _embed_once(text[: len(text) // 2], model, timeout)
    return None
