import sqlite3

con = sqlite3.connect(r"C:\Users\siddharthsha\Videos\GitHub\Repo-extra-1\finex.db")
cur = con.cursor()
cur.execute("SELECT id, status, created_at FROM extraction_runs WHERE document_id = ? ORDER BY created_at DESC LIMIT 3",
            ("3fe64808-3be0-4797-849d-3d0efb91f4db",))
rows = cur.fetchall()
for row in rows:
    print(row)
latest_id = rows[0][0]
cur.execute("SELECT logs FROM extraction_runs WHERE id = ?", (latest_id,))
logs = cur.fetchone()[0] or ""
print("---- lines mentioning note 47 or note_tables ----")
for line in logs.splitlines():
    if "note" in line.lower() and ("47" in line or "note_tables" in line or "notes=" in line):
        print(line)
