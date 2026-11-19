from collections.abc import Iterable
from dataclasses import dataclass

from station.capture.frames import CapturedFrame
from station.quality.grayscale import grayscale_sum
from station.quality.thresholds import exceeds_threshold
from station.runs.config import RunConfig


@dataclass(frozen=True, slots=True)
class WindowValues:
    frame_count: int
    grayscale_total: int
    over_limit_count: int


def measure_window(frames: Iterable[CapturedFrame], config: RunConfig) -> WindowValues:
    count = 0
    total = 0
    over_limit = 0
    previous = None
    frame_length = config.frame_length
    for frame in frames:
        if previous is not None and frame.sequence <= previous:
            raise ValueError('window frames must be in strictly increasing source order')
        frame_total = grayscale_sum(frame.data, frame_length)
        count += 1
        total += frame_total
        if exceeds_threshold(frame_total, config.threshold):
            over_limit += 1
        previous = frame.sequence
    return WindowValues(count, total, over_limit)
