from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    sequence: int
    data: bytes

    def __post_init__(self) -> None:
        if type(self.sequence) is not int:
            raise ValueError('source sequence must be an integer')
        if type(self.data) is not bytes:
            raise ValueError('frame must contain immutable bytes')


def validate_frame(sequence: int, data: bytes, frame_length: int) -> CapturedFrame:
    frame = CapturedFrame(sequence, data)
    if len(frame.data) != frame_length:
        raise ValueError(f'frame must contain exactly {frame_length} bytes')
    return frame
