"""Pipeline regressions; no camera, model downloads, or MySQL required."""
import ast
import logging
import sys
import threading
import unittest
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# These tests exercise orchestration, not OpenCV inference.
sys.modules.setdefault('cv2', SimpleNamespace())
from advanced_detection import AdvancedDetector
from accuracy_detector import AccuracyDetector

ROOT = Path(__file__).resolve().parents[1]


def server_function(name, namespace):
    tree = ast.parse((ROOT / 'stream_server_mysql.py').read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT / 'stream_server_mysql.py'), 'exec'), namespace)
    return namespace[name]


class Frame:
    def __init__(self, marks=None):
        self.marks = list(marks or [])

    def copy(self):
        return Frame(self.marks)


class HelmetPipelineTests(unittest.TestCase):
    def test_model_labels(self):
        for label in ('no helmet', 'no-helmet', 'without helmet', 'head', 'bare_head', 'no hard hat'):
            self.assertEqual(AdvancedDetector._helmet_label(label), 'no_helmet')
        self.assertEqual(AdvancedDetector._helmet_label('helmet'), 'helmet')
        for label in ('person', 'motorcycle', '0'):
            self.assertIsNone(AdvancedDetector._helmet_label(label))

    def make_detector(self, store):
        detector = AdvancedDetector.__new__(AdvancedDetector)
        detector.store = store
        detector.cfg = SimpleNamespace(VIOLATION_COOLDOWN_SECONDS=60)
        detector.session_id = 'test'
        detector.last_violation = defaultdict(float)
        detector._crop = Mock(return_value='evidence')
        detector._jpeg = Mock(return_value=b'jpg')
        return detector

    def save(self, detector, box=None):
        return detector._store_no_helmet(
            {'camera_key': 'cam:1'}, 'frame',
            {'box': box or [100, 100, 200, 200], 'conf': .8, 'track_id': None},
            None, ('no_helmet', .8, 2, 2), [80, 50, 220, 220], None)

    def test_failed_save_retries_then_success_enters_cooldown(self):
        store = SimpleNamespace(record_violation=Mock(side_effect=[RuntimeError('DB offline'), 42]))
        detector = self.make_detector(store)
        with self.assertRaises(RuntimeError):
            self.save(detector)
        self.assertEqual(self.save(detector), 42)
        self.assertIsNone(self.save(detector))
        self.assertEqual(store.record_violation.call_count, 2)
        self.assertIsNone(store.record_violation.call_args.kwargs['plate_number'])
        self.assertEqual(store.record_violation.call_args.kwargs['violation_type'], 'no_helmet')

    def test_untracked_bikes_do_not_share_camera_wide_cooldown(self):
        store = SimpleNamespace(record_violation=Mock(return_value=42))
        detector = self.make_detector(store)
        self.save(detector)
        self.save(detector, [500, 100, 600, 200])
        self.assertEqual(store.record_violation.call_count, 2)

    def test_clean_frames_queued_before_primary_drawing(self):
        tasks = []
        def annotate(camera, frame, *args):
            frame.marks.append('vehicle annotations')
            return 1, 1, {}
        ns = {'Config': SimpleNamespace(ADVANCED_DETECTION_ENABLED=True,
              ADVANCED_EVERY_N_FRAMES=1, ROAD_EVERY_N_FRAMES=4,
              INCIDENT_DETECTION_ENABLED=True, INCIDENT_EVERY_N_FRAMES=1),
              '_original_annotate': annotate, 'time': SimpleNamespace(time=lambda: 100),
              '_advanced_latest_tasks': {}, '_incident_latest_tasks': {},
              '_put_latest_task': lambda target, task: tasks.append(task)}
        fn = server_function('_annotate_with_advanced', ns)
        frame = Frame()
        fn({'camera_key': 'cam:1'}, frame, None, None, {}, {}, 1, 5, {})
        self.assertEqual(frame.marks, ['vehicle annotations'])
        self.assertEqual(len(tasks), 2)
        for task in tasks:
            self.assertEqual(task['frame'].marks, [])
            self.assertEqual(task['captured_at'], 100)
        self.assertIsNot(tasks[0]['frame'], tasks[1]['frame'])

    def test_refreshing_pending_task_preserves_queue_age(self):
        ns = {'_analysis_task_condition': threading.Condition()}
        fn = server_function('_put_latest_task', ns)
        target = {}
        fn(target, {'camera': {'camera_key': 'cam:1'}, 'queued_at': 10, 'frame': 'old'})
        fn(target, {'camera': {'camera_key': 'cam:1'}, 'queued_at': 20, 'frame': 'new'})
        self.assertEqual(target['cam:1']['queued_at'], 10)
        self.assertEqual(target['cam:1']['frame'], 'new')

    def test_road_only_pass_does_not_clear_helmet_overlay(self):
        for summary, expected in [({'helmet_ran': False}, False),
                                  ({'helmet_ran': True, 'helmet_live': []}, True)]:
            base = SimpleNamespace(set_ai_status=Mock())
            task = {'camera': {'camera_key': 'cam:1'}, 'frame': Frame(), 'result': None,
                    'model': None, 'processed_index': 4, 'captured_at': 100}
            ns = {'_pop_latest_task': Mock(side_effect=[task, KeyboardInterrupt]),
                  '_advanced_latest_tasks': {}, '_ensure_analytics_store': lambda: None,
                  'advanced': SimpleNamespace(process=Mock(return_value=summary)),
                  '_advanced_lock': threading.Lock(), 'base': base, 'log': logging.getLogger('test')}
            fn = server_function('_advanced_analysis_loop', ns)
            with self.assertRaises(KeyboardInterrupt):
                fn()
            payload = base.set_ai_status.call_args.kwargs
            self.assertEqual('helmet_detections' in payload, expected)
            if expected:
                self.assertEqual(payload['helmet_detections_at'], 100)

    def test_vote_window_cannot_be_smaller_than_required_frames(self):
        with patch.object(AdvancedDetector, '_load_models'), patch.object(AdvancedDetector, '_load_ocr'), \
             patch.dict('os.environ', {'HELMET_CONFIRM_FRAMES': '3', 'HELMET_CONFIRM_WINDOW': '1'}):
            detector = AccuracyDetector(SimpleNamespace(PLATE_CONFIRM_WINDOW=4), None, 'test', logging.getLogger('test'))
        self.assertEqual(detector.helmet_confirm_window, 3)
        camera, bike = {'camera_key': 'cam:1'}, {'track_id': 1}
        obs = {'status': 'no_helmet', 'confidence': .8}
        self.assertIsNone(detector._helmet_confirmed(camera, bike, obs))
        self.assertIsNone(detector._helmet_confirmed(camera, bike, obs))
        self.assertEqual(detector._helmet_confirmed(camera, bike, obs)[0], 'no_helmet')


if __name__ == '__main__':
    unittest.main()
