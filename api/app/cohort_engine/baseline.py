"""Baseline table for the final cohort: counts by age band and sex, and the ten most common comorbidities (#10).

The cohort members are the persons of the plan's last step, each taken once at their earliest surviving index date.
Two aggregate statements run over that set; person-level rows exist only inside the SQL. `run_baseline` returns exact
counts for internal use; everything that leaves the API goes through `suppress_baseline`, which masks cells of 1 to
10 as "<11" (`suppression.suppress_count`).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import CTE, Connection, Select, and_, case, distinct, func, select

from app.cohort_engine.attrition import run_statement
from app.cohort_engine.compiler import CohortPlan, age_at
from app.cohort_engine.schema import GENDER_CONCEPT, concept, condition_occurrence, person
from app.cohort_engine.suppression import suppress_count
from app.db.session import STATEMENT_TIMEOUT_MS

# (label, youngest, oldest); the last band is open-ended
AGE_BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("0-17", 0, 17), ("18-29", 18, 29), ("30-39", 30, 39), ("40-49", 40, 49),
    ("50-59", 50, 59), ("60-69", 60, 69), ("70-79", 70, 79), ("80+", 80, None),
)
SEXES = ("F", "M", "other")  # "other" is any gender concept that is not female or male
TOP_COMORBIDITIES = 10


@dataclass(frozen=True)
class AgeSexCell:
    age_band: str
    sex: str
    persons: int


@dataclass(frozen=True)
class ComorbidityCount:
    concept_id: int
    name: str | None
    persons: int


@dataclass(frozen=True)
class Baseline:
    age_sex: tuple[AgeSexCell, ...]
    comorbidities: tuple[ComorbidityCount, ...]


@dataclass(frozen=True)
class PublicAgeSexCell:
    age_band: str
    sex: str
    persons: int | str


@dataclass(frozen=True)
class PublicComorbidity:
    concept_id: int
    name: str | None
    persons: int | str


@dataclass(frozen=True)
class PublicBaseline:
    age_sex: tuple[PublicAgeSexCell, ...]
    comorbidities: tuple[PublicComorbidity, ...]


def members(plan: CohortPlan) -> CTE:
    """The final cohort: one row per person, at their earliest index date that survived every step."""
    last = plan.steps[-1].cte
    return (select(last.c.person_id, func.min(last.c.index_date).label("index_date"))
            .group_by(last.c.person_id).cte("members"))


def age_band_case(age):
    branches = []
    for label, low, high in AGE_BANDS:
        in_band = age >= low if high is None else and_(age >= low, age <= high)
        branches.append((in_band, label))
    return case(*branches, else_=AGE_BANDS[-1][0])


def sex_case():
    return case((person.c.gender_concept_id == GENDER_CONCEPT["F"], "F"),
                (person.c.gender_concept_id == GENDER_CONCEPT["M"], "M"), else_="other")


def age_sex_statement(cohort: CTE) -> Select:
    """Classify each member in a subquery, then group; the person-level rows never leave the database."""
    classified = (
        select(age_band_case(age_at(cohort.c.index_date)).label("age_band"), sex_case().label("sex"))
        .select_from(cohort.join(person, person.c.person_id == cohort.c.person_id))
        .subquery("classified")
    )
    return (select(classified.c.age_band, classified.c.sex, func.count().label("persons"))
            .group_by(classified.c.age_band, classified.c.sex))


def comorbidity_statement(cohort: CTE) -> Select:
    """Conditions recorded on or before the index date, by number of distinct members, most common first."""
    on_or_before = and_(condition_occurrence.c.person_id == cohort.c.person_id,
                        condition_occurrence.c.condition_start_date <= cohort.c.index_date)
    persons = func.count(distinct(cohort.c.person_id)).label("persons")
    return (
        select(condition_occurrence.c.condition_concept_id.label("concept_id"),
               concept.c.concept_name.label("concept_name"), persons)
        .select_from(cohort.join(condition_occurrence, on_or_before)
                     .outerjoin(concept, concept.c.concept_id == condition_occurrence.c.condition_concept_id))
        .group_by(condition_occurrence.c.condition_concept_id, concept.c.concept_name)
        .order_by(persons.desc(), condition_occurrence.c.condition_concept_id)
        .limit(TOP_COMORBIDITIES)
    )


def baseline_statements(plan: CohortPlan) -> tuple[Select, Select]:
    cohort = members(plan)
    return age_sex_statement(cohort), comorbidity_statement(cohort)


def run_baseline(conn: Connection, plan: CohortPlan, timeout_ms: int = STATEMENT_TIMEOUT_MS) -> Baseline:
    age_sex_sql, comorbidity_sql = baseline_statements(plan)
    found = {(band, sex): n for band, sex, n in run_statement(conn, age_sex_sql, timeout_ms)}
    grid = tuple(AgeSexCell(band, sex, found.get((band, sex), 0)) for band, _, _ in AGE_BANDS for sex in SEXES)
    top = tuple(ComorbidityCount(concept_id, name, n)
                for concept_id, name, n in run_statement(conn, comorbidity_sql, timeout_ms))
    return Baseline(age_sex=grid, comorbidities=top)


def suppress_baseline(baseline: Baseline) -> PublicBaseline:
    return PublicBaseline(
        age_sex=tuple(PublicAgeSexCell(c.age_band, c.sex, suppress_count(c.persons)) for c in baseline.age_sex),
        comorbidities=tuple(PublicComorbidity(c.concept_id, c.name, suppress_count(c.persons))
                            for c in baseline.comorbidities),
    )
