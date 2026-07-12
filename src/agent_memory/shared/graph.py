"""Pure parsing and validation for decision-graph JSONL records."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

_REQUIRED_TEXT_FIELDS = ("id", "s", "p", "o")
_OPTIONAL_TEXT_FIELDS = ("t", "src")
_TEXT_LIST_FIELDS = ("aliases", "supersedes")


@dataclass(frozen=True)
class GraphIssue:
    """One bounded parse issue; raw graph content is deliberately excluded."""

    line_number: int
    kind: Literal["json", "schema"]
    action: Literal["skipped", "normalized"]
    reason: str


def parse_graph_lines(lines: Iterable[str]) -> tuple[list[dict], list[GraphIssue]]:
    """Return usable graph rows plus safe diagnostics for malformed input.

    Invalid core records are skipped. Invalid optional metadata is normalized so
    the underlying fact and its identifier remain usable. Unknown keys survive,
    which keeps supersede rewrites forward-compatible with future metadata.
    """
    rows: list[dict] = []
    issues: list[GraphIssue] = []
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError:
            issues.append(GraphIssue(line_number, "json", "skipped", "malformed JSON"))
            continue
        if not isinstance(decoded, dict):
            issues.append(GraphIssue(line_number, "schema", "skipped", "record is not an object"))
            continue

        missing = next(
            (
                field
                for field in _REQUIRED_TEXT_FIELDS
                if not isinstance(decoded.get(field), str) or not decoded[field].strip()
            ),
            None,
        )
        if missing:
            issues.append(
                GraphIssue(
                    line_number,
                    "schema",
                    "skipped",
                    f"required field '{missing}' must be a non-empty string",
                )
            )
            continue

        row = dict(decoded)
        for field in _OPTIONAL_TEXT_FIELDS:
            if field in row and not isinstance(row[field], str):
                row.pop(field)
                issues.append(
                    GraphIssue(
                        line_number,
                        "schema",
                        "normalized",
                        f"optional field '{field}' must be a string",
                    )
                )
        for field in _TEXT_LIST_FIELDS:
            if field not in row:
                row[field] = []
                continue
            value = row[field]
            if not isinstance(value, list):
                row[field] = []
                issues.append(
                    GraphIssue(
                        line_number,
                        "schema",
                        "normalized",
                        f"optional field '{field}' must be a list of strings",
                    )
                )
                continue
            clean = [item for item in value if isinstance(item, str) and item.strip()]
            if len(clean) != len(value):
                issues.append(
                    GraphIssue(
                        line_number,
                        "schema",
                        "normalized",
                        f"optional field '{field}' contained non-string or empty values",
                    )
                )
            row[field] = clean
        rows.append(row)
    return rows, issues
