"""Issue #10: the baseline statements are parameterized, read only cdm tables and return counts, not people."""
import json
import pathlib

from sqlalchemy import Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import visitors

from app.cohort_engine.baseline import AgeSexCell, Baseline, ComorbidityCount, baseline_statements, suppress_baseline
from app.cohort_engine.compiler import build_plan
from app.cohort_engine.models import parse_definition

REFERENCE = json.loads((pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "reference_cohort.json")
                       .read_text(encoding="utf-8"))
STATEMENTS = baseline_statements(build_plan(parse_definition(REFERENCE)))
SQL = [str(s.compile(dialect=postgresql.dialect())) for s in STATEMENTS]


def test_there_are_two_statements_one_for_age_and_sex_and_one_for_comorbidities():
    assert len(STATEMENTS) == 2


def test_no_ids_dates_ages_labels_or_limits_appear_in_the_statement_text():
    for sql in SQL:
        for value in ("1308216", "21601784", "320128", "316139", "365", "36500", "60-69", "50-59", "Essential",
                      "No prior heart failure"):
            assert value not in sql, value


def test_the_statements_read_only_cdm_tables():
    for statement in STATEMENTS:
        tables = {t for t in visitors.iterate(statement) if isinstance(t, Table)}
        assert tables and {t.schema for t in tables} == {"cdm"}, tables


def test_the_statements_return_only_aggregates_never_person_ids():
    for statement in STATEMENTS:
        names = [c.name for c in statement.selected_columns]
        assert "person_id" not in names, names
    assert [c.name for c in STATEMENTS[0].selected_columns] == ["age_band", "sex", "persons"]
    assert [c.name for c in STATEMENTS[1].selected_columns] == ["concept_id", "concept_name", "persons"]


def test_suppressing_an_empty_baseline_gives_an_empty_public_baseline():
    public = suppress_baseline(Baseline(age_sex=(), comorbidities=()))
    assert public.age_sex == () and public.comorbidities == ()


def test_suppress_baseline_masks_cells_and_omits_rare_comorbidities_keeping_order_and_names():
    public = suppress_baseline(Baseline(
        age_sex=(AgeSexCell("60-69", "F", 5), AgeSexCell("60-69", "M", 0), AgeSexCell("70-79", "F", 40)),
        comorbidities=(ComorbidityCount(320128, "Essential hypertension", 40), ComorbidityCount(5, "Rare", 10),
                       ComorbidityCount(1, None, 11), ComorbidityCount(2, "Rarer", 9))))
    assert [(c.age_band, c.sex, c.persons) for c in public.age_sex] == [
        ("60-69", "F", "<11"), ("60-69", "M", 0), ("70-79", "F", 40)]
    assert [(c.concept_id, c.name, c.persons) for c in public.comorbidities] == [
        (320128, "Essential hypertension", 40), (1, None, 11)]  # 10 and 9 are left out, not masked
    assert public.comorbidity_note == "Conditions with fewer than 11 patients are not shown."
