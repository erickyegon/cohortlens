"""Issue #37 (criterion 2): a static cap on definitions that make every exposure an index event.

With `first_occurrence_only: false` every matching record is an index row (about 266k for a broad drug concept on the
demo seed) and each rule is probed once per row, so many rules or an occurrence count above 1 can run past the 10 s
statement timeout. Decision (product owner): when `first_occurrence_only` is false a cohort may have at most 3 rules
and every rule must have `min_occurrences` 1. The cap lives in `parse_definition`, the entry point the API uses;
errors are field-level, report everything at once and never echo submitted text.
"""
import copy
import json
import pathlib

import pytest

from app.cohort_engine.models import MAX_RULES_ALL_EVENTS, CohortDefinition, DefinitionError, parse_definition

REFERENCE = json.loads((pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "reference_cohort.json")
                       .read_text(encoding="utf-8"))


def definition(rule_count, first_occurrence_only=False, **rule_overrides):
    raw = copy.deepcopy(REFERENCE)
    raw["index_event"]["first_occurrence_only"] = first_occurrence_only
    raw["rules"] = [{"label": f"Rule {n}", "type": "exclude", "domain": "condition", "concept_ids": [316139],
                     "window": {"start_days": -365, "end_days": -1}, **rule_overrides} for n in range(rule_count)]
    return raw


def errors_of(raw):
    with pytest.raises(DefinitionError) as caught:
        parse_definition(raw)
    return {e["field"]: e["message"] for e in caught.value.errors}


def test_the_cap_is_three_rules():
    assert MAX_RULES_ALL_EVENTS == 3


# ---- allowed -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("rule_count", [0, 1, 2, 3])
def test_up_to_three_rules_with_one_occurrence_are_allowed_when_every_exposure_is_an_index_event(rule_count):
    assert len(parse_definition(definition(rule_count)).rules) == rule_count


def test_an_explicit_min_occurrences_of_one_is_allowed():
    assert parse_definition(definition(3, min_occurrences=1)).rules[2].min_occurrences == 1


def test_the_cap_does_not_apply_to_first_occurrence_only_cohorts():
    for rule_count, occurrences in [(10, 1), (10, 2), (5, 100)]:
        parse_definition(definition(rule_count, first_occurrence_only=True, min_occurrences=occurrences))


def test_first_occurrence_only_defaults_to_true_so_the_default_is_uncapped():
    raw = definition(10, min_occurrences=5)
    del raw["index_event"]["first_occurrence_only"]
    assert len(parse_definition(raw).rules) == 10


# ---- rejected, with a field-level error ----------------------------------------------------------------------------

def test_a_fourth_rule_is_rejected_on_the_rules_field():
    errors = errors_of(definition(4))
    assert set(errors) == {"rules"}
    assert "3" in errors["rules"] and "first_occurrence_only" in errors["rules"]


def test_more_than_the_ordinary_maximum_still_reports_the_ordinary_error_once():
    errors = errors_of(definition(11))
    assert list(errors) == ["rules"]  # one error for the field, not the ordinary one plus the cap


def test_a_rule_needing_two_occurrences_is_rejected_on_that_rules_min_occurrences():
    raw = definition(3)
    raw["rules"][1]["min_occurrences"] = 2
    errors = errors_of(raw)
    assert set(errors) == {"rules.1.min_occurrences"}
    assert "min_occurrences" in errors["rules.1.min_occurrences"] and "1" in errors["rules.1.min_occurrences"]


def test_every_problem_is_reported_in_one_round():
    raw = definition(5, min_occurrences=2)
    assert set(errors_of(raw)) == {"rules"} | {f"rules.{n}.min_occurrences" for n in range(5)}


def test_cap_errors_are_reported_together_with_other_definition_errors():
    raw = definition(4, min_occurrences=3)
    raw["demographics"] = {"age_min": 80, "age_max": 40}
    errors = errors_of(raw)
    assert "rules" in errors and "rules.0.min_occurrences" in errors
    assert any(field.startswith("demographics") for field in errors)


def test_an_out_of_range_occurrence_count_gets_a_single_error_for_that_field():
    errors = errors_of(definition(1, min_occurrences=500))
    assert list(errors) == ["rules.0.min_occurrences"]


def test_a_non_boolean_flag_is_left_to_the_strict_type_check():
    raw = definition(5, min_occurrences=2)
    raw["index_event"]["first_occurrence_only"] = "false"
    errors = errors_of(raw)
    assert "index_event.first_occurrence_only" in errors and "rules.0.min_occurrences" not in errors


def test_error_messages_never_echo_submitted_text():
    raw = definition(4, min_occurrences=2)
    raw["rules"][0]["label"] = "SECRET-LABEL-12345"
    raw["name"] = "SECRET-NAME-12345"
    assert "SECRET" not in json.dumps(errors_of(raw))


# ---- scope ---------------------------------------------------------------------------------------------------------

def test_the_cap_is_an_input_policy_not_a_limit_of_the_engine_model():
    # the compiler and its tests can still build uncapped definitions directly; parse_definition is the gate
    assert len(CohortDefinition.model_validate(definition(4, min_occurrences=2)).rules) == 4
