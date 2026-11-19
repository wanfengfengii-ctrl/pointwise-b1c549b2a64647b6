from dataclasses import dataclass
from enum import StrEnum


class RunPhase(StrEnum):
    RUNNING = 'running'
    PAUSED = 'paused'


@dataclass(frozen=True, slots=True)
class PausePoint:
    run_id: str
    high_water: int | None

    def __post_init__(self) -> None:
        if type(self.run_id) is not str or not self.run_id:
            raise ValueError('a pause point needs a run ID')
        if self.high_water is not None and type(self.high_water) is not int:
            raise ValueError('a pause point high-water mark must be an integer or None')


class RunLifecycle:
    def __init__(self, run_id: str) -> None:
        self._run_id = run_id
        self._cutoff: PausePoint | None = None

    @property
    def phase(self) -> RunPhase:
        return RunPhase.RUNNING if self._cutoff is None else RunPhase.PAUSED

    def require_running(self) -> None:
        if self._cutoff is not None:
            raise ValueError(f'run {self._run_id!r} is paused')

    def require_paused(self) -> PausePoint:
        if self._cutoff is None:
            raise ValueError(f'run {self._run_id!r} is still recording')
        return self._cutoff

    def pause(self, high_water: int | None) -> PausePoint:
        if self._cutoff is None:
            self._cutoff = PausePoint(self._run_id, high_water)
        return self._cutoff
