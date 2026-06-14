"""Verification: connect as the freshly created read-only user and prove the scope
matches the selection — it can read exactly the chosen relations, it cannot write, and
row-level security is active on owned tables.
"""
from __future__ import annotations

from dataclasses import dataclass

from .engines.base import ScopingPlan


@dataclass
class CheckResult:
    name: str
    passed: bool | None   # None => inconclusive / skipped
    detail: str


def verify(engine, plan: ScopingPlan) -> list[CheckResult]:
    results: list[CheckResult] = []

    # 1. Visible relations exactly match the selection.
    selected = sorted(t.table for t in plan.tables)
    visible = sorted(engine.list_visible_relations())
    missing = [r for r in selected if r not in visible]
    unexpected = [r for r in visible if r not in selected]
    ok = not missing and not unexpected
    results.append(CheckResult(
        "scope: visible == selected",
        ok,
        "visible relations match the selection" if ok
        else f"missing={missing} unexpected={unexpected}",
    ))

    # 2. No write privileges on any selected relation.
    writable: list[str] = []
    for t in plan.tables:
        try:
            if not engine.write_blocked(t.table):
                writable.append(t.table)
        except Exception as e:  # noqa: BLE001 - report, don't crash verification
            writable.append(f"{t.table} (error: {e})")
    results.append(CheckResult(
        "writes blocked on all selected",
        not writable,
        "no write privileges" if not writable else f"writable: {writable}",
    ))

    # 3. RLS active on owned tables: with no session identity, owned tables return 0 rows.
    owned = [t for t in plan.tables if t.owner_column]
    if not owned:
        results.append(CheckResult("RLS active on owned tables", None, "no owned tables in plan"))
        return results

    leaks: list[str] = []
    skipped: list[str] = []
    for t in owned:
        try:
            n = engine.rows_without_identity(t.table)
            if n != 0:
                leaks.append(f"{t.table} ({n} rows)")
        except Exception:  # noqa: BLE001 - identity function may not exist outside Supabase
            skipped.append(t.table)
    if leaks:
        results.append(CheckResult("RLS active on owned tables", False, f"rows visible without identity: {leaks}"))
    elif skipped:
        results.append(CheckResult(
            "RLS active on owned tables", None,
            f"could not evaluate for {skipped} (identity function unavailable) - verify manually",
        ))
    else:
        results.append(CheckResult("RLS active on owned tables", True, "no rows visible without a session identity"))
    return results
