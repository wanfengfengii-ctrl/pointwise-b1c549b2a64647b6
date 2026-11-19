from station.capture.frames import CapturedFrame
from station.capture.recording import CaptureRecorder
from station.quality.window import WindowValues, measure_window
from station.runs.registry import RunRegistry


def live_quality(registry: RunRegistry, recorder: CaptureRecorder, run_id: str) -> WindowValues:
    run = registry.get_run(run_id)
    frames = (
        CapturedFrame(ref.sequence, recorder.read_page(run_id, ref.page))
        for ref in recorder.slot_index(run_id)
    )
    return measure_window(frames, run.config)
