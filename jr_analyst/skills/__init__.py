"""The Junior Analyst skills — the charter computation skills, one module each.

A skill is a single framework operation built on the agnostic core (ports +
model + config). Each is vertical-agnostic and adapter-free: it drives ports,
never a concrete system, and — per the read-only jr-analyst boundary — proposes
graded output rather than writing anything canonical.

The v1 build lands here one skill at a time (starting with `ingest_and_align`);
this scaffold intentionally exports nothing yet.
"""

__all__: list[str] = []
