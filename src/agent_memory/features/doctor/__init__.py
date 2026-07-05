"""``agent-memory doctor`` — proactive memory-bank health check.

One command that surfaces problems a maintainer would otherwise discover
piecemeal: over-budget files, broken topic references, dead-PID active
entries, a corrupt or stale semantic index, and chunk-hash collisions. Read-
only; reports findings, never mutates the bank.
"""

from __future__ import annotations

from agent_memory.features.doctor.command import Finding, doctor, run_doctor

__all__ = ["Finding", "doctor", "run_doctor"]
