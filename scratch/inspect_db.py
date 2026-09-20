import sqlite3
import json

conn = sqlite3.connect('market_knowledge.db')
c = conn.cursor()
tables = [t[0] for t in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("Tables in market_knowledge.db:", tables)
for t in tables:
    count = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    print(f"Table {t}: {count} rows")
    if count > 0:
        c.execute(f"SELECT * FROM {t} LIMIT 3")
        rows = c.fetchall()
        print(f"Sample from {t}:", rows)
