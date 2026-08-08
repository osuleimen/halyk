import sqlite3
from config import LEDGER_DB
print(LEDGER_DB)
c = sqlite3.connect(LEDGER_DB)
print(c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
print(c.execute("SELECT * FROM ledger WHERE txn_id='TXN-P4-0014'").fetchall())
