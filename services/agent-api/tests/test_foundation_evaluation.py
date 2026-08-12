from pathlib import Path

from agent_api.eval.runner import load_suite, run_suite

SUITE = Path(__file__).parents[1] / "seed" / "util" / "foundation_eval.json"


def test_foundation_util_suite() -> None:
    suite = load_suite(SUITE)
    assert suite["name"] == "foundation-util-v1"
    failures = run_suite(suite)
    assert failures == [], failures
