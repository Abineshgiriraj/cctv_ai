import os
import sqlite3
import threading
from datetime import datetime


class TrafficStore:
    """Small persistent SQLite store for unique vehicle crossing events."""

    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_lock = threading.Lock()
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _initialize(self):
        with self._init_lock:
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS vehicle_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        camera_ip TEXT NOT NULL,
                        track_id INTEGER NOT NULL,
                        vehicle_type TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        confidence REAL NOT NULL DEFAULT 0,
                        crossed_at TEXT NOT NULL,
                        event_date TEXT NOT NULL,
                        UNIQUE(session_id, camera_ip, track_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_vehicle_events_date
                        ON vehicle_events(event_date, camera_ip, vehicle_type);

                    CREATE INDEX IF NOT EXISTS idx_vehicle_events_crossed_at
                        ON vehicle_events(crossed_at DESC);
                    """
                )

    def record_vehicle(self, *, session_id: str, camera_ip: str, track_id: int,
                       vehicle_type: str, direction: str, confidence: float) -> bool:
        now = datetime.now().astimezone()
        crossed_at = now.isoformat(timespec="seconds")
        event_date = now.date().isoformat()
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO vehicle_events
                    (session_id, camera_ip, track_id, vehicle_type, direction,
                     confidence, crossed_at, event_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        camera_ip,
                        int(track_id),
                        vehicle_type.lower(),
                        direction,
                        float(confidence),
                        crossed_at,
                        event_date,
                    ),
                )
                return cur.rowcount > 0
        except sqlite3.Error:
            return False

    def counts(self, *, event_date: str | None = None, camera_ip: str | None = None):
        event_date = event_date or datetime.now().date().isoformat()
        where = ["event_date = ?"]
        params = [event_date]
        if camera_ip:
            where.append("camera_ip = ?")
            params.append(camera_ip)

        sql = f"""
            SELECT camera_ip, vehicle_type, direction, COUNT(*) AS total
            FROM vehicle_events
            WHERE {' AND '.join(where)}
            GROUP BY camera_ip, vehicle_type, direction
            ORDER BY camera_ip, vehicle_type, direction
        """

        result = {
            "date": event_date,
            "camera_ip": camera_ip,
            "totals": {},
            "by_camera": {},
            "grand_total": 0,
        }
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        for row in rows:
            cam = row["camera_ip"]
            vehicle_type = row["vehicle_type"]
            direction = row["direction"]
            total = int(row["total"])

            cam_row = result["by_camera"].setdefault(cam, {"total": 0, "types": {}})
            type_row = cam_row["types"].setdefault(vehicle_type, {"total": 0, "up": 0, "down": 0})
            type_row["total"] += total
            type_row[direction] = type_row.get(direction, 0) + total
            cam_row["total"] += total

            result["totals"][vehicle_type] = result["totals"].get(vehicle_type, 0) + total
            result["grand_total"] += total

        return result

    def recent(self, limit: int = 25):
        limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, camera_ip, track_id, vehicle_type, direction,
                       confidence, crossed_at
                FROM vehicle_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
