from collections.abc import Iterator
from dataclasses import dataclass

from station.capture.frames import CapturedFrame
from station.capture.pages import PageStore


@dataclass(frozen=True, slots=True)
class SlotRef:
    slot: int
    sequence: int
    page: int


class FrameRing:
    def __init__(self, capacity: int, pages: PageStore) -> None:
        if type(capacity) is not int or capacity < 1:
            raise ValueError('ring capacity must be a positive integer')
        self._capacity = capacity
        self._pages = pages
        self._slots: dict[int, SlotRef] = {}
        self._next_slot = 0
        self._high_water: int | None = None
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError('frame ring is closed')

    @property
    def high_water(self) -> int | None:
        self._require_open()
        return self._high_water

    def append(self, frame: CapturedFrame) -> SlotRef:
        self._require_open()
        if self._high_water is not None and frame.sequence <= self._high_water:
            raise ValueError('source sequence must exceed the last accepted sequence')
        slot = self._next_slot
        ref = SlotRef(slot=slot, sequence=frame.sequence, page=slot)
        self._pages.write_page(ref.page, frame.data)
        self._slots[slot] = ref
        self._high_water = frame.sequence
        self._next_slot = (slot + 1) % self._capacity
        return ref

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
