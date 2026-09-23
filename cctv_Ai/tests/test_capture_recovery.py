import ast
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[1] / 'stream_server.py'

class StopTest(BaseException):
    pass

class CaptureTests(unittest.TestCase):
    def env(self, cap):
        rows = {}
        env = dict(cv2=SimpleNamespace(VideoCapture=lambda: cap, CAP_FFMPEG=1,
                   CAP_PROP_OPEN_TIMEOUT_MSEC=53, CAP_PROP_READ_TIMEOUT_MSEC=54),
                   Config=SimpleNamespace(CAMERA_SUBTYPE=1, RTSP_RECONNECT_SECONDS=0),
                   time=SimpleNamespace(time=lambda: 100, sleep=Mock(side_effect=StopTest)),
                   set_status=lambda key, **kw: rows.setdefault(key, {}).update(kw),
                   rtsp_url=lambda camera: 'rtsp://test', is_camera_active=lambda key: True,
                   log=Mock(), _clear_camera_buffers=Mock())
        tree = ast.parse(SOURCE.read_text())
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in {'_capture_session', 'capture_stream'}]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), env)
        return env, rows

    def test_open_failure_releases_handle_and_sets_error(self):
        cap = Mock(); cap.open.return_value = False
        env, rows = self.env(cap)
        env['_capture_session']({'camera_key':'cam', 'camera_ip':'ip', 'channel_no':1})
        cap.open.assert_called_once_with('rtsp://test', 1, [53, 10000, 54, 5000])
        cap.release.assert_called_once()
        self.assertIn('timed out', rows['cam']['last_error'])

    def test_read_exception_releases_handle(self):
        cap = Mock(); cap.open.return_value = True; cap.read.side_effect = RuntimeError('decode')
        env, rows = self.env(cap)
        with self.assertRaises(RuntimeError):
            env['_capture_session']({'camera_key':'cam', 'camera_ip':'ip', 'channel_no':1})
        cap.release.assert_called_once()

    def test_worker_survives_session_exception(self):
        env, rows = self.env(Mock())
        env['_capture_session'] = Mock(side_effect=RuntimeError('failure'))
        with self.assertRaises(StopTest):
            env['capture_stream']({'camera_key':'cam', 'camera_ip':'ip', 'channel_no':1})
        self.assertEqual(rows['cam']['capture_phase'], 'retrying')
        self.assertFalse(rows['cam']['connected'])
        self.assertIn('RuntimeError', rows['cam']['last_error'])

    def test_deactivation_during_open_releases_without_read(self):
        cap = Mock(); cap.open.return_value = True
        env, rows = self.env(cap); env['is_camera_active'] = lambda key: False
        env['_capture_session']({'camera_key':'cam', 'camera_ip':'ip', 'channel_no':1})
        cap.read.assert_not_called(); cap.release.assert_called_once()

if __name__ == '__main__':
    unittest.main()
