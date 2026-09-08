from __future__ import annotations

from contextlib import asynccontextmanager

import csv
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

CODE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CODE_DIR.parent
STORAGE_DIR = PROJECT_ROOT / "storage"
DATABASE_DIR = STORAGE_DIR / "database"
DB_PATH = DATABASE_DIR / "job_search.db"
STATIC = CODE_DIR / "static"
DATA_DIR = STORAGE_DIR / "data"
EXPORT_DIR = STORAGE_DIR / "exports"
LOG_DIR = STORAGE_DIR / "logs"
SERVER_PID_FILE = DATABASE_DIR / "server.pid"

UVICORN_SERVER = None

DEFAULT_PRESET_MINUTES = 60
DEFAULT_WEBSITE_CONTROLS = 1


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).isoformat(timespec="milliseconds")


def parse_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    try:
        yield con
    finally:
        con.close()


def tx(con: sqlite3.Connection):
    con.execute("BEGIN IMMEDIATE")


def init_db() -> None:
    STORAGE_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS buckets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                position INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                source_file TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS metros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cbsa TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                bucket_slug TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                software_done INTEGER NOT NULL DEFAULT 0,
                hardware_done INTEGER NOT NULL DEFAULT 0,
                cycle INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_metros_bucket_pos ON metros(bucket_slug,active,position,id);

            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK(id=1),
                preset_minutes INTEGER NOT NULL,
                website_controls INTEGER NOT NULL DEFAULT 1,
                bucket_tracking_started_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bucket_settings (
                bucket_slug TEXT PRIMARY KEY,
                percentage REAL NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ledger (
                bucket_slug TEXT PRIMARY KEY,
                balance_seconds REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS allocation_blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                preset_seconds REAL NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('active','closed')),
                close_reason TEXT
            );

            CREATE TABLE IF NOT EXISTS allocation_budgets (
                block_id INTEGER NOT NULL REFERENCES allocation_blocks(id) ON DELETE CASCADE,
                bucket_slug TEXT NOT NULL,
                percentage REAL NOT NULL,
                base_seconds REAL NOT NULL,
                adjusted_seconds REAL NOT NULL,
                PRIMARY KEY(block_id,bucket_slug)
            );

            CREATE TABLE IF NOT EXISTS runtime_state (
                id INTEGER PRIMARY KEY CHECK(id=1),
                running INTEGER NOT NULL DEFAULT 0,
                active_bucket_slug TEXT,
                active_metro_id INTEGER REFERENCES metros(id),
                active_phase TEXT CHECK(active_phase IN ('software','hardware')),
                current_session_id INTEGER,
                active_block_id INTEGER REFERENCES allocation_blocks(id),
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS search_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                block_id INTEGER NOT NULL REFERENCES allocation_blocks(id),
                bucket_slug TEXT NOT NULL,
                metro_id INTEGER NOT NULL REFERENCES metros(id),
                metro_cycle INTEGER NOT NULL,
                phase TEXT NOT NULL CHECK(phase IN ('software','hardware')),
                started_at TEXT NOT NULL,
                ended_at TEXT,
                duration_seconds REAL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_metro ON search_sessions(metro_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_block ON search_sessions(block_id,bucket_slug);

            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metro_id INTEGER NOT NULL REFERENCES metros(id),
                bucket_slug TEXT NOT NULL,
                metro_cycle INTEGER NOT NULL,
                phase TEXT NOT NULL CHECK(phase IN ('software','hardware')),
                apply_type TEXT NOT NULL CHECK(apply_type IN ('easy','hard')),
                session_id INTEGER REFERENCES search_sessions(id),
                block_id INTEGER REFERENCES allocation_blocks(id),
                applied_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_apps_date ON applications(applied_at);
            CREATE INDEX IF NOT EXISTS idx_apps_metro ON applications(metro_id);

            CREATE TABLE IF NOT EXISTS bucket_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                synced_at TEXT NOT NULL,
                bucket_slug TEXT NOT NULL,
                bucket_name TEXT NOT NULL,
                percentage REAL,
                ledger_seconds REAL,
                source_file TEXT,
                metro_json TEXT NOT NULL
            );
            """
        )
        # Lightweight migration for databases created by older builds.
        setting_cols = {r["name"] for r in con.execute("PRAGMA table_info(settings)")}
        if "bucket_tracking_started_at" not in setting_cols:
            con.execute("ALTER TABLE settings ADD COLUMN bucket_tracking_started_at TEXT")
        now = iso()
        con.execute(
            "INSERT OR IGNORE INTO settings(id,preset_minutes,website_controls,bucket_tracking_started_at,updated_at) VALUES(1,?,?,?,?)",
            (DEFAULT_PRESET_MINUTES, DEFAULT_WEBSITE_CONTROLS, now, now),
        )
        con.execute(
            "UPDATE settings SET bucket_tracking_started_at=COALESCE(bucket_tracking_started_at,?),updated_at=updated_at WHERE id=1",
            (now,),
        )
        con.execute(
            "INSERT OR IGNORE INTO runtime_state(id,running,updated_at) VALUES(1,0,?)",
            (now,),
        )

    # First startup: create/update active bucket definitions from data/*.csv.
    if list(DATA_DIR.glob("*.csv")):
        with db() as con:
            existing = con.execute("SELECT COUNT(*) n FROM buckets").fetchone()["n"]
        if existing == 0:
            sync_data_files_internal(initial=True)

    with db() as con:
        ensure_settings_for_active_buckets(con, redistribute_if_needed=True)
        if active_bucket_slugs(con):
            ensure_active_block(con)


def active_buckets(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM buckets WHERE active=1 ORDER BY position,id").fetchall()


def active_bucket_slugs(con: sqlite3.Connection) -> list[str]:
    return [r["slug"] for r in active_buckets(con)]


def ensure_settings_for_active_buckets(con: sqlite3.Connection, redistribute_if_needed: bool = False) -> None:
    slugs = active_bucket_slugs(con)
    if not slugs:
        return
    now = iso()
    existing = {r["bucket_slug"]: float(r["percentage"]) for r in con.execute("SELECT * FROM bucket_settings")}
    for slug in slugs:
        con.execute("INSERT OR IGNORE INTO ledger(bucket_slug,balance_seconds,updated_at) VALUES(?,?,?)", (slug, 0.0, now))
    # Remove active allocation settings for buckets no longer active.
    con.execute(
        f"DELETE FROM bucket_settings WHERE bucket_slug NOT IN ({','.join('?' for _ in slugs)})",
        slugs,
    )
    current = {k: v for k, v in existing.items() if k in slugs}
    total = sum(current.values())
    if redistribute_if_needed or len(current) != len(slugs) or abs(total - 100.0) > 0.01:
        pct = 100.0 / len(slugs)
        for slug in slugs:
            con.execute(
                "INSERT INTO bucket_settings(bucket_slug,percentage,updated_at) VALUES(?,?,?) ON CONFLICT(bucket_slug) DO UPDATE SET percentage=excluded.percentage,updated_at=excluded.updated_at",
                (slug, pct, now),
            )


def settings_row(con: sqlite3.Connection) -> sqlite3.Row:
    return con.execute("SELECT * FROM settings WHERE id=1").fetchone()


def percentage_map(con: sqlite3.Connection) -> dict[str, float]:
    return {r["bucket_slug"]: float(r["percentage"]) for r in con.execute("SELECT * FROM bucket_settings")}


def ledger_map(con: sqlite3.Connection) -> dict[str, float]:
    return {r["bucket_slug"]: float(r["balance_seconds"]) for r in con.execute("SELECT * FROM ledger")}


def simplex_project(values: list[float], total: float) -> list[float]:
    if total <= 0:
        return [0.0] * len(values)
    u = sorted(values, reverse=True)
    cssv = 0.0
    rho = 0
    for j, v in enumerate(u, 1):
        cssv += v
        t = (cssv - total) / j
        if v - t > 0:
            rho = j
    theta = (sum(u[:rho]) - total) / rho if rho else 0.0
    return [max(v - theta, 0.0) for v in values]


def create_block(con: sqlite3.Connection) -> int:
    ensure_settings_for_active_buckets(con)
    slugs = active_bucket_slugs(con)
    if not slugs:
        raise RuntimeError("No active bucket CSVs found in storage\\data")
    s = settings_row(con)
    preset_seconds = float(s["preset_minutes"]) * 60.0
    pcts = percentage_map(con)
    led = ledger_map(con)
    base = {b: preset_seconds * pcts[b] / 100.0 for b in slugs}
    desired = [base[b] - led.get(b, 0.0) for b in slugs]
    adjusted = simplex_project(desired, preset_seconds)
    cur = con.execute(
        "INSERT INTO allocation_blocks(started_at,preset_seconds,status) VALUES(?,?,'active')",
        (iso(), preset_seconds),
    )
    block_id = int(cur.lastrowid)
    for i, b in enumerate(slugs):
        con.execute(
            "INSERT INTO allocation_budgets(block_id,bucket_slug,percentage,base_seconds,adjusted_seconds) VALUES(?,?,?,?,?)",
            (block_id, b, pcts[b], base[b], adjusted[i]),
        )
    con.execute("UPDATE runtime_state SET active_block_id=?,updated_at=? WHERE id=1", (block_id, iso()))
    return block_id


def ensure_active_block(con: sqlite3.Connection) -> int:
    st = con.execute("SELECT * FROM runtime_state WHERE id=1").fetchone()
    if st and st["active_block_id"]:
        block = con.execute("SELECT * FROM allocation_blocks WHERE id=?", (st["active_block_id"],)).fetchone()
        if block and block["status"] == "active":
            return int(block["id"])
    row = con.execute("SELECT id FROM allocation_blocks WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        con.execute("UPDATE runtime_state SET active_block_id=?,updated_at=? WHERE id=1", (row["id"], iso()))
        return int(row["id"])
    return create_block(con)


def session_elapsed(row: sqlite3.Row, now: datetime | None = None) -> float:
    if row["ended_at"]:
        return float(row["duration_seconds"] or 0)
    start = parse_iso(row["started_at"])
    return max(0.0, ((now or utcnow()) - start).total_seconds()) if start else 0.0


def block_usage(con: sqlite3.Connection, block_id: int) -> dict[str, float]:
    usage = {b: 0.0 for b in active_bucket_slugs(con)}
    now = utcnow()
    for r in con.execute("SELECT * FROM search_sessions WHERE block_id=?", (block_id,)):
        usage.setdefault(r["bucket_slug"], 0.0)
        usage[r["bucket_slug"]] += session_elapsed(r, now)
    return usage


def live_ledger_map(con: sqlite3.Connection, block_id: int) -> dict[str, float]:
    """Return the current relative bucket imbalance, including the active block.

    Positive means the bucket has received more time than its target share;
    negative means it has received less.  The values always sum to ~0 across
    active buckets, so the ledger measures *ratio imbalance*, not whether the
    user worked more or fewer total minutes than the preset.
    """
    stored = ledger_map(con)
    budgets = con.execute(
        "SELECT bucket_slug, percentage FROM allocation_budgets WHERE block_id=?",
        (block_id,),
    ).fetchall()
    usage = block_usage(con, block_id)
    total_used = sum(usage.values())
    live: dict[str, float] = {}
    for r in budgets:
        slug = r["bucket_slug"]
        pct = float(r["percentage"]) / 100.0
        value = stored.get(slug, 0.0) + usage.get(slug, 0.0) - pct * total_used
        live[slug] = 0.0 if abs(value) < 0.5 else value
    return live


def close_open_session(con: sqlite3.Connection) -> None:
    st = con.execute("SELECT * FROM runtime_state WHERE id=1").fetchone()
    sid = st["current_session_id"] if st else None
    if not sid:
        return
    row = con.execute("SELECT * FROM search_sessions WHERE id=?", (sid,)).fetchone()
    if not row or row["ended_at"]:
        return
    end = utcnow()
    start = parse_iso(row["started_at"])
    duration = max(0.0, (end - start).total_seconds()) if start else 0.0
    con.execute("UPDATE search_sessions SET ended_at=?,duration_seconds=? WHERE id=?", (iso(end), duration, sid))
    con.execute("UPDATE runtime_state SET current_session_id=NULL,updated_at=? WHERE id=1", (iso(),))


def focus_metro(con: sqlite3.Connection, bucket: str) -> sqlite3.Row | None:
    row = con.execute(
        "SELECT * FROM metros WHERE bucket_slug=? AND active=1 AND NOT(software_done=1 AND hardware_done=1) ORDER BY position,id LIMIT 1",
        (bucket,),
    ).fetchone()
    if row:
        return row
    n = con.execute("SELECT COUNT(*) n FROM metros WHERE bucket_slug=? AND active=1", (bucket,)).fetchone()["n"]
    if not n:
        return None
    con.execute(
        "UPDATE metros SET software_done=0,hardware_done=0,cycle=cycle+1,updated_at=? WHERE bucket_slug=? AND active=1",
        (iso(), bucket),
    )
    return con.execute("SELECT * FROM metros WHERE bucket_slug=? AND active=1 ORDER BY position,id LIMIT 1", (bucket,)).fetchone()


def start_session(con: sqlite3.Connection, bucket: str, metro: sqlite3.Row, phase: str) -> int:
    block_id = ensure_active_block(con)
    cur = con.execute(
        "INSERT INTO search_sessions(block_id,bucket_slug,metro_id,metro_cycle,phase,started_at,created_at) VALUES(?,?,?,?,?,?,?)",
        (block_id, bucket, metro["id"], metro["cycle"], phase, iso(), iso()),
    )
    sid = int(cur.lastrowid)
    con.execute(
        "UPDATE runtime_state SET running=1,active_bucket_slug=?,active_metro_id=?,active_phase=?,current_session_id=?,active_block_id=?,updated_at=? WHERE id=1",
        (bucket, metro["id"], phase, sid, block_id, iso()),
    )
    return sid


def finalize_block(con: sqlite3.Connection, block_id: int, reason: str, apply_to_ledger: bool = True) -> None:
    block = con.execute("SELECT * FROM allocation_blocks WHERE id=?", (block_id,)).fetchone()
    if not block or block["status"] != "active":
        return
    if apply_to_ledger:
        live = live_ledger_map(con, block_id)
        now = iso()
        for slug, value in live.items():
            con.execute(
                "INSERT INTO ledger(bucket_slug,balance_seconds,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(bucket_slug) DO UPDATE SET balance_seconds=excluded.balance_seconds,updated_at=excluded.updated_at",
                (slug, value, now),
            )
    con.execute(
        "UPDATE allocation_blocks SET status='closed',ended_at=?,close_reason=? WHERE id=?",
        (iso(), reason, block_id),
    )
    create_block(con)


def maybe_roll_block(con: sqlite3.Connection) -> None:
    st = con.execute("SELECT * FROM runtime_state WHERE id=1").fetchone()
    block_id = st["active_block_id"] if st else None
    if not block_id:
        return
    block = con.execute("SELECT * FROM allocation_blocks WHERE id=?", (block_id,)).fetchone()
    if block and block["status"] == "active" and sum(block_usage(con, block_id).values()) >= float(block["preset_seconds"]):
        finalize_block(con, block_id, "preset_elapsed")


def parse_bucket_file(path: Path) -> tuple[str, str, list[tuple[str, str, int]]]:
    slug = path.stem.strip().upper()
    if not slug:
        raise ValueError(f"Invalid bucket filename: {path.name}")
    display = f"Bucket {slug}" if len(slug) <= 3 else path.stem.replace("_", " ").replace("-", " ").title()
    rows = list(csv.DictReader(path.open("r", encoding="utf-8-sig", newline="")))
    parsed: list[tuple[str, str, int]] = []
    fallback = 1
    for raw in rows:
        norm = {str(k).strip().lower(): v for k, v in raw.items()}
        cbsa = str(norm.get("cbsa") or norm.get("metro identifying number") or norm.get("metro_id") or "").strip()
        name = str(norm.get("msa") or norm.get("metro") or norm.get("msa name") or norm.get("full msa") or "").strip()
        if not cbsa or not name:
            continue
        pos_raw = norm.get("position") or norm.get("rank") or norm.get("rank_non_a") or fallback
        try:
            pos = int(float(pos_raw))
        except Exception:
            pos = fallback
        fallback += 1
        parsed.append((cbsa.zfill(5), name, pos))
    return slug, display, parsed



def sync_data_files_internal(initial: bool = False) -> dict[str, Any]:
    files = sorted(DATA_DIR.glob("*.csv"), key=lambda p: p.name.lower())
    if not files:
        raise RuntimeError("No CSV files found in storage\\data")
    parsed_files = [parse_bucket_file(p) for p in files]
    with db() as con:
        tx(con)
        st = con.execute("SELECT * FROM runtime_state WHERE id=1").fetchone()
        if st and st["running"]:
            raise RuntimeError("Stop the timer before syncing bucket data")

        old_active = {r["slug"] for r in con.execute("SELECT * FROM buckets WHERE active=1")}
        new_slugs = {slug for slug, _, _ in parsed_files}
        now = iso()

        # Removed buckets/metros are only deactivated. Their applications and
        # search sessions remain forever for later analysis/export.
        for slug in old_active - new_slugs:
            con.execute("UPDATE buckets SET active=0,updated_at=? WHERE slug=?", (now, slug))
            con.execute("UPDATE metros SET active=0,updated_at=? WHERE bucket_slug=?", (now, slug))

        for bpos, (slug, display, rows) in enumerate(parsed_files, 1):
            con.execute(
                "INSERT INTO buckets(slug,name,position,active,source_file,created_at,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET name=excluded.name,position=excluded.position,active=1,source_file=excluded.source_file,updated_at=excluded.updated_at",
                (slug, display, bpos, 1, f"{slug.lower()}.csv", now, now),
            )
            incoming = {cbsa for cbsa, _, _ in rows}
            existing = con.execute("SELECT * FROM metros WHERE bucket_slug=? AND active=1", (slug,)).fetchall()
            for m in existing:
                if m["cbsa"] not in incoming:
                    con.execute("UPDATE metros SET active=0,updated_at=? WHERE id=?", (now, m["id"]))

            # A sync means a brand-new traversal of every incoming bucket.
            # Reset ONLY temporary progress. Historical per-metro time and
            # application rows are untouched.
            for cbsa, name, pos in rows:
                existing_m = con.execute("SELECT * FROM metros WHERE cbsa=?", (cbsa,)).fetchone()
                if existing_m:
                    con.execute(
                        "UPDATE metros SET name=?,bucket_slug=?,position=?,active=1,software_done=0,hardware_done=0,cycle=cycle+1,updated_at=? WHERE id=?",
                        (name, slug, pos, now, existing_m["id"]),
                    )
                else:
                    con.execute(
                        "INSERT INTO metros(cbsa,name,bucket_slug,position,active,software_done,hardware_done,cycle,created_at,updated_at) VALUES(?,?,?,?,1,0,0,1,?,?)",
                        (cbsa, name, slug, pos, now, now),
                    )

        # Sync resets only bucket-level allocation state: percentages, ledger,
        # current bucket-time epoch, and traversal position. Historical
        # applications and per-metro search_sessions are never deleted.
        con.execute("UPDATE ledger SET balance_seconds=0,updated_at=?", (now,))
        con.execute("DELETE FROM bucket_settings")
        ensure_settings_for_active_buckets(con, redistribute_if_needed=True)

        st = con.execute("SELECT * FROM runtime_state WHERE id=1").fetchone()
        if st and st["active_block_id"]:
            con.execute(
                "UPDATE allocation_blocks SET status='closed',ended_at=?,close_reason='bucket_sync' WHERE id=? AND status='active'",
                (now, st["active_block_id"]),
            )

        con.execute(
            "UPDATE runtime_state SET running=0,active_bucket_slug=NULL,active_metro_id=NULL,active_phase=NULL,current_session_id=NULL,active_block_id=NULL,updated_at=? WHERE id=1",
            (now,),
        )
        con.execute(
            "UPDATE settings SET bucket_tracking_started_at=?,updated_at=? WHERE id=1",
            (now, now),
        )
        create_block(con)
        con.execute("COMMIT")
    return {"buckets": len(parsed_files), "metros": sum(len(x[2]) for x in parsed_files)}


class StartRequest(BaseModel):
    bucket: str


class AppRequest(BaseModel):
    apply_type: str = Field(pattern="^(easy|hard)$")


class CompleteRequest(BaseModel):
    phase: str = Field(pattern="^(software|hardware)$")
    confirmed: bool = False


class PresetRequest(BaseModel):
    minutes: int = Field(ge=5, le=720)
    percentages: dict[str, float]


class WebsiteControlsRequest(BaseModel):
    enabled: bool


class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.lock = threading.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket):
        self.clients.discard(ws)

    async def broadcast(self, payload: dict[str, Any]):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


hub = Hub()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    SERVER_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    try:
        yield
    finally:
        try:
            if SERVER_PID_FILE.exists() and SERVER_PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
                SERVER_PID_FILE.unlink()
        except Exception:
            pass


app = FastAPI(title="Search", version="9.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def metro_counts(con: sqlite3.Connection, metro_id: int) -> dict[str, Any]:
    counts = {"software": {"easy": 0, "hard": 0}, "hardware": {"easy": 0, "hard": 0}}
    for r in con.execute("SELECT phase,apply_type,COUNT(*) n FROM applications WHERE metro_id=? GROUP BY phase,apply_type", (metro_id,)):
        counts[r["phase"]][r["apply_type"]] = int(r["n"])
    return counts


def metro_time(con: sqlite3.Connection, metro_id: int) -> dict[str, float]:
    out = {"software": 0.0, "hardware": 0.0}
    now = utcnow()
    for r in con.execute("SELECT * FROM search_sessions WHERE metro_id=?", (metro_id,)):
        out[r["phase"]] += session_elapsed(r, now)
    return out


def state_payload() -> dict[str, Any]:
    with db() as con:
        buckets = active_buckets(con)
        slugs = [b["slug"] for b in buckets]
        if not slugs:
            return {"server_now": iso(), "running": False, "buckets": [], "settings": dict(settings_row(con)), "metros": {}, "focus": {}, "bucket_status": {}}
        block_id = ensure_active_block(con)
        block = con.execute("SELECT * FROM allocation_blocks WHERE id=?", (block_id,)).fetchone()
        budgets = {r["bucket_slug"]: dict(r) for r in con.execute("SELECT * FROM allocation_budgets WHERE block_id=?", (block_id,))}
        usage = block_usage(con, block_id)
        led = live_ledger_map(con, block_id)
        st = con.execute("SELECT * FROM runtime_state WHERE id=1").fetchone()

        focuses: dict[str, Any] = {}
        rows_by_bucket: dict[str, list[Any]] = {}
        for slug in slugs:
            m = focus_metro(con, slug)
            focuses[slug] = dict(m) if m else None
            rows = con.execute("SELECT * FROM metros WHERE bucket_slug=? AND active=1 ORDER BY position,id", (slug,)).fetchall()
            rows_by_bucket[slug] = []
            for r in rows:
                c = metro_counts(con, r["id"])
                rows_by_bucket[slug].append({
                    **dict(r),
                    "is_focus": m is not None and r["id"] == m["id"],
                    "counts": c,
                })

        active = None
        if st and st["running"] and st["active_metro_id"]:
            m = con.execute("SELECT * FROM metros WHERE id=?", (st["active_metro_id"],)).fetchone()
            sess = con.execute("SELECT * FROM search_sessions WHERE id=?", (st["current_session_id"],)).fetchone() if st["current_session_id"] else None
            active = {
                "bucket": st["active_bucket_slug"],
                "metro_id": st["active_metro_id"],
                "metro_name": m["name"] if m else None,
                "cbsa": m["cbsa"] if m else None,
                "phase": st["active_phase"],
                "session_started_at": sess["started_at"] if sess else None,
            }

        bucket_status = {}
        for slug in slugs:
            bd = budgets.get(slug)
            if not bd:
                continue
            allowed = float(bd["adjusted_seconds"])
            used = float(usage.get(slug, 0.0))
            bucket_status[slug] = {
                "used_seconds": used,
                "allowed_seconds": allowed,
                "base_seconds": float(bd["base_seconds"]),
                "percentage": float(bd["percentage"]),
                "remaining_seconds": allowed - used,
                "over": used >= allowed if allowed > 0 else used > 0,
                "ledger_seconds": led.get(slug, 0.0),
            }

        return {
            "server_now": iso(),
            "running": bool(st["running"]),
            "active": active,
            "settings": dict(settings_row(con)),
            "block": {"id": block_id, "preset_seconds": float(block["preset_seconds"]), "total_used_seconds": sum(usage.values())},
            "buckets": [dict(b) for b in buckets],
            "bucket_status": bucket_status,
            "focus": focuses,
            "metros": rows_by_bucket,
        }


def dashboard_payload() -> dict[str, Any]:
    with db() as con:
        local_today = datetime.now().date().isoformat()
        apps = con.execute("SELECT * FROM applications ORDER BY applied_at").fetchall()
        hard_total = sum(1 for r in apps if r["apply_type"] == "hard")
        hard_today = easy_today = 0
        daily_hard: dict[str, int] = {}
        for r in apps:
            dt = parse_iso(r["applied_at"])
            day = dt.astimezone().date().isoformat() if dt else ""
            if r["apply_type"] == "hard":
                daily_hard[day] = daily_hard.get(day, 0) + 1
                if day == local_today:
                    hard_today += 1
            elif day == local_today:
                easy_today += 1

        chart = []
        if daily_hard:
            d = datetime.fromisoformat(min(daily_hard)).date()
            today = datetime.now().date()
            while d <= today:
                chart.append({"date": d.isoformat(), "hard": daily_hard.get(d.isoformat(), 0)})
                d += timedelta(days=1)

        slugs = active_bucket_slugs(con)
        time_today = {b: 0.0 for b in slugs}
        all_days: dict[str, dict[str, float]] = {}
        now = utcnow()
        bucket_epoch = parse_iso(settings_row(con)["bucket_tracking_started_at"])
        for r in con.execute("SELECT * FROM search_sessions"):
            started = parse_iso(r["started_at"])
            if bucket_epoch and started and started < bucket_epoch:
                continue
            start = parse_iso(r["started_at"])
            if not start:
                continue
            day = start.astimezone().date().isoformat()
            all_days.setdefault(day, {})[r["bucket_slug"]] = all_days.setdefault(day, {}).get(r["bucket_slug"], 0.0) + session_elapsed(r, now)
            if day == local_today and r["bucket_slug"] in time_today:
                time_today[r["bucket_slug"]] += session_elapsed(r, now)
        active_days = len(all_days)
        avg_time = {b: (sum(day.get(b, 0.0) for day in all_days.values()) / active_days if active_days else 0.0) for b in slugs}
        app_active_days = len({parse_iso(r["applied_at"]).astimezone().date().isoformat() for r in apps if parse_iso(r["applied_at"])})
        hard_daily_avg = hard_total / app_active_days if app_active_days else 0.0

        pts = chart[-14:]
        slope = 0.0
        if len(pts) >= 2:
            xs = list(range(len(pts))); ys = [p["hard"] for p in pts]
            xb = sum(xs)/len(xs); yb = sum(ys)/len(ys); den = sum((x-xb)**2 for x in xs)
            slope = sum((x-xb)*(y-yb) for x,y in zip(xs,ys))/den if den else 0.0

        return {
            "hard_total": hard_total,
            "hard_today": hard_today,
            "easy_today": easy_today,
            "hard_daily_average": hard_daily_avg,
            "time_today": time_today,
            "avg_time_per_active_day": avg_time,
            "chart": chart,
            "trend": "green" if slope >= 0 else "red",
        }


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def get_state():
    return state_payload()


@app.get("/api/dashboard")
def get_dashboard():
    return dashboard_payload()


@app.post("/api/timer/start")
async def timer_start(req: StartRequest):
    bucket = req.bucket.upper()
    with db() as con:
        tx(con)
        if bucket not in active_bucket_slugs(con):
            raise HTTPException(404, "Unknown bucket")
        st = con.execute("SELECT * FROM runtime_state WHERE id=1").fetchone()
        if st["running"]:
            raise HTTPException(409, f"Bucket {st['active_bucket_slug']} is already running")
        metro = focus_metro(con, bucket)
        if not metro:
            raise HTTPException(404, f"Bucket {bucket} has no metros")
        phase = "hardware" if metro["software_done"] else "software"
        start_session(con, bucket, metro, phase)
        con.execute("COMMIT")
    payload = state_payload(); await hub.broadcast({"type":"state","state":payload}); return payload


@app.post("/api/timer/stop")
async def timer_stop():
    with db() as con:
        tx(con)
        st = con.execute("SELECT * FROM runtime_state WHERE id=1").fetchone()
        if st["running"]:
            close_open_session(con)
            con.execute("UPDATE runtime_state SET running=0,active_bucket_slug=NULL,active_metro_id=NULL,active_phase=NULL,current_session_id=NULL,updated_at=? WHERE id=1", (iso(),))
            maybe_roll_block(con)
        con.execute("COMMIT")
    payload = state_payload(); await hub.broadcast({"type":"state","state":payload}); return payload


@app.post("/api/phase/complete")
async def complete_phase(req: CompleteRequest):
    with db() as con:
        tx(con)
        st = con.execute("SELECT * FROM runtime_state WHERE id=1").fetchone()
        if not st["running"] or not st["active_metro_id"]:
            raise HTTPException(409, "Start a bucket first")
        if req.phase != st["active_phase"]:
            raise HTTPException(409, f"Current phase is {st['active_phase']}")
        if req.phase == "hardware" and not req.confirmed:
            raise HTTPException(409, "Hardware completion requires confirmation")
        metro = con.execute("SELECT * FROM metros WHERE id=?", (st["active_metro_id"],)).fetchone()
        close_open_session(con)
        if req.phase == "software":
            con.execute("UPDATE metros SET software_done=1,updated_at=? WHERE id=?", (iso(), metro["id"]))
            metro = con.execute("SELECT * FROM metros WHERE id=?", (metro["id"],)).fetchone()
            start_session(con, metro["bucket_slug"], metro, "hardware")
        else:
            con.execute("UPDATE metros SET hardware_done=1,updated_at=? WHERE id=?", (iso(), metro["id"]))
            nxt = focus_metro(con, metro["bucket_slug"])
            if nxt:
                start_session(con, metro["bucket_slug"], nxt, "hardware" if nxt["software_done"] else "software")
            else:
                con.execute("UPDATE runtime_state SET running=0,active_bucket_slug=NULL,active_metro_id=NULL,active_phase=NULL,current_session_id=NULL,updated_at=? WHERE id=1", (iso(),))
        con.execute("COMMIT")
    payload = state_payload(); await hub.broadcast({"type":"state","state":payload}); return payload


@app.post("/api/applications")
async def add_application(req: AppRequest):
    with db() as con:
        tx(con)
        st = con.execute("SELECT * FROM runtime_state WHERE id=1").fetchone()
        if not st["running"] or not st["active_metro_id"] or not st["active_phase"]:
            raise HTTPException(409, "No active metro search")
        metro = con.execute("SELECT * FROM metros WHERE id=?", (st["active_metro_id"],)).fetchone()
        con.execute(
            "INSERT INTO applications(metro_id,bucket_slug,metro_cycle,phase,apply_type,session_id,block_id,applied_at) VALUES(?,?,?,?,?,?,?,?)",
            (metro["id"], metro["bucket_slug"], metro["cycle"], st["active_phase"], req.apply_type, st["current_session_id"], st["active_block_id"], iso()),
        )
        con.execute("COMMIT")
    payload = state_payload(); await hub.broadcast({"type":"state","state":payload}); return payload


@app.post("/api/preset")
async def update_preset(req: PresetRequest):
    with db() as con:
        tx(con)
        slugs = active_bucket_slugs(con)
        if set(req.percentages) != set(slugs):
            raise HTTPException(400, "Percentages must include every active bucket exactly once")
        if any(v < 0 or v > 100 for v in req.percentages.values()) or abs(sum(req.percentages.values()) - 100.0) > 0.01:
            raise HTTPException(400, "Percentages must be between 0 and 100 and total 100")
        st = con.execute("SELECT * FROM runtime_state WHERE id=1").fetchone()
        if st["running"]:
            raise HTTPException(409, "Stop the timer before changing presets")
        old = ensure_active_block(con)
        finalize_block(con, old, "preset_changed")
        # finalize_block created a replacement using old settings; close it and replace.
        st = con.execute("SELECT * FROM runtime_state WHERE id=1").fetchone()
        if st["active_block_id"]:
            con.execute("UPDATE allocation_blocks SET status='closed',ended_at=?,close_reason='superseded_by_new_preset' WHERE id=? AND status='active'", (iso(), st["active_block_id"]))
        con.execute("UPDATE settings SET preset_minutes=?,updated_at=? WHERE id=1", (req.minutes, iso()))
        for slug, pct in req.percentages.items():
            con.execute("UPDATE bucket_settings SET percentage=?,updated_at=? WHERE bucket_slug=?", (pct, iso(), slug))
        create_block(con)
        con.execute("COMMIT")
    payload = state_payload(); await hub.broadcast({"type":"state","state":payload}); return payload


@app.post("/api/block/reset")
async def reset_block():
    with db() as con:
        tx(con)
        st = con.execute("SELECT * FROM runtime_state WHERE id=1").fetchone()
        if st["running"]:
            raise HTTPException(409, "Stop the timer before starting a new block")
        finalize_block(con, ensure_active_block(con), "manual_reset")
        con.execute("COMMIT")
    payload = state_payload(); await hub.broadcast({"type":"state","state":payload}); return payload


@app.post("/api/settings/website-controls")
async def website_controls(req: WebsiteControlsRequest):
    with db() as con:
        con.execute("UPDATE settings SET website_controls=?,updated_at=? WHERE id=1", (1 if req.enabled else 0, iso()))
    payload = state_payload(); await hub.broadcast({"type":"state","state":payload}); return payload


@app.post("/api/data/sync")
async def sync_data():
    try:
        result = sync_data_files_internal(initial=False)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    payload = state_payload(); await hub.broadcast({"type":"state","state":payload}); return {**result, "state": payload}



@app.post("/api/shutdown")
async def shutdown_search(request: Request):
    # This service binds only to 127.0.0.1; keep the shutdown endpoint local-only too.
    client_host = request.client.host if request.client else None
    if client_host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(403, "Shutdown is only available locally")

    def _request_exit():
        global UVICORN_SERVER
        if UVICORN_SERVER is not None:
            UVICORN_SERVER.should_exit = True

    # Return the HTTP response first, then gracefully stop Uvicorn.
    threading.Timer(1.0, _request_exit).start()
    return {"ok": True, "shutdown_in_seconds": 1}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await hub.connect(ws)
    try:
        await ws.send_json({"type":"state","state":state_payload()})
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        hub.disconnect(ws)


if __name__ == "__main__":
    import uvicorn

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    debug_logging = (LOG_DIR / "debug.flag").exists()

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=8765,
        reload=False,
        access_log=debug_logging,
        log_level="info" if debug_logging else "warning",
    )
    UVICORN_SERVER = uvicorn.Server(config)
    UVICORN_SERVER.run()
