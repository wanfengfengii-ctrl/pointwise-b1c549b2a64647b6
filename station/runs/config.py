from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunConfig:
    capacity: int
    width: int
    height: int
    threshold: int

    def __post_init__(self) -> None:
        for name in ('capacity', 'width', 'height'):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f'{name} must be a positive integer')
        if type(self.threshold) is not int or self.threshold < 0:
            raise ValueError('threshold must be a nonnegative integer')

    @property
    def frame_length(self) -> int:
        return self.width * self.height


def make_run_config(capacity: int, width: int, height: int, threshold: int) -> RunConfig:
    return RunConfig(capacity, width, height, threshold)
