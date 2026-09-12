import json
from datetime import datetime

import pymysql


class TrafficStore:
    """Persistent MySQL store for traffic counts, violations and road events."""

    def __init__(self, host, port, user, password, database):
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self.database = database
        self._initialize()

    def _connect(self):
        return pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset="utf8mb4",
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def _initialize(self):
        statements = [
            """
            CREATE TABLE IF NOT EXISTS vehicle_events (
                id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(32) NOT NULL,
                camera_ip VARCHAR(45) NOT NULL,
                track_id BIGINT NOT NULL,
                vehicle_type VARCHAR(32) NOT NULL,
                direction VARCHAR(16) NOT NULL,
                confidence DECIMAL(6,5) NOT NULL DEFAULT 0,
                crossed_at DATETIME NOT NULL,
                event_date DATE NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_vehicle_session_track (session_id, camera_ip, track_id),
                KEY idx_vehicle_event_date (event_date, camera_ip, vehicle_type),
                KEY idx_vehicle_crossed_at (crossed_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS daily_vehicle_counts (
                event_date DATE NOT NULL,
                camera_ip VARCHAR(45) NOT NULL,
                vehicle_type VARCHAR(32) NOT NULL,
                direction VARCHAR(16) NOT NULL,
                total_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (event_date, camera_ip, vehicle_type, direction)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS violations (
                id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(32) NOT NULL,
                camera_ip VARCHAR(45) NOT NULL,
                violation_type VARCHAR(64) NOT NULL,
                vehicle_type VARCHAR(32) NULL,
                vehicle_track_id BIGINT NULL,
                person_track_id BIGINT NULL,
                helmet_status VARCHAR(32) NULL,
                plate_number VARCHAR(32) NULL,
                plate_confidence DECIMAL(6,5) NULL,
                detection_confidence DECIMAL(6,5) NOT NULL DEFAULT 0,
                captured_at DATETIME NOT NULL,
                event_date DATE NOT NULL,
                evidence_image LONGBLOB NULL,
                plate_image LONGBLOB NULL,
                metadata_json JSON NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                KEY idx_violation_date (event_date, camera_ip, violation_type),
                KEY idx_violation_plate (plate_number),
                KEY idx_violation_captured (captured_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS road_events (
                id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(32) NOT NULL,
                camera_ip VARCHAR(45) NOT NULL,
                event_type VARCHAR(64) NOT NULL,
                model_label VARCHAR(64) NOT NULL,
                confidence DECIMAL(6,5) NOT NULL DEFAULT 0,
                captured_at DATETIME NOT NULL,
                event_date DATE NOT NULL,
                evidence_image LONGBLOB NULL,
                metadata_json JSON NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                KEY idx_road_event_date (event_date, camera_ip, event_type),
                KEY idx_road_event_captured (captured_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
        ]
        with self._connect() as conn:
            with conn.cursor() as cur:
                for sql in statements:
                    cur.execute(sql)
            conn.commit()

    def record_vehicle(self, *, session_id, camera_ip, track_id, vehicle_type, direction, confidence):
        now = datetime.now()
        sql_event = """
            INSERT IGNORE INTO vehicle_events
            (session_id, camera_ip, track_id, vehicle_type, direction, confidence, crossed_at, event_date)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """
        sql_count = """
            INSERT INTO daily_vehicle_counts
            (event_date, camera_ip, vehicle_type, direction, total_count)
            VALUES (%s,%s,%s,%s,1)
            ON DUPLICATE KEY UPDATE total_count = total_count + 1
        """
        with self._connect() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(sql_event, (session_id, camera_ip, int(track_id), vehicle_type.lower(), direction,
                                            float(confidence), now, now.date()))
                    inserted = cur.rowcount > 0
                    if inserted:
                        cur.execute(sql_count, (now.date(), camera_ip, vehicle_type.lower(), direction))
                conn.commit()
                return inserted
            except Exception:
                conn.rollback()
                raise

    def record_violation(self, *, session_id, camera_ip, violation_type, vehicle_type=None,
                         vehicle_track_id=None, person_track_id=None, helmet_status=None,
                         plate_number=None, plate_confidence=None, detection_confidence=0,
                         evidence_image=None, plate_image=None, metadata=None):
        now = datetime.now()
        sql = """
            INSERT INTO violations
            (session_id, camera_ip, violation_type, vehicle_type, vehicle_track_id, person_track_id,
             helmet_status, plate_number, plate_confidence, detection_confidence, captured_at,
             event_date, evidence_image, plate_image, metadata_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (session_id, camera_ip, violation_type, vehicle_type, vehicle_track_id,
                                  person_track_id, helmet_status, plate_number, plate_confidence,
                                  float(detection_confidence or 0), now, now.date(), evidence_image,
                                  plate_image, json.dumps(metadata or {}, ensure_ascii=False)))
                row_id = cur.lastrowid
            conn.commit()
            return row_id

    def record_road_event(self, *, session_id, camera_ip, event_type, model_label,
                          confidence, evidence_image=None, metadata=None):
        now = datetime.now()
        sql = """
            INSERT INTO road_events
            (session_id, camera_ip, event_type, model_label, confidence, captured_at,
             event_date, evidence_image, metadata_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (session_id, camera_ip, event_type, model_label, float(confidence),
                                  now, now.date(), evidence_image,
                                  json.dumps(metadata or {}, ensure_ascii=False)))
                row_id = cur.lastrowid
            conn.commit()
            return row_id

    def counts(self, *, event_date=None, camera_ip=None):
        event_date = event_date or datetime.now().date().isoformat()
        where = ["event_date = %s"]
        params = [event_date]
        if camera_ip:
            where.append("camera_ip = %s")
            params.append(camera_ip)
        sql = f"""
            SELECT camera_ip, vehicle_type, direction, total_count AS total
            FROM daily_vehicle_counts
            WHERE {' AND '.join(where)}
            ORDER BY camera_ip, vehicle_type, direction
        """
        result = {"date": event_date, "camera_ip": camera_ip, "totals": {}, "by_camera": {}, "grand_total": 0}
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
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

    def recent(self, limit=25):
        limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, camera_ip, track_id, vehicle_type, direction, confidence, crossed_at
                    FROM vehicle_events ORDER BY id DESC LIMIT %s
                """, (limit,))
                rows = cur.fetchall()
        for row in rows:
            if row.get("crossed_at"):
                row["crossed_at"] = row["crossed_at"].isoformat(sep=" ", timespec="seconds")
        return rows

    def recent_violations(self, limit=25):
        limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, camera_ip, violation_type, vehicle_type, vehicle_track_id, person_track_id,
                           helmet_status, plate_number, plate_confidence, detection_confidence, captured_at
                    FROM violations ORDER BY id DESC LIMIT %s
                """, (limit,))
                rows = cur.fetchall()
        for row in rows:
            if row.get("captured_at"):
                row["captured_at"] = row["captured_at"].isoformat(sep=" ", timespec="seconds")
        return rows

    def violation_image(self, violation_id, image_type="evidence"):
        column = "plate_image" if image_type == "plate" else "evidence_image"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {column} AS image FROM violations WHERE id=%s", (int(violation_id),))
                row = cur.fetchone()
        return None if not row else row.get("image")
