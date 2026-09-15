import json
import os
from datetime import datetime, timedelta

import pymysql


class TrafficStore:
    """Persistent MySQL store for traffic counts, violations and road events."""

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
        where = []
        params = []
        if event_date and str(event_date).lower() != "all":
            where.append("event_date = %s")
            params.append(event_date)
        elif not event_date:
            event_date = datetime.now().date().isoformat()
            where.append("event_date = %s")
            params.append(event_date)

        if camera_ip:
            where.append("camera_ip = %s")
            params.append(camera_ip)

        where_clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"""
            SELECT camera_ip, vehicle_type, direction, SUM(total_count) AS total
            FROM daily_vehicle_counts
            {where_clause}
            GROUP BY camera_ip, vehicle_type, direction
            ORDER BY camera_ip, vehicle_type, direction
        """
        result = {
            "date": event_date,
            "camera_ip": camera_ip,
            "totals": {},
            "summary": {
                "persons": 0,
                "vehicles": 0,
                "cars": 0,
                "motorcycles": 0,
                "buses": 0,
                "trucks": 0,
                "bicycles": 0,
                "other": 0,
                "grand_total": 0
            },
            "by_camera": {},
            "grand_total": 0
        }
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        for row in rows:
            cam = row["camera_ip"]
            v_type = str(row["vehicle_type"]).lower()
            direction = row["direction"]
            total = int(row["total"])

            cam_row = result["by_camera"].setdefault(cam, {
                "total": 0,
                "persons": 0,
                "vehicles": 0,
                "cars": 0,
                "motorcycles": 0,
                "buses": 0,
                "trucks": 0,
                "bicycles": 0,
                "other": 0,
                "types": {}
            })
            type_row = cam_row["types"].setdefault(v_type, {"total": 0, "up": 0, "down": 0, "in": 0})
            type_row["total"] += total
            type_row[direction] = type_row.get(direction, 0) + total

            cam_row["total"] += total
            result["totals"][v_type] = result["totals"].get(v_type, 0) + total
            result["grand_total"] += total

            # Categorize into standard report fields
            if v_type == 'person':
                result["summary"]["persons"] += total
                cam_row["persons"] += total
            else:
                result["summary"]["vehicles"] += total
                cam_row["vehicles"] += total
                if v_type == 'car':
                    result["summary"]["cars"] += total
                    cam_row["cars"] += total
                elif v_type == 'motorcycle':
                    result["summary"]["motorcycles"] += total
                    cam_row["motorcycles"] += total
                elif v_type == 'bus':
                    result["summary"]["buses"] += total
                    cam_row["buses"] += total
                elif v_type == 'truck':
                    result["summary"]["trucks"] += total
                    cam_row["trucks"] += total
                elif v_type == 'bicycle':
                    result["summary"]["bicycles"] += total
                    cam_row["bicycles"] += total
                else:
                    result["summary"]["other"] += total
                    cam_row["other"] += total

        result["summary"]["grand_total"] = result["grand_total"]
        return result

    def hourly_counts(self, *, event_date=None, camera_ip=None):
        where = []
        params = []
        if event_date and str(event_date).lower() != "all":
            where.append("event_date = %s")
            params.append(event_date)
        elif not event_date:
            event_date = datetime.now().date().isoformat()
            where.append("event_date = %s")
            params.append(event_date)

        if camera_ip:
            where.append("camera_ip = %s")
            params.append(camera_ip)

        where_clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"""
            SELECT HOUR(crossed_at) AS hr, vehicle_type, COUNT(*) AS count
            FROM vehicle_events
            {where_clause}
            GROUP BY hr, vehicle_type
            ORDER BY hr ASC
        """
        hourly_data = {h: {} for h in range(24)}
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        for row in rows:
            hr = int(row["hr"])
            v_type = str(row["vehicle_type"]).lower()
            cnt = int(row["count"])
            hourly_data[hr][v_type] = cnt

        return {"date": event_date, "camera_ip": camera_ip, "hourly": hourly_data}

    def report(self, *, from_date=None, to_date=None, from_time="00:00", to_time="23:59", camera_ip=None):
        today = datetime.now().date().isoformat()
        from_date = from_date or today
        to_date = to_date or from_date
        from_time = from_time or "00:00"
        to_time = to_time or "23:59"

        if len(from_time) == 5:
            from_time_sql = from_time + ":00"
        else:
            from_time_sql = from_time

        if len(to_time) == 5:
            to_time_sql = to_time + ":59"
        else:
            to_time_sql = to_time

        where = ["event_date BETWEEN %s AND %s", "TIME(crossed_at) BETWEEN %s AND %s"]
        params = [from_date, to_date, from_time_sql, to_time_sql]

        if camera_ip:
            where.append("camera_ip = %s")
            params.append(camera_ip)

        where_sql = "WHERE " + " AND ".join(where)

        sql_summary = f"""
            SELECT vehicle_type, COUNT(*) AS total
            FROM vehicle_events
            {where_sql}
            GROUP BY vehicle_type
        """

        sql_camera = f"""
            SELECT camera_ip, vehicle_type, COUNT(*) AS total
            FROM vehicle_events
            {where_sql}
            GROUP BY camera_ip, vehicle_type
            ORDER BY camera_ip, vehicle_type
        """

        sql_daily = f"""
            SELECT event_date, vehicle_type, COUNT(*) AS total
            FROM vehicle_events
            {where_sql}
            GROUP BY event_date, vehicle_type
            ORDER BY event_date DESC
        """

        sql_hourly = f"""
            SELECT event_date, HOUR(crossed_at) AS hr, vehicle_type, COUNT(*) AS total
            FROM vehicle_events
            {where_sql}
            GROUP BY event_date, hr, vehicle_type
            ORDER BY event_date DESC, hr ASC
        """

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_summary, params)
                summary_rows = cur.fetchall()

                cur.execute(sql_camera, params)
                camera_rows = cur.fetchall()

                cur.execute(sql_daily, params)
                daily_rows = cur.fetchall()

                cur.execute(sql_hourly, params)
                hourly_rows = cur.fetchall()

        summary = {
            "person": 0,
            "car": 0,
            "motorcycle": 0,
            "bus": 0,
            "truck": 0,
            "bicycle": 0,
            "other": 0,
            "vehicles": 0,
            "total": 0,
            "grand_total": 0,
        }

        for r in summary_rows:
            vt = str(r["vehicle_type"]).lower()
            cnt = int(r["total"])
            summary["grand_total"] += cnt
            if vt == "person":
                summary["person"] += cnt
            else:
                summary["vehicles"] += cnt
                summary["total"] += cnt
                if vt in summary:
                    summary[vt] += cnt
                else:
                    summary["other"] += cnt

        cam_dict = {}
        for r in camera_rows:
            ip = r["camera_ip"]
            vt = str(r["vehicle_type"]).lower()
            cnt = int(r["total"])
            c_row = cam_dict.setdefault(ip, {
                "camera_ip": ip,
                "person": 0,
                "car": 0,
                "motorcycle": 0,
                "bus": 0,
                "truck": 0,
                "bicycle": 0,
                "other": 0,
                "total": 0,
                "grand_total": 0,
            })
            c_row["grand_total"] += cnt
            if vt == "person":
                c_row["person"] += cnt
            else:
                c_row["total"] += cnt
                if vt in c_row:
                    c_row[vt] += cnt
                else:
                    c_row["other"] += cnt

        by_camera = list(cam_dict.values())

        daily_dict = {}
        for r in daily_rows:
            d_str = str(r["event_date"])
            vt = str(r["vehicle_type"]).lower()
            cnt = int(r["total"])
            d_row = daily_dict.setdefault(d_str, {
                "date": d_str,
                "person": 0,
                "car": 0,
                "motorcycle": 0,
                "bus": 0,
                "truck": 0,
                "bicycle": 0,
                "other": 0,
                "total": 0,
                "grand_total": 0,
            })
            d_row["grand_total"] += cnt
            if vt == "person":
                d_row["person"] += cnt
            else:
                d_row["total"] += cnt
                if vt in d_row:
                    d_row[vt] += cnt
                else:
                    d_row["other"] += cnt

        daily = list(daily_dict.values())

        hourly_dict = {}
        for r in hourly_rows:
            d_str = str(r["event_date"])
            hr = int(r["hr"])
            hr_str = f"{hr:02d}:00 - {hr:02d}:59"
            key = (d_str, hr_str)
            vt = str(r["vehicle_type"]).lower()
            cnt = int(r["total"])

            h_row = hourly_dict.setdefault(key, {
                "date": d_str,
                "hour": hr_str,
                "person": 0,
                "car": 0,
                "motorcycle": 0,
                "bus": 0,
                "truck": 0,
                "bicycle": 0,
                "other": 0,
                "total": 0,
                "grand_total": 0,
            })
            h_row["grand_total"] += cnt
            if vt == "person":
                h_row["person"] += cnt
            else:
                h_row["total"] += cnt
                if vt in h_row:
                    h_row[vt] += cnt
                else:
                    h_row["other"] += cnt

        hourly = list(hourly_dict.values())

        return {
            "from_date": from_date,
            "to_date": to_date,
            "from_time": from_time,
            "to_time": to_time,
            "camera_ip": camera_ip,
            "summary": summary,
            "by_camera": by_camera,
            "daily": daily,
            "hourly": hourly,
        }

    def no_helmet_count(self, event_date=None):
        event_date = event_date or datetime.now().date().isoformat()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS total FROM violations WHERE event_date=%s AND violation_type='no_helmet'", (event_date,))
                row = cur.fetchone()
        return int(row["total"]) if row else 0

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
