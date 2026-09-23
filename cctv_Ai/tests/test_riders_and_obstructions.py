import ast
import logging
import sys
import unittest
from collections import defaultdict, deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault('cv2', SimpleNamespace())
from accuracy_detector import AccuracyDetector
from road_worker import RoadWorker


class RiderTests(unittest.TestCase):
    def detector(self):
        d = AccuracyDetector.__new__(AccuracyDetector)
        d.cfg = SimpleNamespace(HELMET_CONFIRM_FRAMES=2, HELMET_CONFIDENCE=.25)
        d.helmet_votes = defaultdict(lambda: deque(maxlen=4))
        d.helmet_confirm_frames = 2
        d.strict_no_helmet_vote_ratio = .5
        d.no_helmet_final_avg_confidence = .2
        d.log = logging.getLogger('test')
        return d

    def test_rider_and_passenger_votes_do_not_cancel_each_other(self):
        d = self.detector()
        cam, bike = {'camera_key': 'c'}, {'track_id': 1}
        driver, passenger = {'track_id': 2}, {'track_id': 3}
        helmet = {'status': 'helmet', 'confidence': .9}
        bare = {'status': 'no_helmet', 'confidence': .85}
        for _ in range(2):
            a = d._helmet_confirmed(cam, bike, helmet, driver)
            b = d._helmet_confirmed(cam, bike, bare, passenger)
        self.assertEqual(a[0], 'helmet')
        self.assertEqual(b[0], 'no_helmet')

    def test_confirmed_passenger_is_saved_without_plate_model(self):
        d = self.detector()
        d.cfg.ADVANCED_DETECTION_ENABLED = True
        d.cfg.ADVANCED_EVERY_N_FRAMES = 1
        d.cfg.VIOLATION_COOLDOWN_SECONDS = 90
        d.models = {}
        bike = {"box": [100, 100, 200, 200], "track_id": None}
        people = [{"track_id": 2, "box": [105, 50, 145, 160]},
                  {"track_id": 3, "box": [150, 50, 190, 160]}]
        d._primary_objects = Mock(return_value=(people, [bike]))
        d._primary_vehicles = Mock(return_value=[bike])
        d._helmet_status = lambda c, f, b, person: {
            "status": "helmet" if person["track_id"] == 2 else "no_helmet",
            "confidence": .9, "box": person["box"], "search_box": person["box"]}
        d._draw = Mock()
        d._crop = Mock(return_value='evidence')
        d._jpeg = Mock(return_value=b'jpg')
        d.store = SimpleNamespace(record_violation=Mock(return_value=42))
        d.last_violation = defaultdict(float)
        d.session_id = 'test'
        args = ({"camera_key": "c"}, 'frame', object(), None)
        first = d.process(*args, 1, draw_frame='draw')
        second = d.process(*args, 2, draw_frame='draw')
        third = d.process(*args, 3, draw_frame='draw')
        self.assertEqual(first['helmet_violations'], 0)
        self.assertEqual(second['helmet_violations'], 1)
        self.assertEqual(third['helmet_violations'], 0)
        d.store.record_violation.assert_called_once()
        row = d.store.record_violation.call_args.kwargs
        self.assertEqual(row['person_track_id'], 3)
        self.assertEqual(row['violation_type'], 'no_helmet')
        self.assertEqual(row['evidence_image'], b'jpg')
        self.assertIsNone(row['plate_number'])

    def test_two_riders_checked_once_and_walking_person_excluded(self):
        d = self.detector()
        bikes = [{'box': [100, 100, 200, 200]}, {'box': [400, 100, 500, 200]}]
        persons = [{'box': [105, 50, 145, 160]}, {'box': [150, 50, 190, 160]},
                   {'box': [900, 30, 950, 180]}]
        pairs = d._rider_pairs(persons, bikes)
        self.assertEqual(pairs, [(bikes[0], persons[0]), (bikes[0], persons[1]), (bikes[1], None)])

    def test_other_passengers_head_not_attributed_to_driver(self):
        self.assertFalse(AccuracyDetector._is_head_in_bounds([170, 40, 190, 70], [100, 100, 200, 200], [105, 50, 145, 160]))

    def test_incident_insert_failure_does_not_start_cooldown(self):
        tree = ast.parse((ROOT / 'incident_detector.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_store')
        ns = {'time': SimpleNamespace(time=lambda: 1000)}
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<store>', 'exec'), ns)
        d = SimpleNamespace(store=SimpleNamespace(record_incident=Mock(side_effect=[None, 8])),
            session_id='s', cfg=SimpleNamespace(INCIDENT_COOLDOWN_SECONDS=120),
            last_incident=defaultdict(float), _jpeg=lambda f: b'jpg', log=logging.getLogger('test'))
        args = (d, {'camera_key': 'c'}, 'frame', 'road_obstruction', 'High', .7, {})
        self.assertIsNone(ns['_store'](*args))
        self.assertEqual(ns['_store'](*args), 8)
        self.assertIsNone(ns['_store'](*args))

    def test_road_queue_bounded_and_replaces_old_camera_frame(self):
        with patch('road_worker.threading.Thread'):
            worker = RoadWorker(Mock(), Mock(), Mock(), limit=2)
        worker.submit({'camera_key': 'a'}, 'old')
        worker.submit({'camera_key': 'b'}, 'b')
        worker.submit({'camera_key': 'a'}, 'latest')
        self.assertEqual(list(worker.pending), ['a', 'b'])
        self.assertEqual(worker.pending['a'][1], 'latest')
        worker.submit({'camera_key': 'c'}, 'c')
        self.assertEqual(list(worker.pending), ['b', 'c'])

if __name__ == '__main__':
    unittest.main()
