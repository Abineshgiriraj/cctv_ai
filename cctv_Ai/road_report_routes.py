import os
from datetime import datetime

from flask import Response, jsonify, request


def damage_level(model_label):
    label = str(model_label or '').strip().lower()
    if 'pothole' in label or 'alligator' in label:
        return 'High'
    if 'longitudinal' in label or 'transverse' in label or 'crack' in label:
        return 'Medium'
    return 'Low'


def _image_bytes(value):
    if not value:
        return None
    if isinstance(value, bytes) and value.startswith(b'\xff\xd8\xff'):
        return value
    if isinstance(value, bytes):
        try:
            value = value.decode('utf-8')
        except Exception:
            return None
    if isinstance(value, str) and os.path.isfile(value):
        with open(value, 'rb') as handle:
            return handle.read()
    return None


def register_road_report_routes(app, store, allowed_keys, log):
    def get_store():
        return store() if callable(store) else store
    @app.route('/analytics/road_report')
    def road_report():
        db = get_store()
        if db is None:
            return jsonify({'ok': False, 'error': 'MySQL analytics store is unavailable'}), 503

        today = datetime.now().date().isoformat()
        from_date = (request.args.get('from_date') or today).strip()
        to_date = (request.args.get('to_date') or from_date).strip()
        camera_key = (request.args.get('camera_key') or request.args.get('camera_ip') or '').strip() or None
        label = (request.args.get('label') or '').strip() or None
        try:
            limit = max(1, min(int(request.args.get('limit', 250)), 1000))
        except ValueError:
            limit = 250

        if camera_key and camera_key not in allowed_keys():
            return jsonify({'ok': False, 'error': 'Camera not configured'}), 404

        # Existing databases keep the unique camera key in camera_ip for backward
        # compatibility. Alias it back to camera_key in the API response.
        where = ['event_date BETWEEN %s AND %s', "event_type='road_damage'"]
        params = [from_date, to_date]
        if camera_key:
            where.append('camera_ip=%s')
            params.append(camera_key)
        if label:
            where.append('model_label=%s')
            params.append(label)
        params.append(limit)

        sql = f"""
            SELECT id, camera_ip AS camera_key, event_type, model_label, confidence,
                   captured_at, event_date, metadata_json
            FROM road_events
            WHERE {' AND '.join(where)}
            ORDER BY captured_at DESC
            LIMIT %s
        """

        try:
            with db._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    rows = cur.fetchall()
        except Exception as exc:
            log.exception('Road report query failed: %s', exc)
            return jsonify({'ok': False, 'error': str(exc)}), 500

        high = medium = low = 0
        camera_set = set()
        events = []
        for row in rows:
            level = damage_level(row.get('model_label'))
            if level == 'High':
                high += 1
            elif level == 'Medium':
                medium += 1
            else:
                low += 1
            key = row.get('camera_key')
            camera_set.add(key)
            captured = row.get('captured_at')
            events.append({
                'id': int(row['id']),
                'camera_key': key,
                'camera_ip': key,
                'event_type': row.get('event_type'),
                'model_label': row.get('model_label'),
                'damage_level': level,
                'confidence': float(row.get('confidence') or 0),
                'captured_at': captured.isoformat(sep=' ', timespec='seconds') if captured else None,
                'event_date': str(row.get('event_date') or ''),
            })

        return jsonify({
            'ok': True,
            'from_date': from_date,
            'to_date': to_date,
            'camera_key': camera_key,
            'label': label,
            'summary': {
                'total': len(events),
                'high': high,
                'medium': medium,
                'low': low,
                'cameras': len(camera_set),
            },
            'events': events,
        })

    @app.route('/analytics/road_event_image/<int:event_id>')
    def road_event_image(event_id):
        db = get_store()
        if db is None:
            return jsonify({'ok': False, 'error': 'MySQL analytics store is unavailable'}), 503
        try:
            with db._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT evidence_image FROM road_events WHERE id=%s', (event_id,))
                    row = cur.fetchone()
        except Exception as exc:
            log.exception('Road evidence query failed: %s', exc)
            return jsonify({'ok': False, 'error': str(exc)}), 500

        image = _image_bytes(row.get('evidence_image') if row else None)
        if not image:
            return jsonify({'ok': False, 'error': 'Image not found'}), 404
        return Response(image, mimetype='image/jpeg', headers={'Cache-Control': 'no-store'})
