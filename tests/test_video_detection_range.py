"""Regressions for trimmed detection and source-frame alignment."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from flowhmr.utils import runtime_video_processing as video
from flowhmr.utils.runtime_capabilities import cached_detection_matches


class _Capture:
    def __init__(self, *, seek=True, length=12):
        self.seek, self.length, self.pos, self.closed = seek, length, 0, False

    def isOpened(self):
        return not self.closed

    def get(self, key):
        if key == cv2.CAP_PROP_FRAME_COUNT:
            return self.length
        if key == cv2.CAP_PROP_POS_FRAMES:
            return self.pos
        return 100

    def set(self, key, value):
        if not self.seek:
            self.pos = 2  # Some backends leave the cursor changed after failure.
            return False
        self.pos = value
        return True

    def grab(self):
        if self.pos >= self.length:
            return False
        self.pos += 1
        return True

    def read(self):
        frame = self.pos
        return self.grab(), np.full((1, 1, 3), frame, dtype=np.uint8)

    def release(self):
        self.closed = True


class DetectionRangeTest(unittest.TestCase):
    def test_failed_seek_does_not_run_yolox_outside_range(self):
        first, replacement = _Capture(seek=False), _Capture()
        detector = Mock()
        calls = []
        detector.run_human_detection.side_effect = lambda frame, **_: calls.append(int(frame[0, 0, 0]))
        with patch.object(video.cv2, "VideoCapture", side_effect=(first, replacement)):
            result = video.VideoProcessor(detector).detect_and_track("source.mp4", start_frame=5, end_frame=7)
        self.assertEqual(calls, [5, 6, 7])
        self.assertEqual(result.scanned_range, (5, 7))
        self.assertEqual(result.total_frames, 12)
        self.assertTrue(first.closed and replacement.closed)

    def test_track_cache_never_crosses_ranges(self):
        with TemporaryDirectory() as work:
            bbox = Path(work) / "bbox.npz"
            track = video.TrackInfo(1, np.array([5, 7]), np.array([[1, 2, 10, 20], [3, 2, 12, 20]], np.float32))
            result = video.TrackingResult([track], 12, 100, 100, (5, 7))
            video.VideoProcessor.save_detected_track(bbox, result, 0, (5, 7))
            self.assertTrue(cached_detection_matches(bbox, (5, 7)))
            self.assertFalse(cached_detection_matches(bbox, (6, 8)))
            self.assertFalse(cached_detection_matches(bbox, (0, None)))
            with np.load(bbox, allow_pickle=False) as data:
                np.testing.assert_array_equal(data["start_end"], [5, 7])
                np.testing.assert_array_equal(data["bbox"][1], [2, 2, 11, 20])
            np.savez(bbox, bbox=np.zeros((12, 4)), start_end=[0, 11])
            self.assertTrue(cached_detection_matches(bbox, (0, None)))
            self.assertFalse(cached_detection_matches(bbox, (5, 7)))

    def test_detection_cancels_inside_the_range(self):
        cap = _Capture()
        detector = Mock()
        detector.run_human_detection.return_value = None
        with patch.object(video.cv2, "VideoCapture", return_value=cap):
            with self.assertRaisesRegex(RuntimeError, "stop"):
                video.VideoProcessor(detector).detect_and_track("source.mp4", start_frame=3, end_frame=8,
                    cancel_check=Mock(side_effect=[None, RuntimeError("stop")]))
        self.assertEqual(detector.run_human_detection.call_count, 1)
        self.assertTrue(cap.closed)
