from dataclasses import dataclass

from station.archive.manifest import ArchiveHeader, ArchivedFrame
from station.archive.store import ArchiveStore
from station.archive.verify import verify_stopped_evidence


@dataclass(frozen=True, slots=True)
class StoppedReview:
    header: ArchiveHeader
    frames: tuple[ArchivedFrame, ...]


def read_stopped(store: ArchiveStore, result_id: str) -> StoppedReview:
    header, frames = store.load_stopped(result_id)
    verify_stopped_evidence(header, frames)
    return StoppedReview(header, frames)
