from station.archive.issue import issue_stopped
from station.archive.readback import read_stopped
from station.archive.store import ArchiveStore
from station.handover.board import handover_board
from station.handover.receive import receive_stopped
from station.handover.tickets import TicketStatus
from station.quality.window import WindowValues
from station.runs.lifecycle import PausePoint
from tests.camera_fixture import ArchiveCase


class TestStoppedSeal(ArchiveCase):
    def assert_pending(self, cutoff):
        ticket, = handover_board(self.store, self.run_id, self.session)
        self.assertEqual(ticket.cutoff, cutoff)
        self.assertIs(ticket.status, TicketStatus.PENDING)
        self.assertIsNone(ticket.result_id)

    def test_active_cannot_seal(self):
        self.camera.step(10, b'\x00\x02')
        self.assertEqual(handover_board(self.store, self.run_id, self.session), ())
        with self.assertRaises(ValueError):
            issue_stopped(self.session, self.store, self.run_id, PausePoint(self.run_id, 10))
        self.assertEqual(self.archive_counts(), (0, 0, 0))
        self.assertEqual(self.camera.step(14, b'\x01\x04').high_water, 14)

    def test_pause_and_issue(self):
        cutoff = self.pause_sample()
        self.assertEqual(cutoff, PausePoint(self.run_id, 14))
        with self.assertRaises(ValueError):
            self.camera.step(20, b'\x09\x09')
        result_id = issue_stopped(self.session, self.store, self.run_id, cutoff)
        self.assertEqual(issue_stopped(self.session, self.store, self.run_id, cutoff), result_id)
        ticket, = handover_board(self.store, self.run_id, self.session)
        self.assertEqual((ticket.status, ticket.result_id, ticket.cutoff), (TicketStatus.SIGNED, result_id, cutoff))
        self.assertEqual(self.archive_counts(), (1, 2, 1))
        review = receive_stopped(self.store, self.run_id, result_id, self.session)
        self.assertEqual(review.header.values, WindowValues(2, 11, 1))
        self.assertEqual([(f.sequence, f.data) for f in review.frames], [(10, b'\x01\x03'), (14, b'\x03\x04')])

    def test_pending_before_issue(self):
        cutoff = self.pause_sample()
        self.assert_pending(cutoff)
        with self.assertRaises(ValueError):
            receive_stopped(self.store, self.run_id, 'unissued-result', self.session)
        with self.assertRaises(KeyError):
            handover_board(self.store, 'unknown-run', self.session)

    def test_empty_stopped(self):
        cutoff = self.session.pause_run(self.run_id)
        self.assertIsNone(cutoff.high_water)
        with self.assertRaises(ValueError):
            issue_stopped(self.session, self.store, self.run_id, cutoff)
        self.assert_pending(cutoff)
        self.assertEqual(self.archive_counts(), (0, 0, 0))

    def test_repeat_pause(self):
        cutoff = self.pause_sample()
        second = self.session.open_run('line-cam', 2, 2, 1, 5)
        self.assertEqual(self.session.pause_run(self.run_id), cutoff)
        self.assert_pending(cutoff)
        with self.assertRaises(ValueError):
            self.session.open_run('line-cam', 2, 2, 1, 5)
        self.assertEqual(self.session.append_frame(second.run_id, 1, b'\x00\x01').slot, 0)
        with self.assertRaises(KeyError):
            self.session.pause_run('unknown-run')

    def test_paused_append(self):
        cutoff = self.pause_sample()
        before = self.camera.current_frames()
        second = self.session.open_run('line-cam', 1, 2, 1, 5)
        with self.assertRaises(ValueError):
            self.camera.step(20, b'\x09\x09')
        self.assertEqual(self.camera.current_frames(), before)
        self.assertEqual(self.session.capture.high_water(self.run_id), 14)
        self.assertEqual(self.session.append_frame(second.run_id, 20, b'\x01\x01').high_water, 20)
        result = issue_stopped(self.session, self.store, self.run_id, cutoff)
        self.assertEqual([(f.sequence, f.data) for f in read_stopped(self.store, result).frames],
                         [(10, b'\x01\x03'), (14, b'\x03\x04')])

    def test_distinct_cutoffs(self):
        first_cutoff = self.pause_sample()
        first_result = issue_stopped(self.session, self.store, self.run_id, first_cutoff)
        second = self.session.open_run('line-cam', 2, 2, 1, 3)
        self.session.append_frame(second.run_id, 14, b'\x00\x01')
        second_cutoff = self.session.pause_run(second.run_id)
        second_result = issue_stopped(self.session, self.store, second.run_id, second_cutoff)
        self.assertEqual(first_cutoff.high_water, second_cutoff.high_water)
        self.assertNotEqual(first_result, second_result)
        for run_id, cutoff, result_id, expected in (
            (self.run_id, first_cutoff, first_result, [b'\x01\x03', b'\x03\x04']),
            (second.run_id, second_cutoff, second_result, [b'\x00\x01']),
        ):
            with self.subTest(run_id=run_id):
                self.assertEqual(issue_stopped(self.session, self.store, run_id, cutoff), result_id)
                ticket, = handover_board(self.store, run_id, self.session)
                self.assertEqual(ticket.result_id, result_id)
                self.assertEqual([f.data for f in read_stopped(self.store, result_id).frames], expected)
        self.assertEqual(self.archive_counts(), (2, 3, 2))
        with self.assertRaises(ValueError):
            receive_stopped(self.store, second.run_id, first_result, self.session)

    def test_wrong_cutoff(self):
        cutoff = self.pause_sample()
        for wrong in (PausePoint(self.run_id, 10), PausePoint('wrong-run', cutoff.high_water)):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                issue_stopped(self.session, self.store, self.run_id, wrong)
        with self.assertRaises(KeyError):
            issue_stopped(self.session, self.store, 'unknown-run', cutoff)
        self.assert_pending(cutoff)
        self.assertEqual(self.archive_counts(), (0, 0, 0))

    def test_commit_retry(self):
        cutoff = self.pause_sample()
        before = self.camera.current_frames()
        attempts = []

        def fail_once(result_id):
            attempts.append(result_id)
            if len(attempts) == 1:
                self.assertEqual(self.archive_counts(result_id), (0, 0, 0))
                self.assertIsNone(self.store.find_stopped_result(cutoff))
                self.assert_pending(cutoff)
                raise OSError('publication interrupted before commit')

        with ArchiveStore(self.path, before_commit=fail_once) as writer:
            with self.assertRaisesRegex(OSError, 'before commit'):
                issue_stopped(self.session, writer, self.run_id, cutoff)
            self.assert_pending(cutoff)
            self.assertEqual(self.camera.current_frames(), before)
            self.assertEqual(self.archive_counts(), (0, 0, 0))
            with self.assertRaises(KeyError):
                read_stopped(self.store, attempts[0])
            with self.assertRaises(KeyError):
                handover_board(self.store, self.run_id)
            result_id = issue_stopped(self.session, writer, self.run_id, cutoff)
            self.assertEqual(issue_stopped(self.session, writer, self.run_id, cutoff), result_id)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.archive_counts(), (1, 2, 1))
        self.assertEqual(self.archive_counts(result_id), (1, 2, 1))
        self.assertEqual(handover_board(self.store, self.run_id)[0].result_id, result_id)
        review = read_stopped(self.store, result_id)
        self.assertEqual(review.header.values, WindowValues(2, 11, 1))
        self.assertEqual([f.data for f in review.frames], [b'\x01\x03', b'\x03\x04'])

    def test_write_retry(self):
        cutoff = self.pause_sample()
        attempts = []

        def fail_first_frame(result_id, position):
            attempts.append(position)
            if len(attempts) == 1:
                self.assertEqual(position, 0)
                self.assertEqual(self.archive_counts(result_id), (0, 0, 0))
                raise OSError('frame write interrupted')

        with ArchiveStore(self.path, after_frame=fail_first_frame) as writer:
            with self.assertRaisesRegex(OSError, 'frame write'):
                issue_stopped(self.session, writer, self.run_id, cutoff)
            self.assertEqual(self.archive_counts(), (0, 0, 0))
            self.assert_pending(cutoff)
            result = issue_stopped(self.session, writer, self.run_id, cutoff)
        self.assertEqual(attempts, [0, 0, 1])
        self.assertEqual(self.archive_counts(result), (1, 2, 1))
        self.assertEqual([f.sequence for f in read_stopped(self.store, result).frames], [10, 14])
