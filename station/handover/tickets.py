from dataclasses import dataclass
from enum import StrEnum

from station.runs.lifecycle import PausePoint


class TicketStatus(StrEnum):
    PENDING = 'pending'
    SIGNED = 'signed'


@dataclass(frozen=True, slots=True)
class HandoverTicket:
    cutoff: PausePoint
    status: TicketStatus = TicketStatus.PENDING
    result_id: str | None = None


@dataclass(frozen=True, slots=True)
class HandoverLink:
    cutoff: PausePoint
    result_id: str


class HandoverTickets:
    def __init__(self) -> None:
        self._by_run: dict[str, HandoverTicket] = {}
        self._closed = False

    def find_by_run(self, run_id: str) -> HandoverTicket | None:
        if self._closed:
            raise RuntimeError('handover tickets are closed')
        return self._by_run.get(run_id)

    def get_or_create(self, cutoff: PausePoint) -> HandoverTicket:
        ticket = self.find_by_run(cutoff.run_id)
        if ticket is not None:
            if ticket.cutoff != cutoff:
                raise ValueError('run already has a different handover cutoff')
            return ticket
        ticket = HandoverTicket(cutoff)
        self._by_run[cutoff.run_id] = ticket
        return ticket

    def close(self) -> None:
        self._closed = True
        self._by_run.clear()


def resolve_ticket(ticket: HandoverTicket, link: HandoverLink | None) -> HandoverTicket:
    if link is None or link.cutoff != ticket.cutoff:
        return ticket
    return HandoverTicket(ticket.cutoff, TicketStatus.SIGNED, link.result_id)
