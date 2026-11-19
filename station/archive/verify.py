from station.archive.manifest import ArchiveHeader, ArchiveIntegrityError, ArchivedFrame, frame_digest
from station.capture.frames import CapturedFrame
from station.quality.window import measure_window


def verify_stopped_evidence(header: ArchiveHeader, frames: tuple[ArchivedFrame, ...]) -> None:
    count = header.values.frame_count
    if count < 1 or count > header.config.capacity or len(frames) != count:
        raise ArchiveIntegrityError('manifest does not cover the fixed frame count')
    if frames[-1].sequence != header.cutoff.high_water:
        raise ArchiveIntegrityError('manifest does not end at the pause cutoff')

    for position, frame in enumerate(frames):
        if type(frame.position) is not int or frame.position != position:
            raise ArchiveIntegrityError('manifest positions are incomplete or out of order')
        if type(frame.data) is not bytes or len(frame.data) != header.config.frame_length:
            raise ArchiveIntegrityError(f'frame at position {position} has invalid pixel bytes')
        if frame_digest(position, frame.sequence, frame.data) != frame.digest:
            raise ArchiveIntegrityError(f'frame at position {position} disagrees with its digest')

    try:
        measured = measure_window(
            (CapturedFrame(frame.sequence, frame.data) for frame in frames), header.config
        )
    except ValueError as exc:
        raise ArchiveIntegrityError('manifest source order or frame shape is invalid') from exc
    if measured != header.values:
        raise ArchiveIntegrityError('fixed quality values disagree with the original frames')
