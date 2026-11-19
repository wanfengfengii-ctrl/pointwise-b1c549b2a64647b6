import unittest
from dataclasses import FrozenInstanceError

from station.quality.window import WindowValues
from tests.camera_fixture import CameraFixture


class TestRecording(unittest.TestCase):
    def test_empty_window(self):
        camera = CameraFixture(self)
        run_id = camera.run.run_id
        self.assertEqual(camera.session.live_quality(run_id), WindowValues(0, 0, 0))
        self.assertEqual(camera.current_frames(), ())
        self.assertIsNone(camera.session.capture.high_water(run_id))
        self.assertEqual(camera.session.capture.page_count(run_id), 0)
        with self.assertRaises(KeyError):
            camera.session.live_quality('unknown-run')

    def test_ring_order(self):
        camera = CameraFixture(self, width=1)
        for sequence, value in enumerate((2, 5, 7), start=1):
            camera.step(sequence, bytes([value]))
        self.assertEqual([(ref.sequence, data[0]) for ref, data in camera.current_frames()], [(2, 5), (3, 7)])
        self.assertEqual(camera.session.live_quality(camera.run.run_id), WindowValues(2, 12, 1))

    def test_reject_bad_frames(self):
        camera = CameraFixture(self)
        camera.step(10, b'\x02\x03')
        before = camera.current_frames()
        with self.assertRaises(ValueError):
            camera.step(14, b'\x01')
        with self.assertRaises(ValueError):
            camera.step(9, b'\x02\x03')
        self.assertEqual(camera.current_frames(), before)
        self.assertEqual(camera.session.capture.high_water(camera.run.run_id), 10)
        self.assertEqual(camera.session.live_quality(camera.run.run_id), WindowValues(1, 5, 0))

    def test_registration_guards(self):
        camera = CameraFixture(self)
        session = camera.session
        with self.assertRaises(KeyError):
            session.open_run('not-registered', 2, 2, 1, 5)
        for settings in ((0, 2, 1, 5), (2, 0, 1, 5), (2, 2, -1, 5),
                         (2, 2, 1, -1), (2, True, 1, 5), (2.0, 2, 1, 5)):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                session.open_run('backup-cam', *settings)
        with self.assertRaises(ValueError):
            session.open_run('line-cam', 2, 2, 1, 5)
        with self.assertRaises(FrozenInstanceError):
            camera.run.config.threshold = 0
        backup = session.open_run('backup-cam', 1, 1, 1, 0)
        self.assertNotEqual(backup.run_id, camera.run.run_id)
        camera.step(10, b'\x00\x02')
        self.assertEqual(session.live_quality(camera.run.run_id), WindowValues(1, 2, 0))
        self.assertEqual(session.live_quality(backup.run_id), WindowValues(0, 0, 0))

    def test_sparse_sequences(self):
        camera = CameraFixture(self)
        first = camera.step(10, b'\x00\x02')
        first_ref, first_bytes = camera.current_frames()[0]
        second = camera.step(14, b'\x01\x04')
        third = camera.step(20, b'\x03\x04')
        self.assertEqual([(r.slot, r.high_water) for r in (first, second, third)], [(0, 10), (1, 14), (0, 20)])
        current = camera.current_frames()
        self.assertEqual([(r.slot, r.sequence, d) for r, d in current], [(1, 14, b'\x01\x04'), (0, 20, b'\x03\x04')])
        self.assertEqual(current[-1][0].page, first_ref.page)
        self.assertEqual(first_bytes, b'\x00\x02')
        self.assertEqual(camera.session.capture.page_count(camera.run.run_id), 2)
        self.assertEqual(camera.session.live_quality(camera.run.run_id), WindowValues(2, 12, 1))

    def test_threshold_boundary(self):
        camera = CameraFixture(self)
        camera.step(10, b'\x01\x04')
        self.assertEqual(camera.session.live_quality(camera.run.run_id), WindowValues(1, 5, 0))
        camera.step(14, b'\x03\x04')
        self.assertEqual(camera.session.live_quality(camera.run.run_id), WindowValues(2, 12, 1))

    def test_size_atomic(self):
        camera = CameraFixture(self)
        run_id = camera.run.run_id
        camera.step(10, b'\x00\x02')
        camera.step(14, b'\x01\x04')
        before = camera.current_frames()
        with self.assertRaises(ValueError):
            camera.step(20, b'\x03')
        self.assertEqual(camera.current_frames(), before)
        self.assertEqual(camera.session.capture.high_water(run_id), 14)
        self.assertEqual(camera.session.live_quality(run_id), WindowValues(2, 7, 0))
        receipt = camera.step(20, b'\x03\x04')
        self.assertEqual((receipt.slot, receipt.high_water), (0, 20))
        self.assertEqual(camera.session.live_quality(run_id), WindowValues(2, 12, 1))

    def test_duplicate_sequence(self):
        camera = CameraFixture(self)
        camera.step(10, b'\x00\x02')
        before = camera.current_frames()
        with self.assertRaises(ValueError):
            camera.step(10, b'\x09\x09')
        with self.assertRaises(ValueError):
            camera.step(True, b'\x09\x09')
        self.assertEqual(camera.current_frames(), before)
        self.assertEqual(camera.session.capture.high_water(camera.run.run_id), 10)
        self.assertEqual(camera.step(14, b'\x01\x04').slot, 1)

    def test_unknown_run_and_closed_camera(self):
        camera = CameraFixture(self)
        with self.assertRaises(KeyError):
            camera.session.append_frame('unknown-run', 1, b'\x00\x02')
        camera.source.close()
        with self.assertRaises(RuntimeError):
            camera.step(10, b'\x00\x02')
        self.assertEqual(camera.current_frames(), ())
