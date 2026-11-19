import json
import sqlite3
import unittest
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from station.archive.issue import issue_stopped
from station.archive.live import LiveArchive, read_live_archive
from station.archive.manifest import ArchiveIntegrityError
from station.archive.readback import read_stopped
from station.archive.store import ArchiveStore
from station.handover.board import handover_board
from station.handover.tickets import TicketStatus
from station.quality.window import WindowValues
from tests.camera_fixture import CameraFixture


class LiveCase(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.path = Path(folder) / 'station.sqlite3'
        self.commit_attempts = []
        self.commit_failures = 0
        self.store = self.enterContext(ArchiveStore(self.path, before_commit=self._before_commit))
        self.camera = CameraFixture(self, capacity=2, width=1, height=1, threshold=5)
        self.session = self.camera.session
        self.run_id = self.camera.run.run_id
        self.stage_calls = []
        self.stage_failures = 0
        self.live = LiveArchive(self.session, self.store, before_stage=self._before_stage)

    def _before_commit(self, result_id):
        self.commit_attempts.append(result_id)
        if self.commit_failures:
            self.commit_failures -= 1
            raise OSError('publication interrupted before commit')

    def _before_stage(self, ticket, slot, sequence):
        self.stage_calls.append((slot, sequence))
        if self.stage_failures:
            self.stage_failures -= 1
            raise OSError('staging interrupted before handover')

    def live_counts(self, result_id=None):
        with closing(sqlite3.connect(self.path)) as connection:
            tables = ('live_results', 'live_frames')
            if result_id is None:
                return tuple(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in tables)
            return tuple(
                connection.execute(f'SELECT COUNT(*) FROM {table} WHERE result_id = ?', (result_id,)).fetchone()[0]
                for table in tables
            )

    def table_count(self, table):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def change_archive(self, sql, parameters=()):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('PRAGMA foreign_keys = ON')
            connection.execute(sql, parameters)

    def freeze_sample(self):
        self.camera.step(10, b'\x02')
        self.camera.step(14, b'\x07')
        return self.live.freeze(self.run_id)

    def collect_all(self, ticket):
        collected = []
        while True:
            item = self.live.collect_next(ticket)
            if item is None:
                return collected
            collected.append(item)

    def seal_sample(self):
        ticket = self.freeze_sample()
        self.assertEqual(self.collect_all(ticket), [(0, 10), (1, 14)])
        result_id = self.live.seal(ticket)
        self.assertIsNotNone(result_id)
        return result_id


class TestLiveFreeze(LiveCase):
    def test_empty_ring(self):
        with self.assertRaises(ValueError):
            self.live.freeze(self.run_id)
        self.assertEqual(self.session.capture.page_count(self.run_id), 0)
        self.camera.step(10, b'\x02')
        ticket = self.live.freeze(self.run_id)
        self.assertIsInstance(ticket, str)
        self.assertEqual(self.live.collect_next(ticket), (0, 10))

    def test_unknown_run_ticket_and_result(self):
        with self.assertRaises(KeyError):
            self.live.freeze('unknown-run')
        with self.assertRaises(KeyError):
            self.live.collect_next('unknown-ticket')
        with self.assertRaises(KeyError):
            self.live.seal('unknown-ticket')
        with self.assertRaises(KeyError):
            self.live.collect_next(10)
        with self.assertRaises(KeyError):
            read_live_archive(self.store, 'unknown-result')

    def test_duplicate_freeze_rejected(self):
        ticket = self.freeze_sample()
        with self.assertRaises(ValueError):
            self.live.freeze(self.run_id)
        self.assertEqual(self.live.collect_next(ticket), (0, 10))

    def test_paused_run_rejected(self):
        self.camera.step(10, b'\x02')
        self.session.pause_run(self.run_id)
        with self.assertRaises(ValueError):
            self.live.freeze(self.run_id)
        self.assertEqual(self.session.capture.page_count(self.run_id), 1)

    def test_freeze_registers_without_reading_or_copying(self):
        self.camera.step(10, b'\x02')
        self.camera.step(14, b'\x07')
        before = self.camera.current_frames()
        self.live.freeze(self.run_id)
        self.assertEqual(self.camera.current_frames(), before)
        self.assertEqual(self.session.capture.page_count(self.run_id), 2)
        self.assertEqual(self.session.live_quality(self.run_id), WindowValues(2, 9, 1))


class TestLiveCollect(LiveCase):
    def test_overwrite_safeguards_one_page_per_member(self):
        self.freeze_sample()
        self.assertEqual(self.session.capture.page_count(self.run_id), 2)
        self.camera.step(20, b'\x09')
        self.assertEqual(self.session.capture.page_count(self.run_id), 3)
        self.camera.step(24, b'\x04')
        self.assertEqual(self.session.capture.page_count(self.run_id), 4)
        self.camera.step(29, b'\x02')
        self.camera.step(34, b'\x08')
        self.assertEqual(self.session.capture.page_count(self.run_id), 4)
        self.assertEqual(self.session.live_quality(self.run_id), WindowValues(2, 10, 1))

    def test_collect_releases_pages_in_freeze_order(self):
        ticket = self.freeze_sample()
        self.camera.step(20, b'\x09')
        self.camera.step(24, b'\x04')
        self.assertEqual(self.live.collect_next(ticket), (0, 10))
        self.assertEqual(self.session.capture.page_count(self.run_id), 3)
        self.assertEqual(self.live.collect_next(ticket), (1, 14))
        self.assertEqual(self.session.capture.page_count(self.run_id), 2)
        self.assertIsNone(self.live.collect_next(ticket))

    def test_collect_resident_member_adds_no_page(self):
        ticket = self.freeze_sample()
        self.assertEqual(self.live.collect_next(ticket), (0, 10))
        self.assertEqual(self.session.capture.page_count(self.run_id), 2)
        self.assertEqual(self.live.collect_next(ticket), (1, 14))
        self.assertIsNone(self.live.collect_next(ticket))
        self.assertEqual(self.session.capture.page_count(self.run_id), 2)

    def test_partial_ring_freeze(self):
        self.camera.step(10, b'\x02')
        ticket = self.live.freeze(self.run_id)
        self.camera.step(14, b'\x07')
        self.assertEqual(self.session.capture.page_count(self.run_id), 2)
        self.camera.step(20, b'\x09')
        self.assertEqual(self.session.capture.page_count(self.run_id), 3)
        self.assertEqual(self.live.collect_next(ticket), (0, 10))
        self.assertIsNone(self.live.collect_next(ticket))
        result_id = self.live.seal(ticket)
        review = read_live_archive(self.store, result_id)
        self.assertEqual(review['frames'], ((0, 10, b'\x02'),))
        self.assertEqual(review['values'], WindowValues(1, 2, 0))

    def test_stage_failure_keeps_member_pending(self):
        ticket = self.freeze_sample()
        self.camera.step(20, b'\x09')
        self.camera.step(24, b'\x04')
        self.assertEqual(self.session.capture.page_count(self.run_id), 4)
        self.stage_failures = 1
        with self.assertRaisesRegex(OSError, 'staging interrupted'):
            self.live.collect_next(ticket)
        self.assertEqual(self.stage_calls, [(0, 10)])
        self.assertEqual(self.session.capture.page_count(self.run_id), 4)
        self.assertEqual(self.live.collect_next(ticket), (0, 10))
        self.assertEqual(self.session.capture.page_count(self.run_id), 3)
        self.assertEqual(self.live.collect_next(ticket), (1, 14))
        self.assertEqual(self.session.capture.page_count(self.run_id), 2)

    def test_stage_failure_preserves_earlier_staging(self):
        ticket = self.freeze_sample()
        self.assertEqual(self.live.collect_next(ticket), (0, 10))
        self.stage_failures = 1
        with self.assertRaises(OSError):
            self.live.collect_next(ticket)
        self.assertEqual(self.live.collect_next(ticket), (1, 14))
        result_id = self.live.seal(ticket)
        review = read_live_archive(self.store, result_id)
        self.assertEqual(review['frames'], ((0, 10, b'\x02'), (1, 14, b'\x07')))

    def test_same_slot_same_bytes_stay_distinct_writes(self):
        ticket = self.freeze_sample()
        self.camera.step(20, b'\x02')
        self.camera.step(24, b'\x07')
        self.assertEqual(self.session.capture.page_count(self.run_id), 4)
        self.assertEqual(self.collect_all(ticket), [(0, 10), (1, 14)])
        result_id = self.live.seal(ticket)
        review = read_live_archive(self.store, result_id)
        self.assertEqual(review['frames'], ((0, 10, b'\x02'), (1, 14, b'\x07')))
        self.assertEqual(review['values'], WindowValues(2, 9, 1))

    def test_camera_keeps_writing_during_ticket(self):
        ticket = self.freeze_sample()
        receipt = self.camera.step(20, b'\x09')
        self.assertEqual((receipt.slot, receipt.high_water), (0, 20))
        with self.assertRaises(ValueError):
            self.camera.step(20, b'\x01')
        with self.assertRaises(ValueError):
            self.camera.step(29, b'\x01\x02')
        self.assertEqual(self.session.live_quality(self.run_id), WindowValues(2, 16, 2))
        self.assertEqual(self.collect_all(ticket), [(0, 10), (1, 14)])


class TestLiveSeal(LiveCase):
    def test_seal_waits_for_completion(self):
        ticket = self.freeze_sample()
        self.assertIsNone(self.live.seal(ticket))
        self.assertEqual(self.live.collect_next(ticket), (0, 10))
        self.assertIsNone(self.live.seal(ticket))
        self.assertEqual(self.live_counts(), (0, 0))
        self.assertEqual(self.live.collect_next(ticket), (1, 14))
        result_id = self.live.seal(ticket)
        self.assertIsNotNone(result_id)
        self.assertEqual(self.live_counts(), (1, 2))
        self.assertIsNone(self.live.collect_next(ticket))

    def test_commit_failure_keeps_staged_and_candidate(self):
        ticket = self.freeze_sample()
        self.camera.step(20, b'\x09')
        self.camera.step(24, b'\x04')
        self.assertEqual(self.collect_all(ticket), [(0, 10), (1, 14)])
        self.commit_failures = 1
        with self.assertRaisesRegex(OSError, 'before commit'):
            self.live.seal(ticket)
        candidate, = self.commit_attempts
        self.assertEqual(self.live_counts(), (0, 0))
        self.assertEqual(self.live_counts(candidate), (0, 0))
        with self.assertRaises(KeyError):
            read_live_archive(self.store, candidate)
        self.camera.step(29, b'\x02')
        self.camera.step(34, b'\x08')
        staged_calls = list(self.stage_calls)
        result_id = self.live.seal(ticket)
        self.assertEqual(result_id, candidate)
        self.assertEqual(self.stage_calls, staged_calls)
        self.assertEqual(self.live.seal(ticket), result_id)
        review = read_live_archive(self.store, result_id)
        self.assertEqual(review['frames'], ((0, 10, b'\x02'), (1, 14, b'\x07')))
        self.assertEqual(review['values'], WindowValues(2, 9, 1))

    def test_new_ticket_after_seal(self):
        first = self.seal_sample()
        self.camera.step(20, b'\x09')
        self.camera.step(24, b'\x04')
        ticket = self.live.freeze(self.run_id)
        self.assertEqual(self.collect_all(ticket), [(0, 20), (1, 24)])
        second = self.live.seal(ticket)
        self.assertNotEqual(first, second)
        self.assertEqual(read_live_archive(self.store, first)['frames'], ((0, 10, b'\x02'), (1, 14, b'\x07')))
        self.assertEqual(read_live_archive(self.store, second)['frames'], ((0, 20, b'\x09'), (1, 24, b'\x04')))
        self.assertEqual(self.live_counts(), (2, 4))


class TestLiveHandoverInteraction(LiveCase):
    def test_live_seal_does_not_consume_pause_ticket(self):
        ticket = self.freeze_sample()
        cutoff = self.session.pause_run(self.run_id)
        self.assertEqual(cutoff.high_water, 14)
        self.assertEqual(self.collect_all(ticket), [(0, 10), (1, 14)])
        live_result = self.live.seal(ticket)
        self.assertIsNotNone(live_result)
        pending, = handover_board(self.store, self.run_id, self.session)
        self.assertEqual((pending.status, pending.result_id), (TicketStatus.PENDING, None))
        self.assertEqual(self.table_count('handover_links'), 0)
        stopped_result = issue_stopped(self.session, self.store, self.run_id, cutoff)
        self.assertNotEqual(stopped_result, live_result)
        signed, = handover_board(self.store, self.run_id, self.session)
        self.assertEqual((signed.status, signed.result_id), (TicketStatus.SIGNED, stopped_result))
        self.assertEqual(read_live_archive(self.store, live_result)['frames'], ((0, 10, b'\x02'), (1, 14, b'\x07')))
        self.assertEqual([f.sequence for f in read_stopped(self.store, stopped_result).frames], [10, 14])
        self.assertEqual(self.live_counts(), (1, 2))
        self.assertEqual(self.table_count('handover_links'), 1)


class TestLiveReadback(LiveCase):
    def test_reopen_and_claim(self):
        result_id = self.seal_sample()
        self.camera.close()
        self.store.close()
        with ArchiveStore(self.path) as reopened:
            review = read_live_archive(reopened, result_id)
            self.assertEqual(review['run_id'], self.run_id)
            self.assertEqual(review['frames'], ((0, 10, b'\x02'), (1, 14, b'\x07')))
            self.assertEqual(review['values'], WindowValues(2, 9, 1))
            with self.assertRaises(KeyError):
                read_stopped(reopened, result_id)
            with self.assertRaises(KeyError):
                read_live_archive(reopened, 'unknown-result')

    def test_unpublished_candidate_cannot_be_claimed(self):
        ticket = self.freeze_sample()
        self.collect_all(ticket)
        self.commit_failures = 1
        with self.assertRaises(OSError):
            self.live.seal(ticket)
        candidate, = self.commit_attempts
        with self.assertRaises(KeyError):
            read_live_archive(self.store, candidate)


class TestLiveIntegrity(LiveCase):
    def test_missing_frame(self):
        result_id = self.seal_sample()
        self.change_archive('DELETE FROM live_frames WHERE result_id = ? AND position = 1', (result_id,))
        with self.assertRaises(ArchiveIntegrityError):
            read_live_archive(self.store, result_id)

    def test_pixel_damage(self):
        result_id = self.seal_sample()
        self.change_archive('UPDATE live_frames SET original_bytes = ? WHERE result_id = ? AND position = 1',
                            (b'\x05', result_id))
        with self.assertRaises(ArchiveIntegrityError):
            read_live_archive(self.store, result_id)

    def test_slot_damage(self):
        result_id = self.seal_sample()
        self.change_archive('UPDATE live_frames SET slot = 0 WHERE result_id = ? AND position = 1', (result_id,))
        with self.assertRaises(ArchiveIntegrityError):
            read_live_archive(self.store, result_id)

    def test_sequence_damage(self):
        result_id = self.seal_sample()
        self.change_archive("UPDATE live_frames SET source_sequence = 'f' WHERE result_id = ? AND position = 1",
                            (result_id,))
        with self.assertRaises(ArchiveIntegrityError):
            read_live_archive(self.store, result_id)

    def test_fixed_measurement_damage(self):
        result_id = self.seal_sample()
        self.change_archive('UPDATE live_results SET grayscale_total = ? WHERE result_id = ?', ('a', result_id))
        with self.assertRaises(ArchiveIntegrityError):
            read_live_archive(self.store, result_id)


class TestLiveShiftSample(LiveCase):
    def test_sample_replay(self):
        sample_path = Path(__file__).resolve().parents[1] / 'examples' / 'live_shift.json'
        sample = json.loads(sample_path.read_text(encoding='utf-8'))
        expected = sample['expected']
        self.assertEqual(sample['config'], {'capacity': 2, 'width': 1, 'height': 1, 'threshold': 5})

        def write(key):
            for frame in sample[key]:
                self.camera.step(frame['sequence'], bytes(frame['pixels']))

        write('before_freeze')
        ticket = self.live.freeze(self.run_id)
        self.assertIsNone(self.live.seal(ticket))
        write('before_first_collect')
        self.assertEqual(asdict(self.session.live_quality(self.run_id)), expected['after_first_overwrites'])
        self.assertEqual(self.session.capture.page_count(self.run_id), 4)
        self.stage_failures = 1
        with self.assertRaises(OSError):
            self.live.collect_next(ticket)
        self.assertEqual(self.session.capture.page_count(self.run_id), 4)
        write('after_stage_failure')
        self.assertEqual(asdict(self.session.live_quality(self.run_id)), expected['after_stage_failure_writes'])
        self.assertEqual(self.session.capture.page_count(self.run_id), 4)
        self.assertEqual(self.live.collect_next(ticket), (0, 10))
        self.assertEqual(self.session.capture.page_count(self.run_id), 3)
        self.assertEqual(self.live.collect_next(ticket), (1, 14))
        self.assertEqual(self.session.capture.page_count(self.run_id), 2)
        self.assertIsNone(self.live.collect_next(ticket))
        self.commit_failures = 1
        with self.assertRaises(OSError):
            self.live.seal(ticket)
        candidate, = self.commit_attempts
        self.assertEqual(self.live_counts(candidate), (0, 0))
        write('after_seal_failure')
        self.assertEqual(asdict(self.session.live_quality(self.run_id)), expected['after_seal_failure_writes'])
        result_id = self.live.seal(ticket)
        self.assertEqual(result_id, candidate)
        self.assertEqual(self.live.seal(ticket), result_id)
        self.camera.close()
        self.store.close()
        with ArchiveStore(self.path) as reopened:
            review = read_live_archive(reopened, result_id)
        self.assertEqual(review['run_id'], self.run_id)
        self.assertEqual(
            [(slot, sequence, list(data)) for slot, sequence, data in review['frames']],
            [(frame['slot'], frame['sequence'], frame['pixels']) for frame in expected['archive_frames']],
        )
        self.assertEqual(asdict(review['values']), expected['archive_values'])


if __name__ == '__main__':
    unittest.main()
