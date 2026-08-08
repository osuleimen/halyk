import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from app.services import ledger

df = ledger._get_df()

for scenario in ['P2', 'P4', 'P5', 'P6']:
    sdf = df[df['scenario_id'] == scenario]
    print(f"=== {scenario} ===")
    for _, row in sdf.iterrows():
        print(f"{row['txn_id']} | {row['amount']} | {row['abs_amount']} | {row['counterparty']} | {row['description']}")
    print("\n")
