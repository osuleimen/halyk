import json
from pathlib import Path

def test_honest_submission_exists():
    p = Path("submission.json")
    assert p.exists(), "submission.json missing"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data.get("model") == "muse-spark-1.2-contributor"
    assert "answers" in data
    assert len(data["answers"]) == 12  # 12 scenarios

def test_no_string_null():
    data = json.loads(Path("submission.json").read_text(encoding="utf-8"))
    for covs in data["answers"].values():
        for cell in covs.values():
            assert cell.get("evidence_txn_id") != "null", "string null found, should be None"

def test_evaluator_weighted():
    from app.services.evaluator import evaluate
    data = json.loads(Path("submission.json").read_text(encoding="utf-8"))
    res = evaluate(data["answers"])
    assert res["max_score"] == 36
    assert res["percentage"] >= 75  # honest threshold

def test_evaluator_strict():
    import subprocess, sys, json
    # score.py strict
    out = subprocess.check_output([sys.executable, "score.py"], text=True)
    # at least P1 should be 3/3
    assert "P1: 3/3" in out
