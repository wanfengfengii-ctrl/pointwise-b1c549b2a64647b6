from dataclasses import dataclass

from station.capture.frames import validate_frame
from station.capture.pages import PageStore
from station.capture.ring import FrameRing, SlotRef
from station.runs.registry import RunRegistry


@dataclass(frozen=True, slots=True)
class WriteReceipt:
    slot: int
    high_water: int


class CaptureRecorder:
    def __init__(self, registry: RunRegistry) -> None:
        self._registry = registry
        self._rings: dict[str, FrameRing] = {}
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError('capture recorder is closed')

    def _current_ring(self, run_id: str) -> FrameRing | None:
        self._require_open()
        self._registry.get_run(run_id)
        return self._rings.get(run_id)

    def append_frame(self, run_id: str, sequence: int, data: bytes) -> WriteReceipt:
        self._require_open()
        run = self._registry.get_run(run_id)
        run.lifecycle.require_running()
        frame = validate_frame(sequence, data, run.config.frame_length)
        ring = self._rings.get(run_id)
        if ring is None:
            ring = FrameRing(run.config.capacity, PageStore(run.config.frame_length))
        ref = ring.append(frame)
        self._rings[run_id] = ring
        return WriteReceipt(ref.slot, ref.sequence)

    def high_water(self, run_id: str) -> int | None:
        ring = self._current_ring(run_id)
        return None if ring is None else ring.high_water

    def slot_index(self, run_id: str) -> tuple[SlotRef, ...]:
        ring = self._current_ring(run_id)
        return () if ring is None else tuple(ring.iter_slot_refs())

    def read_page(self, run_id: str, address: int) -> bytes:
        ring = self._current_ring(run_id)
        if ring is None:
            raise KeyError(address)
        return ring.read_page(address)

    def page_count(self, run_id: str) -> int:
        ring = self._current_ring(run_id)
        return 0 if ring is None else ring.page_count

    def close(self) -> None:
        self._closed = True
        for ring in self._rings.values():
            ring.close()
        self._rings.clear()
