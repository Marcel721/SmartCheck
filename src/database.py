import sqlite3
from datetime import datetime
from src.config import DB_PATH

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hdds (
            serial_number TEXT PRIMARY KEY,
            model TEXT,
            pending_sectors INTEGER,
            reallocated_sectors INTEGER,
            uncorrectable_sectors INTEGER,
            smart_overall TEXT,
            test_result TEXT,
            slot_id INTEGER,
            tested_at TEXT,
            test_duration_seconds REAL
        )
    """)
    # Automatisch 'tested_at'-Spalte nachrüsten, falls Datenbank schon existiert
    try:
        cursor.execute("ALTER TABLE hdds ADD COLUMN tested_at TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE hdds ADD COLUMN test_duration_seconds REAL")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

def save_or_update_hdd(data: dict) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    cursor.execute("""
        INSERT INTO hdds (
            serial_number, model, pending_sectors, reallocated_sectors,
            uncorrectable_sectors, smart_overall, test_result, slot_id, tested_at,
            test_duration_seconds
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(serial_number) DO UPDATE SET
            model=excluded.model,
            pending_sectors=excluded.pending_sectors,
            reallocated_sectors=excluded.reallocated_sectors,
            uncorrectable_sectors=excluded.uncorrectable_sectors,
            smart_overall=excluded.smart_overall,
            test_result=excluded.test_result,
            slot_id=excluded.slot_id,
            tested_at=excluded.tested_at,
            test_duration_seconds=excluded.test_duration_seconds
    """, (
        data["serial_number"],
        data.get("model", "UNKNOWN"),
        data.get("pending_sectors", 0),
        data.get("reallocated_sectors", 0),
        data.get("uncorrectable_sectors", 0),
        data.get("smart_overall", "UNKNOWN"),
        data.get("test_result", "UNKNOWN"),
        data.get("slot_id", 1),
        now_str,
        data.get("test_duration_seconds")
    ))
    conn.commit()
    conn.close()
    return now_str

def get_all_hdds():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM hdds ORDER BY tested_at DESC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def get_hdd_by_serial(serial_number: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM hdds WHERE serial_number = ?", (serial_number,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None