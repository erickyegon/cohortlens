"""Compile a validated cohort definition into SQL (docs/cohort-engine.md).

The result is a chain of CTEs built with SQLAlchemy Core, so every value is a bound parameter and no user text is
ever part of the SQL. Each step exposes `(person_id, index_date)`; later steps only remove rows.

This module takes a `CohortDefinition` (already validated by `models.parse_definition`) and never executes anything.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import CTE, Select, and_, case, extract, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.cohort_engine.models import CohortDefinition, Demographics, IndexEvent
from app.cohort_engine.schema import DOMAINS, GENDER_CONCEPT, concept_ancestor, person

# Assumed birthday when a person has no month or day of birth recorded (the OHDSI convention for missing parts).
DEFAULT_BIRTH_MONTH = 7
DEFAULT_BIRTH_DAY = 1


@dataclass(frozen=True)
class Step:
    key: str
    label: str
    cte: CTE


@dataclass(frozen=True)
class CohortPlan:
    """The ordered steps of an attrition waterfall; each step is a CTE of `(person_id, index_date)` rows."""

    steps: tuple[Step, ...]

    def step(self, key: str) -> Step:
        for step in self.steps:
            if step.key == key:
                return step
        raise KeyError(key)

    def rows(self, key: str) -> Select:
        """`SELECT person_id, index_date` for a step (the whole CTE chain up to it is included)."""
        cte = self.step(key).cte
        return select(cte.c.person_id, cte.c.index_date)


def concept_filter(column: ColumnElement, concept_ids: list[int], include_descendants: bool) -> ColumnElement:
    """`column IN (...)`, expanded through concept_ancestor when descendants are wanted."""
    if include_descendants:
        descendants = select(concept_ancestor.c.descendant_concept_id).where(
            concept_ancestor.c.ancestor_concept_id.in_(concept_ids))
        return column.in_(descendants)
    return column.in_(concept_ids)


def index_events(index: IndexEvent) -> CTE:
    """Index events of the requested domain, restricted to `date_range`, then (optionally) one per person.

    `date_range` is applied first, so 'first occurrence' means the first event inside the range.
    """
    table, concept_column, date_column = DOMAINS[index.domain]
    conditions = [concept_filter(concept_column, index.concept_ids, index.include_descendants)]
    if index.date_range is not None:
        if index.date_range.start is not None:
            conditions.append(date_column >= index.date_range.start)
        if index.date_range.end is not None:
            conditions.append(date_column <= index.date_range.end)
    events = select(table.c.person_id, date_column.label("index_date")).where(*conditions)
    if index.first_occurrence_only:
        events = events.with_only_columns(table.c.person_id, func.min(date_column).label("index_date")).group_by(
            table.c.person_id)
    else:
        events = events.distinct()
    return events.cte("index_events")


def age_at(index_date: ColumnElement) -> ColumnElement:
    """Whole years at `index_date`, adjusted for the birthday (a missing month/day is assumed to be July 1)."""
    month = func.coalesce(person.c.month_of_birth, DEFAULT_BIRTH_MONTH)
    day = func.coalesce(person.c.day_of_birth, DEFAULT_BIRTH_DAY)
    index_month, index_day = extract("month", index_date), extract("day", index_date)
    before_birthday = or_(index_month < month, and_(index_month == month, index_day < day))
    return extract("year", index_date) - person.c.year_of_birth - case((before_birthday, 1), else_=0)


def demographics(previous: CTE, demo: Demographics) -> CTE:
    """Keep index rows whose person matches the sex list and the age range *at that row's index date*."""
    age = age_at(previous.c.index_date)
    conditions: list[ColumnElement] = [person.c.gender_concept_id.in_([GENDER_CONCEPT[s] for s in demo.sex])]
    if demo.age_min is not None:
        conditions.append(age >= demo.age_min)
    if demo.age_max is not None:
        conditions.append(age <= demo.age_max)
    kept = (
        select(previous.c.person_id, previous.c.index_date)
        .select_from(previous.join(person, person.c.person_id == previous.c.person_id))
        .where(*conditions)
    )
    return kept.cte("demographics")


def build_plan(definition: CohortDefinition) -> CohortPlan:
    index = index_events(definition.index_event)
    kept = demographics(index, definition.demographics)
    return CohortPlan(steps=(
        Step("index", "First index event" if definition.index_event.first_occurrence_only else "Index events", index),
        Step("demographics", "Age and sex", kept),
    ))
