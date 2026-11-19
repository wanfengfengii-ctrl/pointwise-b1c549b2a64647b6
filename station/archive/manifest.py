from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256

from station.capture.frames import CapturedFrame
from station.quality.window import WindowValues
from station.runs.config import RunConfig
from station.runs.lifecycle import PausePoint


class ArchiveIntegrityError(ValueError):
    """The fixed archive evidence is incomplete or inconsistent."""


@dataclass(frozen=True, slots=True)
class ArchiveHeader:
    result_id: str
    cutoff: PausePoint
    camera_id: str
    config: RunConfig
    values: WindowValues


@dataclass(frozen=True, slots=True)
class ArchivedFrame:
    position: int
    sequence: int
    data: bytes
    digest: str


@dataclass(frozen=True, slots=True)
class LiveArchiveHeader:
    result_id: str
    run_id: str
    camera_id: str
    config: RunConfig
    values: WindowValues


@dataclass(frozen=True, slots=True)
class LiveArchivedFrame:
    position: int
    slot: int
    sequence: int
    data: bytes
    digest: str


def frame_digest(position: int, sequence: int, data: bytes) -> str:
    digest = sha256()
    digest.update(f'{position:x}:{sequence:x}:'.encode('ascii'))
    digest.update(data)
    return digest.hexdigest()


def build_manifest(frames: Iterable[CapturedFrame]) -> tuple[ArchivedFrame, ...]:
    return tuple(
        ArchivedFrame(position, frame.sequence, frame.data, frame_digest(position, frame.sequence, frame.data))
        for position, frame in enumerate(frames)
    )


def live_frame_digest(position: int, slot: int, sequence: int, data: bytes) -> str:
    digest = sha256()
    digest.update(f'{position:x}:{slot:x}:{sequence:x}:'.encode('ascii'))
    digest.update(data)
    return digest.hexdigest()


def build_live_manifest(frames: Iterable[tuple[int, int, bytes]]) -> tuple[LiveArchivedFrame, ...]:
    return tuple(
        LiveArchivedFrame(position, slot, sequence, data, live_frame_digest(position, slot, sequence, data))
        for position, (slot, sequence, data) in enumerate(frames)
    )
