import sys
from pathlib import Path
import unittest
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/dvs_streamer/python'))
from native_decoder import fresh, rates, render


class NativeTests(unittest.TestCase):
    def test_stale_live_label_is_not_receiving(self):
        state = dict(state='live', has_frame=True, monotonic=10, last_good_age=5600)
        self.assertFalse(fresh(state, 10.1))
        state['last_good_age'] = 0
        self.assertTrue(fresh(state, 10.1))
        self.assertFalse(fresh(state, 13))

    def test_heartbeat_does_not_imply_mipi_data(self):
        a = dict(session_pid=1, monotonic=10, frames=9, snapshot_id=1, stream=dict(bytes=100, buffers=2))
        b = dict(a, monotonic=15, snapshot_id=26)
        result = rates(a, b)
        self.assertEqual(result['mib_s'], 0)
        self.assertEqual(result['decoded_fps'], 0)
        self.assertEqual(result['preview_fps'], 5)
        self.assertIsNone(rates(a, dict(b, session_pid=2)))

    def test_colors_roi_invalid_and_flip(self):
        p = np.zeros((720, 960), np.uint8)
        p[0, 0], p[0, 1], p[-1, -1] = 1, 3, 2
        self.assertEqual(render(p).getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(render(p).getpixel((1, 0)), (128, 128, 128))
        self.assertEqual(render(p, flip_x=True).getpixel((959, 0)), (255, 255, 255))
        self.assertEqual(render(p, roi=(1,959,0,719)).getpixel((0,0)), (128,128,128))
        self.assertEqual(p[0,0], 1)

    def test_noise_and_roi_validation(self):
        p = np.zeros((720, 960), np.uint8); p[10,10] = 1
        self.assertEqual(render(p, noise=1).getpixel((10,10)), (128,128,128))
        with self.assertRaises(ValueError): render(p, roi=(20,10,0,719))


if __name__ == '__main__': unittest.main()
