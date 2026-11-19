"""Seal the current ring window of an active run while the camera keeps writing."""
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

from station.archive.manifest import LiveArchiveHeader, build_live_manifest
from station.archive.store import ArchiveStore
from station.archive.verify import verify_live_evidence
from station.capture.frames import CapturedFrame, validate_frame
from station.capture.ring import SlotRef
from station.quality.window import measure_window
from station.runs.session import RecordingSession


@dataclass
class _LiveTicket:
    """In-progress live seal: registered members plus already staged originals."""

    ticket_id: str
    run_id: str
    result_id: str
    members: tuple[SlotRef, ...]
    staged: dict[int, tuple[int, int, bytes]] = field(default_factory=dict)
    next_position: int = 0
    sealed: bool = False


class LiveArchive:
    """Freeze, collect, and publish cut-point windows for one recording session."""

    def __init__(
        self,
        session: RecordingSession,
        store: ArchiveStore,
        *,
        before_stage: Callable[[str, int, int], None] | None = None,
    ) -> None:
        self._session = session
        self._store = store
        self._before_stage = before_stage
        self._tickets: dict[str, _LiveTicket] = {}
        self._open_by_run: dict[str, str] = {}

    def _require_ticket(self, ticket: str) -> _LiveTicket:
        if type(ticket) is not str:
            raise KeyError(ticket)
        return self._tickets[ticket]

    def freeze(self, run_id: str) -> str:
        run = self._session.registry.get_run(run_id)
        run.lifecycle.require_running()
        if run_id in self._open_by_run:
            raise ValueError(f'run {run_id!r} already has an in-progress live ticket')
        members = self._session.capture.freeze_window(run_id)
        state = _LiveTicket(uuid4().hex, run_id, uuid4().hex, members)
        self._tickets[state.ticket_id] = state
        self._open_by_run[run_id] = state.ticket_id
        return state.ticket_id

    def collect_next(self, ticket: str) -> tuple[int, int] | None:
        state = self._require_ticket(ticket)
        if state.sealed or state.next_position >= len(state.members):
            return None
        ref = state.members[state.next_position]
        run = self._session.registry.get_run(state.run_id)
        capture = self._session.capture
        data = capture.collect_frozen(state.run_id, ref.slot, ref.sequence)
        validate_frame(ref.sequence, data, run.config.frame_length)
        if self._before_stage is not None:
            self._before_stage(ticket, ref.slot, ref.sequence)
        state.staged[state.next_position] = (ref.slot, ref.sequence, data)
        state.next_position += 1
        capture.release_frozen(state.run_id, ref.slot, ref.sequence)
        return (ref.slot, ref.sequence)

    def seal(self, ticket: str) -> str | None:
        state = self._require_ticket(ticket)
        if state.sealed:
            return state.result_id
        if state.next_position < len(state.members):
            return None
        run = self._session.registry.get_run(state.run_id)
        frames = tuple(state.staged[position] for position in range(len(state.members)))
        values = measure_window(
            (CapturedFrame(sequence, data) for _slot, sequence, data in frames), run.config
        )
        header = LiveArchiveHeader(state.result_id, state.run_id, run.camera_id, run.config, values)
        result_id = self._store.commit_live(header, build_live_manifest(frames))
        state.sealed = True
        del self._open_by_run[state.run_id]
        return result_id


def read_live_archive(store: ArchiveStore, result_id: str) -> dict:
    header, frames = store.load_live(result_id)
    verify_live_evidence(header, frames)
    return {
        'run_id': header.run_id,
        'frames': tuple((frame.slot, frame.sequence, frame.data) for frame in frames),
        'values': header.values,
    }
