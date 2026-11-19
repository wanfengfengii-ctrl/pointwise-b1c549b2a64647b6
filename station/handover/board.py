from station.archive.store import ArchiveStore
from station.handover.tickets import HandoverTicket, resolve_ticket
from station.runs.session import RecordingSession


def handover_board(
    store: ArchiveStore, run_id: str, session: RecordingSession | None = None
) -> tuple[HandoverTicket, ...]:
    if type(run_id) is not str:
        raise KeyError(run_id)
    if session is not None:
        try:
            session.registry.get_run(run_id)
        except KeyError:
            pass
        else:
            pending = session.tickets.find_by_run(run_id)
            if pending is None:
                return ()
            return (resolve_ticket(pending, store.read_handover_link(run_id)),)

    link = store.read_handover_link(run_id)
    if link is None:
        raise KeyError(run_id)
    return (resolve_ticket(HandoverTicket(link.cutoff), link),)
