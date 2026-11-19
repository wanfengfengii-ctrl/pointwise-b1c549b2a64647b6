from dataclasses import dataclass

from station.capture.recording import CaptureRecorder, WriteReceipt


@dataclass(frozen=True, slots=True)
class CameraFrameEvent:
    sequence: int
    data: bytes


class SimulatedCamera:
    def __init__(self, recorder: CaptureRecorder, run_id: str) -> None:
        self._recorder = recorder
        self._run_id = run_id
        self._closed = False

    def __enter__(self) -> 'SimulatedCamera':
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def step(self, event: CameraFrameEvent) -> WriteReceipt:
        if self._closed:
            raise RuntimeError('camera source is closed')
        if not isinstance(event, CameraFrameEvent):
            raise ValueError('camera step requires a frame event')
        return self._recorder.append_frame(self._run_id, event.sequence, event.data)

    def close(self) -> None:
        self._closed = True
