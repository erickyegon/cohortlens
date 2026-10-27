"""Issue #8: the counting statement is parameterized like the rest of the compiler (no values, no person rows)."""
import json
import pathlib

from sqlalchemy.dialects import postgresql

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
    top_level = SQL.upper().rsplit(")\nSELECT", 1)[-1]
    assert "PERSON_ID," not in top_level.split("FROM")[0]  # the final select list holds counts, not people


def test_the_counting_statement_reads_only_cdm_tables():
    import re
    tables = set(re.findall(r"(?:FROM|JOIN)\s+([a-z_]+\.[a-z_]+)", SQL))
    assert tables and all(t.startswith("cdm.") for t in tables), tables
