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
sys.modules.setdefault('cv2', SimpleNamespace())
from advanced_detection import AdvancedDetector
from accuracy_detector import AccuracyDetector
ROOT = Path(__file__).resolve().parents[1]

def server_function(name, namespace):
    tree = ast.parse((ROOT / 'stream_server_mysql.py').read_text())
    node = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name))
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
        return detector._store_no_helmet({'camera_key': 'cam:1'}, 'frame', {'box': box or [100, 100, 200, 200], 'conf': 0.8, 'track_id': None}, None, ('no_helmet', 0.8, 2, 2), [80, 50, 220, 220], None)

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

    def test_vote_window_cannot_be_smaller_than_required_frames(self):
        with patch.object(AdvancedDetector, '_load_models'), patch.object(AdvancedDetector, '_load_ocr'), patch.dict('os.environ', {'HELMET_CONFIRM_FRAMES': '3', 'HELMET_CONFIRM_WINDOW': '1'}):
            detector = AccuracyDetector(SimpleNamespace(PLATE_CONFIRM_WINDOW=4), None, 'test', logging.getLogger('test'))
        self.assertEqual(detector.helmet_confirm_window, 3)
        camera, bike = ({'camera_key': 'cam:1'}, {'track_id': 1})
        obs = {'status': 'no_helmet', 'confidence': 0.8}
        self.assertIsNone(detector._helmet_confirmed(camera, bike, obs))
        self.assertIsNone(detector._helmet_confirmed(camera, bike, obs))
        self.assertEqual(detector._helmet_confirmed(camera, bike, obs)[0], 'no_helmet')
if __name__ == '__main__':
    unittest.main()
