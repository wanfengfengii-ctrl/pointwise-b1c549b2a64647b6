from collections.abc import Iterator
from dataclasses import dataclass

from station.capture.frames import CapturedFrame
from station.capture.pages import PageStore


@dataclass(frozen=True, slots=True)
class SlotRef:
    slot: int
    sequence: int
    page: int


class _FrozenMember:
    """One registered cut-point write the ring still owes to the archive."""

    __slots__ = ('sequence', 'safeguard')

    def __init__(self, sequence: int) -> None:
        self.sequence = sequence
        self.safeguard: int | None = None


class FrameRing:
    def __init__(self, capacity: int, pages: PageStore) -> None:
        if type(capacity) is not int or capacity < 1:
            raise ValueError('ring capacity must be a positive integer')
        self._capacity = capacity
        self._pages = pages
        self._slots: dict[int, SlotRef] = {}
        self._next_slot = 0
        self._high_water: int | None = None
        self._frozen: dict[int, _FrozenMember] | None = None
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError('frame ring is closed')

    @property
    def high_water(self) -> int | None:
        self._require_open()
        return self._high_water

    def freeze(self) -> tuple[SlotRef, ...]:
        """Register the current valid slots and source sequences without reading pixels."""
        self._require_open()
        if self._frozen is not None:
            raise ValueError('frame ring already has an unfinished frozen window')
        members = tuple(self.iter_slot_refs())
        self._frozen = {ref.slot: _FrozenMember(ref.sequence) for ref in members}
        return members

    def _safeguard_address(self, slot: int) -> int:
        return self._capacity + slot

    def _safeguard_frozen(self, slot: int) -> None:
        if self._frozen is None:
            return
        member = self._frozen.get(slot)
        if member is None or member.safeguard is not None:
            return
        resident = self._slots.get(slot)
        if resident is None or resident.sequence != member.sequence:
            return
        address = self._safeguard_address(slot)
        self._pages.write_page(address, self._pages.read_page(resident.page))
        member.safeguard = address

    def append(self, frame: CapturedFrame) -> SlotRef:
        self._require_open()
        if self._high_water is not None and frame.sequence <= self._high_water:
            raise ValueError('source sequence must exceed the last accepted sequence')
        slot = self._next_slot
        ref = SlotRef(slot=slot, sequence=frame.sequence, page=slot)
        self._safeguard_frozen(slot)
        self._pages.write_page(ref.page, frame.data)
        self._slots[slot] = ref
        self._high_water = frame.sequence
        self._next_slot = (slot + 1) % self._capacity
        return ref

    def _frozen_member(self, slot: int, sequence: int) -> _FrozenMember:
        if self._frozen is None:
            raise ValueError('frame ring has no frozen window')
        member = self._frozen.get(slot)
        if member is None or member.sequence != sequence:
            raise ValueError('requested frame is not a pending frozen member')
        return member

    def collect_frozen(self, slot: int, sequence: int) -> bytes:
        self._require_open()
        member = self._frozen_member(slot, sequence)
        if member.safeguard is not None:
            return self._pages.read_page(member.safeguard)
        resident = self._slots.get(slot)
        if resident is None or resident.sequence != sequence:
            raise RuntimeError('frozen original is no longer preserved')
        return self._pages.read_page(resident.page)

    def release_frozen(self, slot: int, sequence: int) -> None:
        self._require_open()
        member = self._frozen_member(slot, sequence)
        if member.safeguard is not None:
            self._pages.release_page(member.safeguard)
        del self._frozen[slot]
        if not self._frozen:
            self._frozen = None

    def iter_slot_refs(self) -> Iterator[SlotRef]:
        self._require_open()
        count = len(self._slots)
        first = self._next_slot if count == self._capacity else 0
        for offset in range(count):
            yield self._slots[(first + offset) % self._capacity]

    def read_page(self, address: int) -> bytes:
        return self._pages.read_page(address)

    @property
    def page_count(self) -> int:
        return self._pages.page_count

    def close(self) -> None:
        self._closed = True
        self._pages.close()
        self._slots.clear()
        self._frozen = None
