import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from station.archive.issue import issue_stopped
from station.archive.store import ArchiveStore
from station.capture.source import CameraFrameEvent, SimulatedCamera
from station.handover.board import handover_board
from station.handover.receive import receive_stopped
from station.runs.session import RecordingSession


def require_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(f'{label}: expected {expected!r}, received {actual!r}')


def main():
    sample_path = Path(__file__).resolve().parent / 'examples' / 'shift.json'
    sample = json.loads(sample_path.read_text(encoding='utf-8'))
    camera_id = sample['camera_id']
    config = sample['config']
    expected = sample['expected']
    expected_capture = {'writes': expected['writes'], 'live': expected['live']}
    expected_review = {
        'config': config,
        'values': expected['live'],
        'handover': expected['handover'],
        'frames': expected['archived_frames'],
    }

    with TemporaryDirectory() as folder:
        path = Path(folder) / 'shift.sqlite3'
        with RecordingSession([camera_id]) as session, ArchiveStore(path) as archive:
            run = session.open_run(camera_id, **config)
            with SimulatedCamera(session.capture, run.run_id) as camera:
                writes = [
                    asdict(camera.step(CameraFrameEvent(frame['sequence'], bytes(frame['pixels']))))
                    for frame in sample['frames']
                ]
                capture = {'writes': writes, 'live': asdict(session.live_quality(run.run_id))}
                require_equal(capture, expected_capture, 'camera rotation and live quality')
                cutoff = session.pause_run(run.run_id)
                pending, = handover_board(archive, run.run_id, session)
                require_equal((pending.status.value, pending.result_id), ('pending', None), 'before issue')
                result_id = issue_stopped(session, archive, run.run_id, cutoff)
                require_equal(issue_stopped(session, archive, run.run_id, cutoff), result_id, 'issue retry')

        with ArchiveStore(path) as reopened:
            ticket, = handover_board(reopened, run.run_id)
            require_equal(ticket.result_id, result_id, 'offline handover result')
            review = receive_stopped(reopened, run.run_id, ticket.result_id)
            require_equal(review.header.cutoff, cutoff, 'offline cutoff')
            offline = {
                'config': asdict(review.header.config),
                'values': asdict(review.header.values),
                'handover': ticket.status.value,
                'frames': [{'sequence': f.sequence, 'pixels': list(f.data)} for f in review.frames],
            }
            require_equal(offline, expected_review, 'offline review')

    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v2.sparse_sequences',
        'input': {'camera_id': camera_id, 'config': config, 'frames': sample['frames']},
        'expected': expected_capture,
        'actual': capture,
        'downstream_expected': expected_review,
        'downstream_actual': offline,
        'code_paths': [
            'station/capture/ring.py', 'station/quality/window.py', 'station/archive/issue.py',
            'station/archive/store.py', 'station/handover/receive.py', 'station/archive/verify.py',
        ],
        'oracle': 'Three successful writes in two slots visit 0, 1, 0. The remaining byte sums are 5 and 7. Only 7 exceeds 5.',
    }, sort_keys=True))


if __name__ == '__main__':
    main()
