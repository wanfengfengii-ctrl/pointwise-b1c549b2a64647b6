import sqlite3
from contextlib import closing

from station.archive.issue import issue_stopped
from station.archive.manifest import ArchiveIntegrityError
from station.archive.readback import read_stopped
from station.archive.store import ArchiveStore
from station.handover.board import handover_board
from station.handover.receive import receive_stopped
from station.handover.tickets import TicketStatus
from station.quality.window import WindowValues
from tests.camera_fixture import ArchiveCase


class TestOfflineReview(ArchiveCase):
    def issue_and_close(self, first=b'\x01\x03', second=b'\x03\x04'):
        cutoff = self.pause_sample(first, second)
        result_id = issue_stopped(self.session, self.store, self.run_id, cutoff)
        self.camera.close()
        self.store.close()
        return result_id

    def test_reopen_without_camera(self):
        self.camera.step(10, b'\x00\x02')
        self.camera.step(14, b'\x01\x04')
        self.camera.step(20, b'\x03\x04')
        cutoff = self.session.pause_run(self.run_id)
        result_id = issue_stopped(self.session, self.store, self.run_id, cutoff)
        self.camera.close()
        self.store.close()
        with self.assertRaises(RuntimeError):
            self.camera.step(21, b'\x01\x01')
        with self.assertRaises(RuntimeError):
            self.session.live_quality(self.run_id)
        with ArchiveStore(self.path) as reopened:
            review = read_stopped(reopened, result_id)
        self.assertEqual(review.header.cutoff, cutoff)
        self.assertEqual((review.header.config.width, review.header.config.height, review.header.config.threshold), (2, 1, 5))
        self.assertEqual(review.header.values, WindowValues(2, 12, 1))
        self.assertEqual([(f.position, f.sequence, f.data) for f in review.frames],
                         [(0, 14, b'\x01\x04'), (1, 20, b'\x03\x04')])

    def test_handover_reference(self):
        result_id = self.issue_and_close()
        with ArchiveStore(self.path) as reopened:
            ticket, = handover_board(reopened, self.run_id)
            self.assertIs(ticket.status, TicketStatus.SIGNED)
            self.assertEqual(ticket.result_id, result_id)
            review = receive_stopped(reopened, self.run_id, ticket.result_id)
        self.assertEqual(review.header.cutoff, ticket.cutoff)
        self.assertEqual(review.header.result_id, result_id)
        self.assertEqual([f.data for f in review.frames], [b'\x01\x03', b'\x03\x04'])

    def test_frozen_threshold(self):
        result_id = self.issue_and_close()
        with ArchiveStore(self.path) as reopened:
            review = read_stopped(reopened, result_id)
        self.assertEqual(review.header.config.threshold, 5)
        self.assertEqual(review.header.values, WindowValues(2, 11, 1))

    def test_unknown_result(self):
        self.issue_and_close()
        with ArchiveStore(self.path) as reopened:
            with self.assertRaises(KeyError):
                read_stopped(reopened, 'unknown-result')
            with self.assertRaises(KeyError):
                handover_board(reopened, 'unknown-run')

    def test_reinitialize(self):
        result_id = self.issue_and_close()
        self.change_archive('CREATE TABLE shift_note (note TEXT NOT NULL)')
        self.change_archive('INSERT INTO shift_note (note) VALUES (?)', ('lane inspected',))
        for _ in range(2):
            with ArchiveStore(self.path) as reopened:
                ticket, = handover_board(reopened, self.run_id)
                self.assertEqual(ticket.result_id, result_id)
                review = read_stopped(reopened, result_id)
                self.assertEqual(review.header.config.threshold, 5)
                self.assertEqual(review.header.values, WindowValues(2, 11, 1))
                self.assertEqual([(f.sequence, f.data) for f in review.frames],
                                 [(10, b'\x01\x03'), (14, b'\x03\x04')])
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute('SELECT note FROM shift_note').fetchall(), [('lane inspected',)])
        self.assertEqual(self.archive_counts(), (1, 2, 1))

    def test_missing_frame(self):
        result_id = self.issue_and_close()
        self.change_archive('DELETE FROM stopped_frames WHERE result_id = ? AND position = 1', (result_id,))
        with ArchiveStore(self.path) as reopened, self.assertRaises(ArchiveIntegrityError):
            read_stopped(reopened, result_id)

    def test_zero_frame_missing(self):
        result_id = self.issue_and_close(first=b'\x00\x00')
        self.change_archive('DELETE FROM stopped_frames WHERE result_id = ? AND position = 0', (result_id,))
        with ArchiveStore(self.path) as reopened, self.assertRaises(ArchiveIntegrityError):
            receive_stopped(reopened, self.run_id, result_id)

    def test_same_sum_damage(self):
        result_id = self.issue_and_close()
        self.change_archive('UPDATE stopped_frames SET original_bytes = ? WHERE result_id = ? AND position = 1',
                            (b'\x04\x03', result_id))
        with ArchiveStore(self.path) as reopened, self.assertRaises(ArchiveIntegrityError):
            read_stopped(reopened, result_id)

    def test_fixed_measurement_damage(self):
        result_id = self.issue_and_close()
        self.change_archive('UPDATE stopped_results SET grayscale_total = ? WHERE result_id = ?', ('c', result_id))
        with ArchiveStore(self.path) as reopened, self.assertRaises(ArchiveIntegrityError):
            read_stopped(reopened, result_id)

    def test_exact_large_and_negative_integers(self):
        run = self.session.open_run('backup-cam', 2, 1, 1, 1 << 100)
        sequences = (-(1 << 90), 1 << 100)
        self.session.append_frame(run.run_id, sequences[0], b'\xff')
        self.session.append_frame(run.run_id, sequences[1], b'\x00')
        cutoff = self.session.pause_run(run.run_id)
        result_id = issue_stopped(self.session, self.store, run.run_id, cutoff)
        self.camera.close()
        self.store.close()
        with ArchiveStore(self.path) as reopened:
            review = receive_stopped(reopened, run.run_id, result_id)
        self.assertEqual(review.header.cutoff.high_water, sequences[1])
        self.assertEqual(review.header.config.threshold, 1 << 100)
        self.assertEqual(tuple(f.sequence for f in review.frames), sequences)
        self.assertEqual(review.header.values, WindowValues(2, 255, 0))
