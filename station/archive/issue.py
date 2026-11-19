from uuid import uuid4

from station.archive.manifest import ArchiveHeader, build_manifest
from station.archive.store import ArchiveStore
from station.capture.frames import CapturedFrame
from station.handover.checks import require_stopped_ticket
from station.quality.window import measure_window
from station.runs.lifecycle import PausePoint
from station.runs.session import RecordingSession


def issue_stopped(
    session: RecordingSession, store: ArchiveStore, run_id: str, cutoff: PausePoint
) -> str:
    run = session.registry.get_run(run_id)
    ticket = require_stopped_ticket(run, cutoff, session.tickets)
    existing = store.find_stopped_result(ticket.cutoff)
    if existing is not None:
        return existing

    capture = session.capture
    frames = tuple(
        CapturedFrame(ref.sequence, capture.read_page(run_id, ref.page))
        for ref in capture.slot_index(run_id)
    )
    if not frames or frames[-1].sequence != ticket.cutoff.high_water:
        raise ValueError('stopped frame window disagrees with the pause cutoff')
    values = measure_window(frames, run.config)
    header = ArchiveHeader(uuid4().hex, ticket.cutoff, run.camera_id, run.config, values)
    return store.commit_stopped(header, build_manifest(frames))
