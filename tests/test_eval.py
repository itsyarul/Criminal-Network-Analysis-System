"""Eval gate tests — backend.eval.score runs offline on exports, honestly graded."""
import json
import subprocess
import sys

from backend.eval.score import run_all


def test_eval_report_shape():
    report = run_all()
    assert "error" not in report, report.get("error")
    assert report["overall"] in ("PASS", "PARTIAL", "FAIL")
    assert set(report["checks"]) == {
        "flag_hygiene", "communities", "bridges",
        "bursts", "structuring", "alias_recovery",
    }
    for name, check in report["checks"].items():
        assert check["status"] in ("PASS", "PARTIAL", "FAIL"), name
        assert check["detail"], name


def test_eval_event_days_from_ground_truth():
    report = run_all()
    assert report["event_days"] == [58, 61, 64]


def test_eval_hygiene_and_structuring_pass():
    report = run_all()
    assert report["checks"]["flag_hygiene"]["status"] == "PASS"
    assert report["checks"]["structuring"]["status"] == "PASS"


def test_eval_bridges_honest_partial():
    # Known data property (see README_TASK1 gap #1): X1/X2 rank outside top-6
    # on NetworkX betweenness. The gate must report PARTIAL, never fake PASS.
    report = run_all()
    status = report["checks"]["bridges"]["status"]
    assert status in ("PASS", "PARTIAL")
    if status == "PARTIAL":
        assert "missing" in report["checks"]["bridges"]["detail"]


def test_eval_cli_exit_codes():
    r = subprocess.run([sys.executable, "-m", "backend.eval.score"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr  # PARTIAL does not fail by default
    assert "Overall:" in r.stdout
    r = subprocess.run([sys.executable, "-m", "backend.eval.score", "--json"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    # NOTE: backend modules print Neo4j-fallback warnings to stdout, so the
    # JSON payload starts at the first '{' rather than offset 0.
    body = json.loads(r.stdout[r.stdout.index("{"):])
    assert body["overall"] in ("PASS", "PARTIAL", "FAIL")
