import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CODE_DIR.parent
STORAGE = PROJECT_ROOT / "storage"
DB = STORAGE / "database" / "job_search.db"
OUT = STORAGE / "exports"
OUT.mkdir(parents=True, exist_ok=True)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
now = datetime.now(timezone.utc)

def parse(v):
    return datetime.fromisoformat(v) if v else None

def duration(r):
    if r["ended_at"]:
        return float(r["duration_seconds"] or 0)
    s = parse(r["started_at"])
    return max(0.0, (now-s).total_seconds()) if s else 0.0

rows=[]
for m in con.execute("SELECT * FROM metros ORDER BY bucket_slug,position,id"):
    times={"software":0.0,"hardware":0.0}
    for s in con.execute("SELECT * FROM search_sessions WHERE metro_id=?",(m["id"],)):
        times[s["phase"]]+=duration(s)
    counts={"software":0,"hardware":0}
    easy={"software":0,"hardware":0}; hard={"software":0,"hardware":0}
    for a in con.execute("SELECT phase,apply_type,COUNT(*) n FROM applications WHERE metro_id=? GROUP BY phase,apply_type",(m["id"],)):
        counts[a["phase"]]+=a["n"]
        (easy if a["apply_type"]=="easy" else hard)[a["phase"]]+=a["n"]
    rows.append([
        m["cbsa"],m["name"],m["bucket_slug"],
        counts["software"],round(times["software"]/60,2),
        counts["hardware"],round(times["hardware"]/60,2),
        easy["software"],hard["software"],easy["hardware"],hard["hardware"]
    ])

path=OUT/f"metro_search_data_{datetime.now().strftime('%Y-%m-%d_%H%M')}.csv"
with path.open('w',newline='',encoding='utf-8-sig') as f:
    w=csv.writer(f)
    w.writerow(["Metro identifying number","Metro","Bucket","software applies","software time minutes","hardware applies","hardware time minutes","software easy","software hard","hardware easy","hardware hard"])
    w.writerows(rows)
print(f"Exported {len(rows)} metros to:\n{path}")
input("\nPress Enter to close...")
