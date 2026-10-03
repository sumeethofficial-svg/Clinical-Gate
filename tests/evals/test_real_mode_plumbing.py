"""The real-provider code path (non-scripted model, attack cases judged on leaks only) must not crash."""
from evals.runner import Runner, load_cases


def test_real_mode_runs_with_any_llm_client_and_still_detects_leaks():
    ids = {"di-01", "ii-01", "re-01", "legit-fd-01", "legit-mgr-01"}
    cases = [c for c in load_cases("llm") if c["id"] in ids]
    r = Runner(mode="real", provider="heuristic")       # heuristic stands in for a provider here
    try:
        results = r.run(cases)
    finally:
        r.admin.close()
    assert len(results) == 5 and not any(x.error for x in results), [(x.id, x.error) for x in results]
    assert not any(x.leaks for x in results)
