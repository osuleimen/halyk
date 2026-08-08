"""
Evaluator — score submission against ground truth.
"""
import json
import os
import logging

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from config import GROUND_TRUTH_PATH

logger = logging.getLogger(__name__)


def load_ground_truth() -> dict:
    if not os.path.exists(GROUND_TRUTH_PATH):
        return {}
    with open(GROUND_TRUTH_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def score_cell(pred: dict, truth: dict) -> dict:
    """Score a single covenant cell. Returns breakdown."""
    result = {"status_score": 0.0, "actual_score": 0.0, "evidence_score": 0.0, "total": 0.0, "details": ""}

    t_status = truth.get("status")
    t_actual = truth.get("actual")
    t_evidence = truth.get("evidence_txn_id")

    p_status = pred.get("status")
    p_actual = pred.get("actual")
    p_evidence = pred.get("evidence_txn_id")

    # Status: 0.50 — must match exactly
    if p_status == t_status:
        result["status_score"] = 0.50
    else:
        result["details"] = f"Wrong status: {p_status} vs {t_status}. Entire cell = 0."
        return result  # Wrong status → entire cell is 0

    # Actual: 0.30 — gradient scale
    if p_actual is not None and t_actual is not None and t_actual != 0:
        try:
            p_val = float(p_actual)
            t_val = float(t_actual)
            relative_error = abs(p_val - t_val) / abs(t_val)
            result["actual_score"] = round(0.30 * max(0, 1 - relative_error / 0.05), 4)
            result["details"] += f"actual error: {relative_error:.4%}. "
        except (ValueError, TypeError):
            result["actual_score"] = 0.0
            result["details"] += "actual is not a valid number. "
    else:
        result["details"] += "actual missing or zero. "

    # Evidence: 0.20
    if t_evidence is not None:
        # Specific txn expected
        if p_evidence == t_evidence:
            result["evidence_score"] = 0.20
        else:
            result["details"] += f"Wrong evidence: {p_evidence} vs {t_evidence}. "
    else:
        # null in key → score follows actual score scale
        # These 0.20 scale with actual accuracy
        if t_actual is not None and t_actual != 0 and p_actual is not None:
            try:
                p_val = float(p_actual)
                t_val = float(t_actual)
                relative_error = abs(p_val - t_val) / abs(t_val)
                result["evidence_score"] = round(0.20 * max(0, 1 - relative_error / 0.05), 4)
            except (ValueError, TypeError):
                result["evidence_score"] = 0.0

    result["total"] = round(result["status_score"] + result["actual_score"] + result["evidence_score"], 4)
    return result


def evaluate(answers: dict) -> dict:
    """Evaluate all answers against ground truth. Returns detailed report."""
    gt = load_ground_truth()
    if not gt:
        return {"error": "No ground truth available"}

    scenarios = gt.get("scenarios", {})
    report = {"cells": {}, "total_score": 0.0, "max_score": 0.0, "cells_scored": 0}

    for scenario_id, sdata in scenarios.items():
        for cov_id, truth in sdata.get("covenants", {}).items():
            cell_key = f"{scenario_id}.{cov_id}"
            pred = answers.get(scenario_id, {}).get(cov_id, {})

            if not pred:
                report["cells"][cell_key] = {"total": 0.0, "details": "Cell missing"}
                report["max_score"] += 1.0
                continue

            cell_score = score_cell(pred, truth)
            report["cells"][cell_key] = cell_score
            report["total_score"] += cell_score["total"]
            report["max_score"] += 1.0
            report["cells_scored"] += 1

    report["percentage"] = round(report["total_score"] / report["max_score"] * 100, 2) if report["max_score"] else 0
    return report
