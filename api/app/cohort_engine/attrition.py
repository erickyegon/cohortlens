"""Attrition counts: the number of distinct persons after every step, from ONE statement (docs/cohort-engine.md).

`count_statement(plan)` unions one `COUNT(DISTINCT person_id)` per step over the plan's shared CTE chain, so each
step is computed once. `run_attrition` executes it under a statement timeout and returns aggregates only: a step
key, its display label and a number. No person-level row ever leaves this module.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, Integer, Select, bindparam, distinct, func, select, union_all
from sqlalchemy.exc import OperationalError

from app.cohort_engine.compiler import CohortPlan
from app.db.session import STATEMENT_TIMEOUT_MS


class CohortTimeout(Exception):
    """The cohort query ran longer than the statement timeout and was cancelled by the database."""


@dataclass(frozen=True)
class StepCount:
    key: str
    label: str
    persons: int


def count_statement(plan: CohortPlan) -> Select:
    """One statement: `(position, persons)` per step, in step order. Position is a bound parameter, not text."""
    per_step = [
        select(bindparam(f"position_{n}", n, type_=Integer).label("position"),
               func.count(distinct(step.cte.c.person_id)).label("persons"))
        for n, step in enumerate(plan.steps)
    ]
    counts = union_all(*per_step).subquery("step_counts")
    return select(counts.c.position, counts.c.persons).order_by(counts.c.position)


def run_statement(conn: Connection, statement: Select, timeout_ms: int = STATEMENT_TIMEOUT_MS):
    """Execute under a transaction-local statement timeout; a cancelled query raises `CohortTimeout`.

    The caller owns the transaction; after a timeout it must roll back before reusing the connection (done here).
    """
    try:
        conn.execute(select(func.set_config("statement_timeout", str(int(timeout_ms)), True)))
        return conn.execute(statement).fetchall()
    except OperationalError as error:
        if type(error.orig).__name__ == "QueryCanceled":
            conn.rollback()
            raise CohortTimeout(f"cohort query exceeded {timeout_ms} ms") from None
        raise


def run_attrition(conn: Connection, plan: CohortPlan, timeout_ms: int = STATEMENT_TIMEOUT_MS) -> list[StepCount]:
    rows = run_statement(conn, count_statement(plan), timeout_ms)
    persons = {position: count for position, count in rows}
    return [StepCount(step.key, step.label, persons[n]) for n, step in enumerate(plan.steps)]
