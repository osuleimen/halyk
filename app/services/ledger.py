"""
Ledger Service — pandas-based querying of master_ledger_2025.csv.
"""
import os
import re
import pandas as pd
import logging

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from config import LEDGER_PATH, ACCOUNT_TO_SCENARIO

logger = logging.getLogger(__name__)

_df: pd.DataFrame | None = None


def _get_df() -> pd.DataFrame:
    """Load and prepare the ledger (lazy singleton)."""
    global _df
    if _df is not None:
        return _df

    df = pd.read_csv(LEDGER_PATH)
    df["date"] = pd.to_datetime(df["date"])
    # Extract scenario_id from txn_id  (TXN-P1-0039 → P1)
    df["scenario_id"] = df["txn_id"].apply(lambda x: "-".join(x.split("-")[1:-1]))
    # Absolute amount for convenience
    df["abs_amount"] = df["amount"].abs()
    _df = df
    logger.info("Ledger loaded: %d transactions", len(df))
    return df


def query(
    scenario_id: str | None = None,
    description_contains: str | None = None,
    counterparty_contains: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    currency: str | None = None,
    limit: int = 200,
) -> pd.DataFrame:
    """Flexible ledger query. Returns filtered DataFrame."""
    df = _get_df().copy()

    if scenario_id:
        df = df[df["scenario_id"] == scenario_id]
    if description_contains:
        df = df[df["description"].str.contains(description_contains, case=False, na=False)]
    if counterparty_contains:
        df = df[df["counterparty"].str.contains(counterparty_contains, case=False, na=False)]
    if date_from:
        df = df[df["date"] >= pd.to_datetime(date_from)]
    if date_to:
        df = df[df["date"] <= pd.to_datetime(date_to)]
    if min_amount is not None:
        df = df[df["abs_amount"] >= min_amount]
    if max_amount is not None:
        df = df[df["abs_amount"] <= max_amount]
    if currency:
        df = df[df["currency"] == currency.upper()]

    return df.head(limit)


def get_scenario_summary(scenario_id: str) -> dict:
    """Get a summary of all transactions for a scenario."""
    df = query(scenario_id=scenario_id, limit=10000)
    if df.empty:
        return {"error": f"No transactions found for {scenario_id}"}

    expenses = df[df["amount"] < 0]
    income = df[df["amount"] > 0]

    return {
        "scenario_id": scenario_id,
        "account_id": df["account_id"].iloc[0],
        "total_transactions": len(df),
        "total_expenses": round(float(expenses["amount"].sum()), 2),
        "total_income": round(float(income["amount"].sum()), 2),
        "net": round(float(df["amount"].sum()), 2),
        "date_range": f"{df['date'].min().date()} to {df['date'].max().date()}",
        "currencies": df["currency"].unique().tolist(),
        "top_expense_categories": _top_categories(expenses, 10),
        "top_counterparties": _top_counterparties(expenses, 10),
    }


def _top_categories(df: pd.DataFrame, n: int) -> list[dict]:
    """Group expenses by description keywords."""
    if df.empty:
        return []
    records = []
    for _, row in df.iterrows():
        records.append({
            "txn_id": row["txn_id"],
            "description": row["description"],
            "amount": round(float(row["abs_amount"]), 2),
            "counterparty": row["counterparty"],
        })
    records.sort(key=lambda x: x["amount"], reverse=True)
    return records[:n]


def _top_counterparties(df: pd.DataFrame, n: int) -> list[dict]:
    """Group by counterparty and sum."""
    if df.empty:
        return []
    grouped = df.groupby("counterparty")["amount"].sum().abs().sort_values(ascending=False)
    return [{"counterparty": cp, "total": round(float(amt), 2)} for cp, amt in grouped.head(n).items()]


def find_related_party_transactions(
    scenario_id: str,
    related_parties: list[str],
) -> pd.DataFrame:
    """Find transactions involving known related parties."""
    df = query(scenario_id=scenario_id, limit=10000)
    if df.empty or not related_parties:
        return pd.DataFrame()

    pattern = "|".join(re.escape(p) for p in related_parties)
    mask = df["counterparty"].str.contains(pattern, case=False, na=False)
    return df[mask]


def format_transactions_for_llm(df: pd.DataFrame, max_rows: int = 50) -> str:
    """Format a DataFrame as a readable string for the LLM."""
    if df.empty:
        return "Транзакции не найдены."

    df_show = df.head(max_rows)
    lines = [f"Всего: {len(df)} транзакций (показаны {len(df_show)})"]
    lines.append(f"{'txn_id':<20} {'date':>12} {'amount':>16} {'currency':>4}  {'counterparty':<40} description")
    lines.append("-" * 140)

    for _, row in df_show.iterrows():
        lines.append(
            f"{row['txn_id']:<20} {str(row['date'].date()):>12} "
            f"{row['amount']:>16,.2f} {row['currency']:>4}  "
            f"{str(row['counterparty'])[:40]:<40} {str(row['description'])[:60]}"
        )

    if len(df) > max_rows:
        lines.append(f"\n... и ещё {len(df) - max_rows} транзакций")
        lines.append(f"Общая сумма (все): {df['amount'].sum():,.2f}")

    return "\n".join(lines)
