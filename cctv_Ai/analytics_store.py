import json
import os
from datetime import datetime

import pymysql


class TrafficStore:
    """Persistent MySQL store for traffic counts, violations and road events.

    Existing installations already use a `camera_ip` column. To remain backward
    compatible while supporting multiple channels on one recorder, new records store
    the unique camera key (for example `192.168.0.249_ch3`) in that column. The
    public API exposes the value as `camera_key` as well.
    """

    def __init__(self, _legacy_path=None, host=None, port=None, user=None, password=None, database=None):
        self.host = host or os.getenv("DB_HOST", "127.0.0.1")
        self.port = int(port or os.getenv("DB_PORT", 3306))
        self.user = user or os.getenv("DB_USER", "root")
        self.password = password if password is not None else os.getenv("DB_PASSWORD", "")
        self.database = database or os.getenv("DB_NAME", "cctv_ai")
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

    @staticmethod
    def _camera_key(camera=None, camera_ip=None):
        if isinstance(camera, dict):
            return str(camera.get("camera_key") or camera.get("camera_ip") or camera_ip or "")
        if camera:
            return str(camera)
        return str(camera_ip or "")

    @staticmethod
    def _camera_metadata(camera):
        if not isinstance(camera, dict):
            return {}
        return {
            "camera_key": camera.get("camera_key"),
            "camera_ip": camera.get("camera_ip"),
            "channel_no": camera.get("channel_no"),
            "camera_name": camera.get("camera_name"),
            "area_name": camera.get("area_name"),
        }

    def _initialize(self):
        statements = [
            """
            CREATE TABLE IF NOT EXISTS vehicle_events (
                id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(32) NOT NULL,
                camera_ip VARCHAR(128) NOT NULL,
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
                camera_ip VARCHAR(128) NOT NULL,
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
                camera_ip VARCHAR(128) NOT NULL,
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
                camera_ip VARCHAR(128) NOT NULL,
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
                # Older schemas used VARCHAR(45), which is too short for some
                # descriptive camera keys. Expanding is safe and preserves data.
                for table in ("vehicle_events", "daily_vehicle_counts", "violations", "road_events"):
                    try:
                        cur.execute(f"ALTER TABLE {table} MODIFY camera_ip VARCHAR(128) NOT NULL")
                    except Exception:
                        pass
            conn.commit()

    def _save_image_to_disk(self, prefix, image_bytes):
        if not image_bytes or not isinstance(image_bytes, bytes):
            return image_bytes
        if not image_bytes.startswith(b"\xff\xd8\xff"):
            return image_bytes
        import time
        import uuid
        os.makedirs("data/evidence", exist_ok=True)
        filename = f"data/evidence/{prefix}_{uuid.uuid4().hex[:8]}_{int(time.time())}.jpg"
        with open(filename, "wb") as handle:
            handle.write(image_bytes)
        return filename

    def record_vehicle(self, *, session_id, track_id, vehicle_type, direction, confidence,
                       camera=None, camera_ip=None):
        camera_key = self._camera_key(camera, camera_ip)
        if not camera_key:
            raise ValueError("camera key is required")
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
                    cur.execute(sql_event, (
                        session_id, camera_key, int(track_id), vehicle_type.lower(), direction,
                        float(confidence), now, now.date(),
                    ))
                    inserted = cur.rowcount > 0
                    if inserted:
                        cur.execute(sql_count, (now.date(), camera_key, vehicle_type.lower(), direction))
                conn.commit()
                return inserted
            except Exception:
                conn.rollback()
                raise

    def record_violation(self, *, session_id, violation_type, vehicle_type=None,
                         vehicle_track_id=None, person_track_id=None, helmet_status=None,
                         plate_number=None, plate_confidence=None, detection_confidence=0,
                         evidence_image=None, plate_image=None, metadata=None,
                         camera=None, camera_ip=None):
        camera_key = self._camera_key(camera, camera_ip)
        if not camera_key:
            raise ValueError("camera key is required")
        evidence_image = self._save_image_to_disk("violation", evidence_image)
        plate_image = self._save_image_to_disk("plate", plate_image)
        merged_metadata = dict(metadata or {})
        merged_metadata.update({k: v for k, v in self._camera_metadata(camera).items() if v is not None})
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
                cur.execute(sql, (
                    session_id, camera_key, violation_type, vehicle_type, vehicle_track_id,
                    person_track_id, helmet_status, plate_number, plate_confidence,
                    float(detection_confidence or 0), now, now.date(), evidence_image,
                    plate_image, json.dumps(merged_metadata, ensure_ascii=False),
                ))
                row_id = cur.lastrowid
            conn.commit()
            return row_id

    def record_road_event(self, *, session_id, event_type, model_label, confidence,
                          evidence_image=None, metadata=None, camera=None, camera_ip=None):
        camera_key = self._camera_key(camera, camera_ip)
        if not camera_key:
            raise ValueError("camera key is required")
        evidence_image = self._save_image_to_disk("road", evidence_image)
        merged_metadata = dict(metadata or {})
        merged_metadata.update({k: v for k, v in self._camera_metadata(camera).items() if v is not None})
        now = datetime.now()
        sql = """
            INSERT INTO road_events
            (session_id, camera_ip, event_type, model_label, confidence, captured_at,
             event_date, evidence_image, metadata_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    session_id, camera_key, event_type, model_label, float(confidence),
                    now, now.date(), evidence_image,
                    json.dumps(merged_metadata, ensure_ascii=False),
                ))
                row_id = cur.lastrowid
            conn.commit()
            return row_id

    def counts(self, *, event_date=None, camera_key=None, camera_ip=None):
        camera_key = camera_key or camera_ip
        event_date = event_date or datetime.now().date().isoformat()
        where = ["event_date = %s"]
        params = [event_date]
        if camera_key:
            where.append("camera_ip = %s")
            params.append(camera_key)
        sql = f"""
            SELECT camera_ip AS camera_key, vehicle_type, direction, SUM(total_count) AS total
            FROM daily_vehicle_counts
            WHERE {' AND '.join(where)}
            GROUP BY camera_ip, vehicle_type, direction
            ORDER BY camera_ip, vehicle_type, direction
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        result = {"date": event_date, "camera_key": camera_key, "totals": {}, "by_camera": {}, "grand_total": 0}
        for row in rows:
            key = row["camera_key"]
            vehicle_type = str(row["vehicle_type"]).lower()
            total = int(row["total"] or 0)
            result["totals"][vehicle_type] = result["totals"].get(vehicle_type, 0) + total
            result["grand_total"] += total
            cam = result["by_camera"].setdefault(key, {"camera_key": key, "camera_ip": key, "total": 0, "types": {}})
            cam["total"] += total
            cam["types"][vehicle_type] = cam["types"].get(vehicle_type, 0) + total
        return result

    def hourly_counts(self, *, event_date=None, camera_key=None, camera_ip=None):
        camera_key = camera_key or camera_ip
        event_date = event_date or datetime.now().date().isoformat()
        where = ["event_date = %s"]
        params = [event_date]
        if camera_key:
            where.append("camera_ip = %s")
            params.append(camera_key)
        sql = f"""
            SELECT HOUR(crossed_at) AS hr, vehicle_type, COUNT(*) AS count
            FROM vehicle_events
            WHERE {' AND '.join(where)}
            GROUP BY hr, vehicle_type
            ORDER BY hr
        """
        hourly = {h: {} for h in range(24)}
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        for row in rows:
            hourly[int(row["hr"])][str(row["vehicle_type"]).lower()] = int(row["count"])
        return {"date": event_date, "camera_key": camera_key, "hourly": hourly}

    @staticmethod
    def _empty_report_row(**extra):
        return {
            **extra,
            "person": 0,
            "car": 0,
            "motorcycle": 0,
            "bus": 0,
            "truck": 0,
            "bicycle": 0,
            "other": 0,
            "total": 0,
            "grand_total": 0,
        }

    @classmethod
    def _add_type(cls, row, vehicle_type, count):
        vehicle_type = str(vehicle_type).lower()
        count = int(count or 0)
        row["grand_total"] += count
        if vehicle_type == "person":
            row["person"] += count
        else:
            row["total"] += count
            if vehicle_type in row:
                row[vehicle_type] += count
            else:
                row["other"] += count

    def report(self, *, from_date=None, to_date=None, from_time="00:00", to_time="23:59",
               camera_key=None, camera_ip=None):
        camera_key = camera_key or camera_ip
        today = datetime.now().date().isoformat()
        from_date = from_date or today
        to_date = to_date or from_date
        from_time_sql = (from_time or "00:00") + (":00" if len(from_time or "00:00") == 5 else "")
        to_time_sql = (to_time or "23:59") + (":59" if len(to_time or "23:59") == 5 else "")
        where = ["event_date BETWEEN %s AND %s", "TIME(crossed_at) BETWEEN %s AND %s"]
        params = [from_date, to_date, from_time_sql, to_time_sql]
        if camera_key:
            where.append("camera_ip = %s")
            params.append(camera_key)
        where_sql = "WHERE " + " AND ".join(where)
        queries = {
            "summary": f"SELECT vehicle_type, COUNT(*) total FROM vehicle_events {where_sql} GROUP BY vehicle_type",
            "camera": f"SELECT camera_ip AS camera_key, vehicle_type, COUNT(*) total FROM vehicle_events {where_sql} GROUP BY camera_ip, vehicle_type ORDER BY camera_ip, vehicle_type",
            "daily": f"SELECT event_date, vehicle_type, COUNT(*) total FROM vehicle_events {where_sql} GROUP BY event_date, vehicle_type ORDER BY event_date DESC",
            "hourly": f"SELECT event_date, HOUR(crossed_at) hr, vehicle_type, COUNT(*) total FROM vehicle_events {where_sql} GROUP BY event_date, hr, vehicle_type ORDER BY event_date DESC, hr",
        }
        with self._connect() as conn:
            with conn.cursor() as cur:
                results = {}
                for name, sql in queries.items():
                    cur.execute(sql, params)
                    results[name] = cur.fetchall()
        summary = self._empty_report_row()
        for row in results["summary"]:
            self._add_type(summary, row["vehicle_type"], row["total"])
        by_camera_map = {}
        for row in results["camera"]:
            key = row["camera_key"]
            target = by_camera_map.setdefault(key, self._empty_report_row(camera_key=key, camera_ip=key))
            self._add_type(target, row["vehicle_type"], row["total"])
        daily_map = {}
        for row in results["daily"]:
            day = str(row["event_date"])
            target = daily_map.setdefault(day, self._empty_report_row(date=day))
            self._add_type(target, row["vehicle_type"], row["total"])
        hourly_map = {}
        for row in results["hourly"]:
            day = str(row["event_date"])
            hour = int(row["hr"])
            label = f"{hour:02d}:00 - {hour:02d}:59"
            target = hourly_map.setdefault((day, hour), self._empty_report_row(date=day, hour=label))
            self._add_type(target, row["vehicle_type"], row["total"])
        return {
            "from_date": from_date,
            "to_date": to_date,
            "from_time": from_time,
            "to_time": to_time,
            "camera_key": camera_key,
            "summary": summary,
            "by_camera": list(by_camera_map.values()),
            "daily": list(daily_map.values()),
            "hourly": list(hourly_map.values()),
        }

    def no_helmet_count(self, event_date=None):
        event_date = event_date or datetime.now().date().isoformat()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) total FROM violations WHERE event_date=%s AND violation_type='no_helmet'", (event_date,))
                row = cur.fetchone()
        return int((row or {}).get("total") or 0)

    def recent(self, limit=25):
        limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, camera_ip AS camera_key, camera_ip, track_id, vehicle_type,
                           direction, confidence, crossed_at
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
                    SELECT id, camera_ip AS camera_key, camera_ip, violation_type, vehicle_type,
                           vehicle_track_id, person_track_id, helmet_status, plate_number,
                           plate_confidence, detection_confidence, captured_at, metadata_json
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
        if not row or not row.get("image"):
            return None
        image = row["image"]
        if isinstance(image, bytes) and image.startswith(b"\xff\xd8\xff"):
            return image
        if isinstance(image, bytes):
            try:
                image = image.decode("utf-8")
            except Exception:
                return None
        if isinstance(image, str) and os.path.isfile(image):
            with open(image, "rb") as handle:
                return handle.read()
        return None
