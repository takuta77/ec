"""DO NOT MERGE — used to validate that the test-unit required check fires."""


def test_deliberate_failure_for_pipeline_validation() -> None:
    assert False, "deliberate failure to validate CI test-unit gate"
