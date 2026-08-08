import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from app.services import ledger

df = ledger._get_df()

txns = ['TXN-P2-0009', 'TXN-P2-0012', 'TXN-P4-0000', 'TXN-P5-0004']
sdf = df[df['txn_id'].isin(txns)]
for _, row in sdf.iterrows():
    print(f"{row['txn_id']} | {row['amount']} | {row['abs_amount']} | {row['counterparty']} | {row['description']}")
