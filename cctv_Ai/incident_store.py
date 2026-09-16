import json
import os
from datetime import datetime


class IncidentStore:
    """Small incident store layered on the existing MySQL TrafficStore connection."""

    def __init__(self, traffic_store, log):
        self.traffic_store = traffic_store
        self.log = log
        self.available = traffic_store is not None
        if self.available:
            self._initialize()

    def _connect(self):
        return self.traffic_store._connect()

    def _initialize(self):
        sql = """
        CREATE TABLE IF NOT EXISTS incident_events (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            session_id VARCHAR(32) NOT NULL,
            camera_ip VARCHAR(128) NOT NULL,
            incident_type VARCHAR(64) NOT NULL,
            severity VARCHAR(16) NOT NULL DEFAULT 'Medium',
            confidence DECIMAL(6,5) NOT NULL DEFAULT 0,
            captured_at DATETIME NOT NULL,
            event_date DATE NOT NULL,
            evidence_image LONGBLOB NULL,
            metadata_json JSON NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY idx_incident_date (event_date, camera_ip, incident_type),
            KEY idx_incident_captured (captured_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                try:
                    cur.execute("ALTER TABLE incident_events MODIFY camera_ip VARCHAR(128) NOT NULL")
                except Exception:
                    pass
            conn.commit()

    @staticmethod
    def _camera_key(camera):
        if isinstance(camera, dict):
            return str(camera.get("camera_key") or camera.get("camera_ip") or "")
        return str(camera or "")

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

    def _save_image(self, prefix, image_bytes):
        if not image_bytes:
            return None
        if hasattr(self.traffic_store, "_save_image_to_disk"):
            return self.traffic_store._save_image_to_disk(prefix, image_bytes)
        if not isinstance(image_bytes, bytes):
            return image_bytes
        import time
        import uuid
        os.makedirs("data/evidence", exist_ok=True)
        path = f"data/evidence/{prefix}_{uuid.uuid4().hex[:8]}_{int(time.time())}.jpg"
        with open(path, "wb") as handle:
            handle.write(image_bytes)
        return path

    def record(self, *, session_id, camera, incident_type, severity="Medium",
               confidence=0.0, evidence_image=None, metadata=None):
        if not self.available:
            return None
        camera_key = self._camera_key(camera)
        if not camera_key:
            return None
        now = datetime.now()
        evidence_image = self._save_image("incident", evidence_image)
        payload = dict(metadata or {})
        payload.update({k: v for k, v in self._camera_metadata(camera).items() if v is not None})
        sql = """
        INSERT INTO incident_events
        (session_id, camera_ip, incident_type, severity, confidence, captured_at,
         event_date, evidence_image, metadata_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    session_id, camera_key, incident_type, severity,
                    float(confidence or 0.0), now, now.date(), evidence_image,
                    json.dumps(payload, ensure_ascii=False),
                ))
                event_id = cur.lastrowid
            conn.commit()
        return event_id

    def report(self, *, from_date, to_date, camera_key=None, incident_type=None, limit=300):
        where = ["event_date BETWEEN %s AND %s"]
        params = [from_date, to_date]
        if camera_key:
            where.append("camera_ip=%s")
            params.append(camera_key)
        if incident_type:
            where.append("incident_type=%s")
            params.append(incident_type)
        params.append(max(1, min(int(limit), 1000)))
        sql = f"""
        SELECT id, camera_ip AS camera_key, incident_type, severity, confidence,
               captured_at, event_date, metadata_json
        FROM incident_events
        WHERE {' AND '.join(where)}
        ORDER BY captured_at DESC
        LIMIT %s
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        for row in rows:
            if row.get("captured_at"):
                row["captured_at"] = row["captured_at"].isoformat(sep=" ", timespec="seconds")
            meta = row.get("metadata_json")
            if isinstance(meta, str):
                try:
                    row["metadata"] = json.loads(meta)
                except Exception:
                    row["metadata"] = {}
            elif isinstance(meta, dict):
                row["metadata"] = meta
            else:
                row["metadata"] = {}
            row.pop("metadata_json", None)
        return rows

    def image(self, event_id):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT evidence_image FROM incident_events WHERE id=%s", (int(event_id),))
                row = cur.fetchone()
        if not row or not row.get("evidence_image"):
            return None
        image = row["evidence_image"]
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
