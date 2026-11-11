"""SQLite persistence with versioned, additive migrations.

Every migration is append-only and runs once; schema_version records which have
run. Nothing here drops or rewrites existing tables.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager

from engine import VAR

DB = VAR / "operations.db"

MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "operations core", """
CREATE TABLE IF NOT EXISTS missions (
  id TEXT PRIMARY KEY, created_utc TEXT, issue_date TEXT, horizon INTEGER,
  status TEXT, author TEXT, params TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS flight_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT, uploaded_utc TEXT,
  filename TEXT, provenance TEXT, n_rows INTEGER, payload TEXT);
CREATE TABLE IF NOT EXISTS telemetry (
  id INTEGER PRIMARY KEY AUTOINCREMENT, station_id TEXT, ts_utc TEXT,
  received_utc TEXT, seq INTEGER, payload TEXT);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT, actor TEXT, action TEXT, detail TEXT);
"""),
    (2, "users and sessions", """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  role TEXT NOT NULL, organisation TEXT, pw_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  demo INTEGER NOT NULL DEFAULT 0, created_utc TEXT, last_login_utc TEXT);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_utc TEXT, expires_utc TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id));
"""),
    (3, "field surveillance", """
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, site_id TEXT NOT NULL, issue_date TEXT, horizon INTEGER,
  reason TEXT, priority TEXT, status TEXT NOT NULL, due_date TEXT, assignee_id INTEGER,
  created_by INTEGER, created_utc TEXT, updated_utc TEXT, alert_id TEXT, demo INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, client_uuid TEXT UNIQUE, task_id INTEGER, site_id TEXT,
  lat REAL, lon REAL, gps_accuracy_m REAL, observed_at TEXT NOT NULL, observer_id INTEGER,
  water_present TEXT, habitat_type TEXT, larvae_present TEXT, dips INTEGER, larvae_count INTEGER,
  anopheles_present TEXT, species TEXT, stages TEXT, pupae_present TEXT, pupae_count INTEGER,
  water_depth_cm REAL, vegetation_cover_pct REAL, notes TEXT, quality_flags TEXT,
  status TEXT NOT NULL, reviewer_id INTEGER, review_comment TEXT, reviewed_utc TEXT,
  created_utc TEXT, demo INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS interventions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, site_id TEXT NOT NULL, observation_id INTEGER NOT NULL,
  method TEXT NOT NULL, product TEXT, planned_dose TEXT, protocol_ref TEXT NOT NULL,
  status TEXT NOT NULL, authorized_by INTEGER NOT NULL, authorized_utc TEXT,
  performed_by INTEGER, performed_at TEXT, quantity_used TEXT, performance_notes TEXT,
  followup_observation_id INTEGER, outcome TEXT, closed_utc TEXT, demo INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS alert_states (
  alert_id TEXT PRIMARY KEY, state TEXT NOT NULL, actor_id INTEGER, ts_utc TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT, user_id INTEGER, page TEXT, message TEXT);
"""),
    (4, "mission approval trail", """
ALTER TABLE missions ADD COLUMN approved_by INTEGER;
ALTER TABLE missions ADD COLUMN approved_utc TEXT;
ALTER TABLE missions ADD COLUMN demo INTEGER NOT NULL DEFAULT 0;
"""),
    (5, "programme uptake: habitats, survey fields, outbox, user district", """
CREATE TABLE IF NOT EXISTS habitats (
  code TEXT PRIMARY KEY, lat REAL NOT NULL, lon REAL NOT NULL, habitat_type TEXT, permanence TEXT,
  description TEXT, created_by INTEGER, created_utc TEXT, first_observation_id INTEGER,
  demo INTEGER NOT NULL DEFAULT 0);
ALTER TABLE observations ADD COLUMN habitat_permanence TEXT;
ALTER TABLE observations ADD COLUMN distance_to_house_m REAL;
ALTER TABLE observations ADD COLUMN sun_exposure TEXT;
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_utc TEXT, kind TEXT NOT NULL, channel TEXT NOT NULL,
  language TEXT NOT NULL, recipient_user_id INTEGER, recipient_label TEXT, body TEXT NOT NULL,
  status TEXT NOT NULL, status_note TEXT, created_by INTEGER, ref TEXT, demo INTEGER NOT NULL DEFAULT 0);
ALTER TABLE users ADD COLUMN district TEXT;
ALTER TABLE users ADD COLUMN phone TEXT;
ALTER TABLE users ADD COLUMN language TEXT NOT NULL DEFAULT 'en';
"""),
    (6, "evidence platform: sources, datasets, samples, products, areas, training, registry", """
CREATE TABLE IF NOT EXISTS data_sources (
  key TEXT PRIMARY KEY, name TEXT NOT NULL, domain TEXT NOT NULL, mode TEXT NOT NULL, origin TEXT,
  expected_update_days REAL, connector_state TEXT NOT NULL DEFAULT 'not_configured', last_ingest_utc TEXT,
  last_observation TEXT, validation_status TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS ingestion_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT NOT NULL, started_utc TEXT, finished_utc TEXT,
  status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 1, n_received INTEGER, n_accepted INTEGER, n_duplicate INTEGER,
  n_rejected INTEGER, message TEXT, actor TEXT, dataset_version_id INTEGER);
CREATE TABLE IF NOT EXISTS dataset_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, version INTEGER NOT NULL, label TEXT, filename TEXT,
  sha256 TEXT, raw_path TEXT, processed_path TEXT, n_rows INTEGER, columns TEXT, report TEXT, status TEXT NOT NULL,
  provenance TEXT NOT NULL, target TEXT, horizon INTEGER, created_by TEXT, created_utc TEXT, confirmed_by TEXT,
  confirmed_utc TEXT, demo INTEGER NOT NULL DEFAULT 0, UNIQUE(kind, version));
CREATE TABLE IF NOT EXISTS environmental_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT NOT NULL, dataset_version_id INTEGER, station_id TEXT, site_id TEXT,
  lat REAL, lon REAL, observed_utc TEXT NOT NULL, received_utc TEXT, variable TEXT NOT NULL, value REAL, unit TEXT,
  quality_flag TEXT, provenance TEXT NOT NULL, dedupe_key TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS entomological_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, dataset_version_id INTEGER, observation_id INTEGER, site_id TEXT,
  task_id INTEGER, intervention_id INTEGER, lat REAL, lon REAL, sample_date TEXT NOT NULL, collector TEXT, sampling_method TEXT, dips INTEGER,
  water_present TEXT, larvae_present TEXT, anopheles_larvae_count INTEGER, stage_early INTEGER, stage_late INTEGER, pupae_count INTEGER,
  species TEXT, sample_type TEXT NOT NULL DEFAULT 'larval', resistance_finding TEXT, provenance TEXT NOT NULL, demo INTEGER NOT NULL DEFAULT 0,
  dedupe_key TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS raster_products (
  product_id TEXT PRIMARY KEY, source_key TEXT NOT NULL, platform TEXT, acquired_utc TEXT, acquired_date TEXT, processed_utc TEXT,
  resolution_m REAL, cloud_pct REAL, relative_orbit INTEGER, orbit_state TEXT, processing_method TEXT, storage_ref TEXT, provenance TEXT NOT NULL,
  validation_status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS administrative_areas (
  code TEXT PRIMARY KEY, level TEXT NOT NULL, name TEXT NOT NULL, parent TEXT, onboarded INTEGER NOT NULL DEFAULT 0,
  geometry_ref TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS training_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, target TEXT NOT NULL, horizon INTEGER NOT NULL, dataset_version_id INTEGER, config TEXT, status TEXT NOT NULL,
  seed INTEGER, metrics TEXT, message TEXT, created_by TEXT, created_utc TEXT, started_utc TEXT, finished_utc TEXT, model_id TEXT,
  predictions_path TEXT, predictions_sha256 TEXT);
CREATE TABLE IF NOT EXISTS model_registry (
  id TEXT PRIMARY KEY, target TEXT NOT NULL, horizon INTEGER, version TEXT NOT NULL, status TEXT NOT NULL, path TEXT, metrics TEXT, training_run_id INTEGER,
  dataset_version_id INTEGER, audit_warning INTEGER NOT NULL DEFAULT 0, notes TEXT, decided_by TEXT, decided_utc TEXT, created_utc TEXT);
CREATE TABLE IF NOT EXISTS observation_photos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, observation_id INTEGER NOT NULL, path TEXT NOT NULL, content_type TEXT, bytes INTEGER,
  uploaded_by INTEGER, uploaded_utc TEXT);
ALTER TABLE observations ADD COLUMN sampling_method TEXT;
ALTER TABLE observations ADD COLUMN anopheles_larvae_count INTEGER;
ALTER TABLE interventions ADD COLUMN habitat_suitability TEXT;
ALTER TABLE interventions ADD COLUMN protocol_status TEXT;
ALTER TABLE interventions ADD COLUMN decision_reason TEXT;
ALTER TABLE missions ADD COLUMN kind TEXT NOT NULL DEFAULT 'inspection';
ALTER TABLE missions ADD COLUMN flight_result TEXT;
ALTER TABLE missions ADD COLUMN decision_note TEXT;
"""),
]


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _migrate(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, name TEXT, applied_utc TEXT)")
    done = {r[0] for r in c.execute("SELECT version FROM schema_version")}
    for v, name, sql in MIGRATIONS:
        if v in done:
            continue
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            try:
                c.execute(stmt)
            except sqlite3.OperationalError as e:  # column already present from an earlier hand migration
                if "duplicate column" not in str(e):
                    raise
        c.execute("INSERT INTO schema_version VALUES (?,?,?)", (v, name, now()))
    c.commit()


_ready = False


def conn() -> sqlite3.Connection:
    global _ready
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    if not _ready:
        c.execute("PRAGMA journal_mode = WAL")
        _migrate(c)
        _ready = True
    return c


@contextmanager
def tx():
    c = conn()
    try:
        yield c
        c.commit()
    finally:
        c.close()


def schema_version() -> int:
    with tx() as c:
        r = c.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(r[0] or 0)


def backup(dest) -> None:
    """Online, consistent copy using SQLite's backup API."""
    src = conn()
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
