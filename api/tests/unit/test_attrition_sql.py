"""Issue #8: the counting statement is parameterized like the rest of the compiler (no values, no person rows)."""
import json
import pathlib

from sqlalchemy import Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import visitors

from app.cohort_engine.attrition import count_statement
from app.cohort_engine.compiler import build_plan
from app.cohort_engine.models import parse_definition

REFERENCE = json.loads((pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "reference_cohort.json")
                       .read_text(encoding="utf-8"))
SQL = str(count_statement(build_plan(parse_definition(REFERENCE))).compile(dialect=postgresql.dialect()))


def test_the_counting_statement_has_no_ids_dates_ages_or_labels_in_its_text():
    for value in ("1308216", "21601784", "320128", "316139", "365", "36500", "50", "No prior heart failure"):
        assert value not in SQL, value


def test_the_counting_statement_counts_distinct_persons_and_returns_no_person_columns():
    assert SQL.upper().count("COUNT(DISTINCT") == 6  # one per step
    assert "SELECT step_counts.position, step_counts.persons" in SQL  # the only columns the statement returns
    assert "step_counts.person_id" not in SQL


def test_the_counting_statement_reads_only_cdm_tables():
    statement = count_statement(build_plan(parse_definition(REFERENCE)))
    tables = {t for t in visitors.iterate(statement) if isinstance(t, Table)}
    assert tables and {t.schema for t in tables} == {"cdm"}, tables
