from collections.abc import Iterable
from dataclasses import dataclass
from uuid import uuid4

from station.runs.bindings import CameraBindings
from station.runs.config import RunConfig, make_run_config
from station.runs.lifecycle import RunLifecycle


@dataclass(frozen=True, slots=True)
class Run:
    run_id: str
    camera_id: str
    config: RunConfig
    lifecycle: RunLifecycle


class RunRegistry:
    def __init__(self, camera_ids: Iterable[str]) -> None:
        self._bindings = CameraBindings(camera_ids)
        self._runs: dict[str, Run] = {}
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError('run registry is closed')

    def open_run(self, camera_id: str, capacity: int, width: int, height: int, threshold: int) -> Run:
        self._require_open()
        self._bindings.require_registered(camera_id)
        config = make_run_config(capacity, width, height, threshold)
        run_id = uuid4().hex
        while run_id in self._runs:
            run_id = uuid4().hex
        run = Run(run_id, camera_id, config, RunLifecycle(run_id))
        self._bindings.claim(camera_id, run_id)
        self._runs[run_id] = run
        return run

    def get_run(self, run_id: str) -> Run:
        self._require_open()
        if type(run_id) is not str:
            raise KeyError(run_id)
        return self._runs[run_id]

    def release_camera(self, run_id: str) -> bool:
        run = self.get_run(run_id)
        run.lifecycle.require_paused()
        return self._bindings.release(run.camera_id, run.run_id)

    def close(self) -> None:
        self._closed = True
        self._bindings.clear()
        self._runs.clear()
