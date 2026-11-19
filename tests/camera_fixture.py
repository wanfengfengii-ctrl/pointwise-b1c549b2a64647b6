import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from station.archive.store import ArchiveStore
from station.capture.source import CameraFrameEvent, SimulatedCamera
from station.runs.session import RecordingSession


class CameraFixture:
    def __init__(self, case: TestCase, *, capacity=2, width=2, height=1, threshold=5):
        self.session = RecordingSession(['line-cam', 'backup-cam'])
        case.addCleanup(self.session.close)
        self.run = self.session.open_run('line-cam', capacity, width, height, threshold)
        self.source = SimulatedCamera(self.session.capture, self.run.run_id)
        case.addCleanup(self.source.close)

    def step(self, sequence, data):
        return self.source.step(CameraFrameEvent(sequence, data))

    def current_frames(self):
        recorder = self.session.capture
        run_id = self.run.run_id
        return tuple((ref, recorder.read_page(run_id, ref.page)) for ref in recorder.slot_index(run_id))

    def close(self):
        self.source.close()
        self.session.close()


class ArchiveCase(TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.path = Path(folder) / 'station.sqlite3'
        self.store = self.enterContext(ArchiveStore(self.path))
        self.camera = CameraFixture(self)
        self.session = self.camera.session
        self.run_id = self.camera.run.run_id

    def pause_sample(self, first=b'\x01\x03', second=b'\x03\x04'):
        self.camera.step(10, first)
        self.camera.step(14, second)
        return self.session.pause_run(self.run_id)

    def archive_counts(self, result_id=None):
        with closing(sqlite3.connect(self.path)) as connection:
            tables = ('stopped_results', 'stopped_frames', 'handover_links')
            if result_id is None:
                return tuple(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in tables)
            return tuple(
                connection.execute(f'SELECT COUNT(*) FROM {table} WHERE result_id = ?', (result_id,)).fetchone()[0]
                for table in tables
            )

    def change_archive(self, sql, parameters=()):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('PRAGMA foreign_keys = ON')
            connection.execute(sql, parameters)
