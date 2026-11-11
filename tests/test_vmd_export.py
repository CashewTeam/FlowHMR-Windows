import math
from pathlib import Path
from struct import Struct, unpack_from
import tempfile
import unittest

import numpy as np

from flowhmr.core.export.vmd import export_vmd


def read_vmd(path):
    """Read VMD v2 independently of the production serializer."""
    blob = path.read_bytes()
    signature, model, count = unpack_from("<30s20sI", blob)
    assert signature.rstrip(b"\0") == b"Vocaloid Motion Data 0002"
    assert model.rstrip(b"\0") == b"FlowHMR"
    bone_record = Struct("<15sI3f4f64s")
    offset = 54
    tracks = {}
    for _ in range(count):
        name, frame, *values = bone_record.unpack_from(blob, offset)
        name = name.rstrip(b"\0").decode("shift_jis")
        tracks.setdefault(name, {})[frame] = values
        offset += bone_record.size
    assert unpack_from("<4I", blob, offset) == (0, 0, 0, 0)
    display_count, = unpack_from("<I", blob, offset + 16)
    offset += 20
    display = []
    for _ in range(display_count):
        frame, visible, count = unpack_from("<IBI", blob, offset)
        offset += 9
        ik = {}
        for _ in range(count):
            name, on = unpack_from("<20sB", blob, offset)
            ik[name.rstrip(b"\0").decode("shift_jis")] = bool(on)
            offset += 21
        display.append((frame, bool(visible), ik))
    assert offset == len(blob)
    return tracks, display


class VmdExportTests(unittest.TestCase):
    def test_writes_scene_coordinates_body_and_hand_keys_on_shared_timeline(self):
        poses = np.zeros((2, 52, 3))
        poses[0, 0, 1] = math.pi / 2
        poses[0, 1, 0] = .4
        poses[0, 10, 2] = .2
        poses[1, 34, 2] = .5
        poses[1, 45, 2] = -.6
        trans = np.array(((.08, 1.16, -.16), (.16, 1.24, .16)))
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "person.vmd"
            export_vmd({"poses": poses, "trans": trans}, target, start_frame=11, scene_end=16)
            tracks, display = read_vmd(target)

        self.assertIn("右薬指３", tracks)
        self.assertTrue(all(tuple(frames) == (11, 12) for frames in tracks.values()))
        np.testing.assert_allclose(tracks["センター"][11][:3], (1, 0, 2), atol=1e-6)
        np.testing.assert_allclose(tracks["センター"][12][:3], (2, 1, -2), atol=1e-6)
        np.testing.assert_allclose(tracks["センター"][11][3:7],
                                   (0, -math.sqrt(.5), 0, math.sqrt(.5)), atol=1e-6)
        self.assertAlmostEqual(tracks["左足"][11][3], -math.sin(.2))
        self.assertAlmostEqual(tracks["左足先EX"][11][5], math.sin(.1))
        self.assertAlmostEqual(tracks["左親指０"][12][5], math.sin(.25))
        self.assertAlmostEqual(tracks["右小指３"][12][5], -math.sin(.3))
        self.assertEqual([(frame, visible) for frame, visible, _ in display],
                         [(0, False), (10, False), (11, True), (12, True), (13, False)])
        self.assertEqual(set(display[0][2]), {
            "左足ＩＫ", "右足ＩＫ", "左つま先ＩＫ", "右つま先ＩＫ",
            "左足IK", "右足IK", "左つま先IK", "右つま先IK",
        })
        self.assertFalse(any(on for _, _, keys in display for on in keys.values()))
        self.assertEqual(trans[0, 1], 1.16)  # caller's scene motion is unchanged

    def test_rejects_unsupported_motion_without_replacing_existing_output(self):
        valid = {"poses": np.zeros((2, 52, 3)), "trans": np.zeros((2, 3))}
        cases = [
            ({**valid, "mocap_framerate": 24}, {}),
            ({**valid, "trans": np.zeros((1, 3))}, {}),
            ({**valid, "poses": np.full((2, 52, 3), np.inf)}, {}),
            (valid, {"start_frame": -1}),
            (valid, {"start_frame": 2, "scene_end": 2}),
            (valid, {"start_frame": 2**32 - 1}),
        ]
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "existing.vmd"
            target.write_bytes(b"previous export")
            for motion, options in cases:
                with self.subTest(options=options), self.assertRaises(ValueError):
                    export_vmd(motion, target, **options)
            self.assertEqual(target.read_bytes(), b"previous export")
            self.assertEqual(list(target.parent.iterdir()), [target])


if __name__ == "__main__":
    unittest.main()
