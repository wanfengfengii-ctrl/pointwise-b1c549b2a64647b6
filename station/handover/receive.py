from station.archive.manifest import ArchiveIntegrityError
from station.archive.readback import StoppedReview, read_stopped
from station.archive.store import ArchiveStore
from station.handover.board import handover_board
from station.handover.checks import require_transferable
from station.runs.session import RecordingSession


def receive_stopped(
    store: ArchiveStore,
    run_id: str,
    result_id: str,
    session: RecordingSession | None = None,
) -> StoppedReview:
    ticket = require_transferable(handover_board(store, run_id, session), result_id)
    review = read_stopped(store, result_id)
    if review.header.cutoff != ticket.cutoff:
        raise ArchiveIntegrityError('handover link and archive cutoff differ')
    return review
