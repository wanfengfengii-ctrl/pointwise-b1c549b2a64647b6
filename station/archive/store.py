import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from os import PathLike

from station.archive.manifest import (
    ArchiveHeader,
    ArchiveIntegrityError,
    ArchivedFrame,
    LiveArchiveHeader,
    LiveArchivedFrame,
)
from station.archive.schema import decode_exact_integer, encode_exact_integer, initialize_archive
from station.handover.tickets import HandoverLink
from station.quality.window import WindowValues
from station.runs.config import make_run_config
from station.runs.lifecycle import PausePoint


class ArchiveStore:
    """An archive connection. Each writer commits one complete paused result."""

    def __init__(
        self,
        path: str | PathLike[str],
        *,
        after_frame: Callable[[str, int], None] | None = None,
        before_commit: Callable[[str], None] | None = None,
    ) -> None:
        self._connection = sqlite3.connect(path, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        try:
            initialize_archive(self._connection)
        except BaseException:
            self._connection.close()
            raise
        self._closed = False
        self._after_frame = after_frame
        self._before_commit = before_commit

    def __enter__(self) -> 'ArchiveStore':
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._require_idle()
            self._connection.close()
            self._closed = True

    def _require_idle(self) -> None:
        if self._closed:
            raise RuntimeError('archive store is closed')
        if self._connection.in_transaction:
            raise RuntimeError('archive operation already in progress on this connection')

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[None]:
        self._require_idle()
        connection = self._connection
        connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        try:
            yield
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise

    def _find_result(self, cutoff: PausePoint) -> str | None:
        if cutoff.high_water is None:
            return None
        row = self._connection.execute(
            'SELECT result_id FROM stopped_results WHERE run_id = ? AND high_water = ?',
            (cutoff.run_id, encode_exact_integer(cutoff.high_water)),
        ).fetchone()
        return None if row is None else row['result_id']

    def find_stopped_result(self, cutoff: PausePoint) -> str | None:
        self._require_idle()
        return self._find_result(cutoff)

    def _insert_header(self, header: ArchiveHeader, high_water: str) -> None:
        config = header.config
        values = header.values
        self._connection.execute(
            '''INSERT INTO stopped_results (
                   result_id, run_id, high_water, camera_id, capacity, width, height,
                   threshold, frame_count, grayscale_total, over_limit_count
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                header.result_id, header.cutoff.run_id, high_water, header.camera_id,
                encode_exact_integer(config.capacity), encode_exact_integer(config.width),
                encode_exact_integer(config.height), encode_exact_integer(config.threshold),
                encode_exact_integer(values.frame_count), encode_exact_integer(values.grayscale_total),
                encode_exact_integer(values.over_limit_count),
            ),
        )

    def commit_stopped(self, header: ArchiveHeader, frames: tuple[ArchivedFrame, ...]) -> str:
        """Publish a complete manifest prepared and measured by the issuer."""
        cutoff = header.cutoff
        if cutoff.high_water is None or not frames or len(frames) != header.values.frame_count:
            raise ValueError('publication requires a complete nonempty paused window')
        high_water = encode_exact_integer(cutoff.high_water)
        with self._transaction(write=True):
            existing = self._find_result(cutoff)
            if existing is not None:
                return existing

            self._insert_header(header, high_water)
            for frame in frames:
                self._connection.execute(
                    '''INSERT INTO stopped_frames (
                           result_id, position, source_sequence, original_bytes, digest
                       ) VALUES (?, ?, ?, ?, ?)''',
                    (header.result_id, frame.position, encode_exact_integer(frame.sequence), frame.data, frame.digest),
                )
                if self._after_frame is not None:
                    self._after_frame(header.result_id, frame.position)

            self._connection.execute(
                'INSERT INTO handover_links (run_id, high_water, result_id) VALUES (?, ?, ?)',
                (cutoff.run_id, high_water, header.result_id),
            )
            if self._before_commit is not None:
                self._before_commit(header.result_id)
        return header.result_id

    def commit_live(self, header: LiveArchiveHeader, frames: tuple[LiveArchivedFrame, ...]) -> str:
        """Publish a complete live-sealed window collected and measured by the issuer."""
        if not frames or len(frames) != header.values.frame_count:
            raise ValueError('publication requires a complete nonempty live window')
        with self._transaction(write=True):
            row = self._connection.execute(
                'SELECT result_id FROM live_results WHERE result_id = ?', (header.result_id,)
            ).fetchone()
            if row is not None:
                return header.result_id

            config = header.config
            values = header.values
            self._connection.execute(
                '''INSERT INTO live_results (
                       result_id, run_id, camera_id, capacity, width, height,
                       threshold, frame_count, grayscale_total, over_limit_count
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    header.result_id, header.run_id, header.camera_id,
                    encode_exact_integer(config.capacity), encode_exact_integer(config.width),
                    encode_exact_integer(config.height), encode_exact_integer(config.threshold),
                    encode_exact_integer(values.frame_count), encode_exact_integer(values.grayscale_total),
                    encode_exact_integer(values.over_limit_count),
                ),
            )
            for frame in frames:
                self._connection.execute(
                    '''INSERT INTO live_frames (
                           result_id, position, slot, source_sequence, original_bytes, digest
                       ) VALUES (?, ?, ?, ?, ?, ?)''',
                    (header.result_id, frame.position, frame.slot,
                     encode_exact_integer(frame.sequence), frame.data, frame.digest),
                )
            if self._before_commit is not None:
                self._before_commit(header.result_id)
        return header.result_id

    @staticmethod
    def _decode_header(row: sqlite3.Row) -> ArchiveHeader:
        camera_id = row['camera_id']
        if type(camera_id) is not str or not camera_id:
            raise ValueError('invalid archived camera ID')
        cutoff = PausePoint(row['run_id'], decode_exact_integer(row['high_water']))
        config = make_run_config(
            decode_exact_integer(row['capacity']),
            decode_exact_integer(row['width']),
            decode_exact_integer(row['height']),
            decode_exact_integer(row['threshold']),
        )
        values = WindowValues(
            decode_exact_integer(row['frame_count']),
            decode_exact_integer(row['grayscale_total']),
            decode_exact_integer(row['over_limit_count']),
        )
        return ArchiveHeader(row['result_id'], cutoff, camera_id, config, values)

    def load_stopped(self, result_id: str) -> tuple[ArchiveHeader, tuple[ArchivedFrame, ...]]:
        if type(result_id) is not str:
            raise KeyError(result_id)
        with self._transaction(write=False):
            row = self._connection.execute(
                'SELECT * FROM stopped_results WHERE result_id = ?', (result_id,)
            ).fetchone()
            if row is None:
                raise KeyError(result_id)
            frame_rows = self._connection.execute(
                '''SELECT position, source_sequence, original_bytes, digest
                   FROM stopped_frames WHERE result_id = ? ORDER BY position''',
                (result_id,),
            )
            try:
                header = self._decode_header(row)
                frames = tuple(
                    ArchivedFrame(frame['position'], decode_exact_integer(frame['source_sequence']),
                                  frame['original_bytes'], frame['digest'])
                    for frame in frame_rows
                )
            except ValueError as exc:
                raise ArchiveIntegrityError('invalid archived header or source sequence') from exc
        return header, frames

    @staticmethod
    def _decode_live_header(row: sqlite3.Row) -> LiveArchiveHeader:
        camera_id = row['camera_id']
        if type(camera_id) is not str or not camera_id:
            raise ValueError('invalid archived camera ID')
        run_id = row['run_id']
        if type(run_id) is not str or not run_id:
            raise ValueError('invalid archived run ID')
        result_id = row['result_id']
        if type(result_id) is not str or not result_id:
            raise ValueError('invalid archived result ID')
        config = make_run_config(
            decode_exact_integer(row['capacity']),
            decode_exact_integer(row['width']),
            decode_exact_integer(row['height']),
            decode_exact_integer(row['threshold']),
        )
        values = WindowValues(
            decode_exact_integer(row['frame_count']),
            decode_exact_integer(row['grayscale_total']),
            decode_exact_integer(row['over_limit_count']),
        )
        return LiveArchiveHeader(result_id, run_id, camera_id, config, values)

    def load_live(self, result_id: str) -> tuple[LiveArchiveHeader, tuple[LiveArchivedFrame, ...]]:
        if type(result_id) is not str:
            raise KeyError(result_id)
        with self._transaction(write=False):
            row = self._connection.execute(
                'SELECT * FROM live_results WHERE result_id = ?', (result_id,)
            ).fetchone()
            if row is None:
                raise KeyError(result_id)
            frame_rows = self._connection.execute(
                '''SELECT position, slot, source_sequence, original_bytes, digest
                   FROM live_frames WHERE result_id = ? ORDER BY position''',
                (result_id,),
            )
            try:
                header = self._decode_live_header(row)
                frames = tuple(
                    LiveArchivedFrame(frame['position'], frame['slot'],
                                      decode_exact_integer(frame['source_sequence']),
                                      frame['original_bytes'], frame['digest'])
                    for frame in frame_rows
                )
            except ValueError as exc:
                raise ArchiveIntegrityError('invalid archived live header or source sequence') from exc
        return header, frames

    def read_handover_link(self, run_id: str) -> HandoverLink | None:
        self._require_idle()
        if type(run_id) is not str:
            raise KeyError(run_id)
        row = self._connection.execute(
            'SELECT result_id, high_water FROM handover_links WHERE run_id = ?', (run_id,)
        ).fetchone()
        if row is None:
            return None
        try:
            cutoff = PausePoint(run_id, decode_exact_integer(row['high_water']))
            if type(row['result_id']) is not str or not row['result_id']:
                raise ValueError('invalid handover result ID')
        except ValueError as exc:
            raise ArchiveIntegrityError('invalid archived handover link') from exc
        return HandoverLink(cutoff, row['result_id'])
