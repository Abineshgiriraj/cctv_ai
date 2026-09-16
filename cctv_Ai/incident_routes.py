from datetime import datetime

from flask import Response, jsonify, request


def register_incident_routes(app, incident_store, traffic_store, allowed_keys, log):
    @app.route('/analytics/incident_report')
    def incident_report():
        if incident_store is None or not getattr(incident_store, 'available', False):
            return jsonify({'ok': False, 'error': 'Incident store is unavailable'}), 503

        today = datetime.now().date().isoformat()
        from_date = (request.args.get('from_date') or today).strip()
        to_date = (request.args.get('to_date') or from_date).strip()
        camera_key = (request.args.get('camera_key') or request.args.get('camera_ip') or '').strip() or None
        incident_type = (request.args.get('incident_type') or '').strip() or None
        try:
            limit = max(1, min(int(request.args.get('limit', 300)), 1000))
        except ValueError:
            limit = 300

        if camera_key and camera_key not in allowed_keys():
            return jsonify({'ok': False, 'error': 'Camera not configured'}), 404

        try:
            events = incident_store.report(
                from_date=from_date,
                to_date=to_date,
                camera_key=camera_key,
                incident_type=incident_type,
                limit=limit,
            )

            # Merge model-based road obstruction rows already produced by the
            # existing advanced detector so both obstruction methods appear on one page.
            if traffic_store is not None and incident_type in (None, 'road_obstruction'):
                where = ["event_date BETWEEN %s AND %s", "event_type='road_obstruction'"]
                params = [from_date, to_date]
                if camera_key:
                    where.append('camera_ip=%s')
                    params.append(camera_key)
                params.append(limit)
                sql = f"""
                    SELECT id, camera_ip AS camera_key, model_label, confidence,
                           captured_at, event_date, metadata_json
                    FROM road_events
                    WHERE {' AND '.join(where)}
                    ORDER BY captured_at DESC
                    LIMIT %s
                """
                with traffic_store._connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(sql, params)
                        rows = cur.fetchall()
                for row in rows:
                    captured = row.get('captured_at')
                    events.append({
                        'id': int(row['id']),
                        'camera_key': row.get('camera_key'),
                        'incident_type': 'road_obstruction',
                        'severity': 'High' if float(row.get('confidence') or 0) >= 0.70 else 'Medium',
                        'confidence': float(row.get('confidence') or 0),
                        'captured_at': captured.isoformat(sep=' ', timespec='seconds') if captured else None,
                        'event_date': str(row.get('event_date') or ''),
                        'metadata': {
                            'source': 'road_obstruction_model',
                            'model_label': row.get('model_label'),
                        },
                        'image_source': 'road',
                    })

            for event in events:
                event.setdefault('image_source', 'incident')
            events.sort(key=lambda row: row.get('captured_at') or '', reverse=True)
            events = events[:limit]

            summary = {
                'total': len(events),
                'accidents': sum(1 for row in events if row.get('incident_type') == 'accident'),
                'road_obstructions': sum(1 for row in events if row.get('incident_type') == 'road_obstruction'),
                'high': sum(1 for row in events if str(row.get('severity')).lower() == 'high'),
                'cameras': len({row.get('camera_key') for row in events if row.get('camera_key')}),
            }
            return jsonify({
                'ok': True,
                'from_date': from_date,
                'to_date': to_date,
                'camera_key': camera_key,
                'incident_type': incident_type,
                'summary': summary,
                'events': events,
            })
        except Exception as exc:
            log.exception('Incident report failed: %s', exc)
            return jsonify({'ok': False, 'error': str(exc)}), 500

    @app.route('/analytics/incident_image/<int:event_id>')
    def incident_image(event_id):
        if incident_store is None or not getattr(incident_store, 'available', False):
            return jsonify({'ok': False, 'error': 'Incident store is unavailable'}), 503
        image = incident_store.image(event_id)
        if not image:
            return jsonify({'ok': False, 'error': 'Image not found'}), 404
        return Response(image, mimetype='image/jpeg', headers={'Cache-Control': 'no-store'})
