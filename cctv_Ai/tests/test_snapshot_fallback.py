import ast
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace


class SnapshotFallbackTests(unittest.TestCase):
    def call(self, raw=b'raw', ai=None, connected=True, raw_at=99, ai_at=None, fallback='1'):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'stream_server.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'ai_snapshot')
        node.decorator_list = []
        env = dict(allowed_keys=lambda: ['cam'], time=SimpleNamespace(time=lambda: 100),
                   lock=threading.Lock(), tracked_frames={'cam': ai}, output_frames={'cam': raw},
                   ai_status={'cam': {'last_processed_at': ai_at}},
                   camera_status={'cam': {'connected': connected, 'last_frame_at': raw_at,
                                          'last_error': 'RTSP read failed' if not connected else None}},
                   request=SimpleNamespace(args={'fallback': fallback}), jsonify=lambda x: x,
                   Response=lambda data, **kw: (data, kw))
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'snapshot', 'exec'), env)
        return env['ai_snapshot']('cam')

    def test_raw_is_shown_while_ai_waits(self):
        data, meta = self.call()
        self.assertEqual(data, b'raw')
        self.assertEqual(meta['headers']['X-Camera-Frame-Source'], 'raw')

    def test_fresh_ai_retains_tracking_display(self):
        data, meta = self.call(ai=b'boxes', ai_at=99)
        self.assertEqual(data, b'boxes')
        self.assertEqual(meta['headers']['X-Camera-Frame-Source'], 'ai')

    def test_stale_ai_switches_to_fresh_raw(self):
        self.assertEqual(self.call(ai=b'old', ai_at=1)[0], b'raw')

    def test_disconnected_camera_does_not_display_cached_raw(self):
        body, status = self.call(connected=False)
        self.assertEqual(status, 503)
        self.assertEqual(body['stage'], 'capture')
        self.assertEqual(body['error'], 'RTSP read failed')

    def test_stale_raw_is_not_used(self):
        self.assertEqual(self.call(raw_at=1)[1], 503)

    def test_without_opt_in_reports_ai_wait(self):
        body, status = self.call(fallback='0')
        self.assertEqual(status, 503)
        self.assertEqual(body['stage'], 'ai')
        self.assertTrue(body['raw_available'])


if __name__ == '__main__':
    unittest.main()
