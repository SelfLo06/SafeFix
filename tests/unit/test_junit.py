from pathlib import Path

from safefix.junit import parse_junit_report


FIXTURES = Path(__file__).parents[1] / "fixtures" / "junit"


def _failures(path: Path):
    return {case.failure_id: case for case in parse_junit_report(path) if case.is_failure}


def test_failure_id_stable_between_baseline_and_later_report():
    baseline = _failures(FIXTURES / "baseline.xml")
    later = _failures(FIXTURES / "later.xml")

    assert set(baseline) == {"tests.test_math::test_addition"}
    assert set(later) == set(baseline)


def test_parameterized_instances_have_distinct_stable_ids():
    failures = _failures(FIXTURES / "parameterized.xml")

    assert set(failures) == {
        "tests.test_math::test_addition[one]",
        "tests.test_math::test_addition[two]",
    }


def test_failed_error_status_change_preserves_failure_id():
    failed = _failures(FIXTURES / "failed.xml")
    errored = _failures(FIXTURES / "error.xml")

    assert set(failed) == set(errored) == {"tests.test_math::test_addition"}
    assert failed["tests.test_math::test_addition"].status == "failed"
    assert errored["tests.test_math::test_addition"].status == "error"


def test_collection_error_gets_deterministic_synthetic_id():
    first = parse_junit_report(FIXTURES / "collection_error.xml")
    second = parse_junit_report(FIXTURES / "collection_error.xml")

    failures = [case for case in first if case.is_failure]
    assert len(failures) == 1
    assert failures[0].failure_id == (
        "collection_error::tests.test_collect::"
        "bd317cf1598e481a"
    )
    assert [case.failure_id for case in first] == [case.failure_id for case in second]


def test_collection_error_is_distinct_from_collected_testcase():
    collection_error = parse_junit_report(FIXTURES / "collection_error.xml")[0]
    collected = parse_junit_report(FIXTURES / "baseline.xml")[0]

    assert collection_error.is_collection_error is True
    assert collected.is_collection_error is False
