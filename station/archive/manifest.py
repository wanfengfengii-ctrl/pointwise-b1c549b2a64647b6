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
