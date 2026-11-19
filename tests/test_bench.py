from docdrift.bench import run_bench


def test_benchmark_has_no_failures_and_no_false_alarms():
    res = run_bench()
    bad = [r["name"] for r in res["rows"] if r["kind"] != "limit" and not r["ok"]]
    assert bad == []
    assert res["false_alarms"] == 0
