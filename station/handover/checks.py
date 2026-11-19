from station.handover.tickets import HandoverTicket, HandoverTickets, TicketStatus
from station.runs.lifecycle import PausePoint
from station.runs.registry import Run


def require_stopped_ticket(run: Run, requested: PausePoint, tickets: HandoverTickets) -> HandoverTicket:
    cutoff = run.lifecycle.require_paused()
    if requested != cutoff:
        raise ValueError('requested cutoff differs from the paused run')
    ticket = tickets.find_by_run(run.run_id)
    if ticket is None or ticket.cutoff != cutoff:
        raise ValueError('run has no matching handover ticket')
    if cutoff.high_water is None:
        raise ValueError('a paused window must contain at least one frame to be issued')
    return ticket


def require_transferable(
    board: tuple[HandoverTicket, ...], result_id: str
) -> HandoverTicket:
    for ticket in board:
        if ticket.status is TicketStatus.SIGNED and ticket.result_id == result_id:
            return ticket
    raise ValueError('result is not signed on this run handover')
