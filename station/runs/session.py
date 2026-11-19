from collections.abc import Iterable

from station.capture.recording import CaptureRecorder, WriteReceipt
from station.handover.tickets import HandoverTickets
from station.quality.live import live_quality
from station.quality.window import WindowValues
from station.runs.lifecycle import PausePoint
from station.runs.registry import Run, RunRegistry


class RecordingSession:
    """Own a local session whose public operations run one event at a time."""

    def __init__(self, camera_ids: Iterable[str]) -> None:
        self.registry = RunRegistry(camera_ids)
        self.capture = CaptureRecorder(self.registry)
        self.tickets = HandoverTickets()

    def __enter__(self) -> 'RecordingSession':
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def open_run(self, camera_id: str, capacity: int, width: int, height: int, threshold: int) -> Run:
        return self.registry.open_run(camera_id, capacity, width, height, threshold)

    def append_frame(self, run_id: str, sequence: int, data: bytes) -> WriteReceipt:
        return self.capture.append_frame(run_id, sequence, data)

    def live_quality(self, run_id: str) -> WindowValues:
        return live_quality(self.registry, self.capture, run_id)

    def pause_run(self, run_id: str) -> PausePoint:
        run = self.registry.get_run(run_id)
        cutoff = run.lifecycle.pause(self.capture.high_water(run_id))
        self.tickets.get_or_create(cutoff)
        self.registry.release_camera(run_id)
        return cutoff

    def close(self) -> None:
        self.capture.close()
        self.tickets.close()
        self.registry.close()
