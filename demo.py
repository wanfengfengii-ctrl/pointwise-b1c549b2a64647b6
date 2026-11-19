import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from station.archive.issue import issue_stopped
from station.archive.live import LiveArchive, read_live_archive
from station.archive.store import ArchiveStore
from station.capture.source import CameraFrameEvent, SimulatedCamera
from station.handover.board import handover_board
from station.handover.receive import receive_stopped
from station.runs.session import RecordingSession


def require_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(f'{label}: expected {expected!r}, received {actual!r}')


def run_stopped_demo():
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


def run_live_demo():
    sample_path = Path(__file__).resolve().parent / 'examples' / 'live_shift.json'
    sample = json.loads(sample_path.read_text(encoding='utf-8'))
    camera_id = sample['camera_id']
    config = sample['config']
    expected = sample['expected']
    event_keys = ('before_freeze', 'before_first_collect', 'after_stage_failure', 'after_seal_failure')

    def events(key):
        return [CameraFrameEvent(frame['sequence'], bytes(frame['pixels'])) for frame in sample[key]]

    stage_attempts = []
    commit_attempts = []

    def before_stage(ticket, slot, sequence):
        stage_attempts.append((slot, sequence))
        if len(stage_attempts) == 1:
            raise OSError('staging interrupted before handover')

    def before_commit(result_id):
        commit_attempts.append(result_id)
        if len(commit_attempts) == 1:
            raise OSError('publication interrupted before commit')

    with TemporaryDirectory() as folder:
        path = Path(folder) / 'live.sqlite3'
        page_counts = {}
        live_qualities = {}
        with RecordingSession([camera_id]) as session, ArchiveStore(path, before_commit=before_commit) as archive:
            run = session.open_run(camera_id, **config)
            run_id = run.run_id
            live = LiveArchive(session, archive, before_stage=before_stage)
            with SimulatedCamera(session.capture, run_id) as camera:
                for event in events('before_freeze'):
                    camera.step(event)
                ticket = live.freeze(run_id)
                page_counts['at_freeze'] = session.capture.page_count(run_id)
                require_equal(page_counts['at_freeze'], 2, 'freeze copies no pages')
                require_equal(live.seal(ticket), None, 'seal stays pending before collection')

                for event in events('before_first_collect'):
                    camera.step(event)
                live_qualities['after_first_overwrites'] = asdict(session.live_quality(run_id))
                require_equal(live_qualities['after_first_overwrites'], expected['after_first_overwrites'],
                              'live quality after first overwrites')
                page_counts['after_first_overwrites'] = session.capture.page_count(run_id)
                require_equal(page_counts['after_first_overwrites'], 4, 'one safeguard page per frozen member')

                try:
                    live.collect_next(ticket)
                except OSError:
                    pass
                else:
                    raise AssertionError('first staging should have been rejected')
                page_counts['after_stage_failure'] = session.capture.page_count(run_id)
                require_equal(page_counts['after_stage_failure'], 4, 'capture keeps the original after staging failure')

                for event in events('after_stage_failure'):
                    camera.step(event)
                live_qualities['after_stage_failure_writes'] = asdict(session.live_quality(run_id))
                require_equal(live_qualities['after_stage_failure_writes'], expected['after_stage_failure_writes'],
                              'live quality after further rotation')
                require_equal(session.capture.page_count(run_id), 4, 'rotation adds no extra safeguard pages')

                require_equal(live.collect_next(ticket), (0, 10), 'first collect')
                page_counts['after_first_collect'] = session.capture.page_count(run_id)
                require_equal(page_counts['after_first_collect'], 3, 'collected member releases its safeguard page')
                require_equal(live.collect_next(ticket), (1, 14), 'second collect')
                page_counts['after_second_collect'] = session.capture.page_count(run_id)
                require_equal(page_counts['after_second_collect'], 2, 'all safeguard pages released')
                require_equal(live.collect_next(ticket), None, 'collection complete')

                try:
                    live.seal(ticket)
                except OSError:
                    pass
                else:
                    raise AssertionError('first publication should have been rejected')
                candidate, = commit_attempts
                with closing(sqlite3.connect(path)) as independent:
                    unpublished = independent.execute(
                        'SELECT COUNT(*) FROM live_results WHERE result_id = ?', (candidate,)
                    ).fetchone()[0]
                require_equal(unpublished, 0, 'failed publication leaves no result')

                for event in events('after_seal_failure'):
                    camera.step(event)
                live_qualities['after_seal_failure_writes'] = asdict(session.live_quality(run_id))
                require_equal(live_qualities['after_seal_failure_writes'], expected['after_seal_failure_writes'],
                              'live quality after seal failure')
                result_id = live.seal(ticket)
                require_equal(result_id, candidate, 'seal retry reuses the candidate number')
                require_equal(live.seal(ticket), result_id, 're-sealing returns the original number')
                require_equal(stage_attempts, [(0, 10), (0, 10), (1, 14)], 'staging attempts')

        with ArchiveStore(path) as reopened:
            review = read_live_archive(reopened, result_id)
            require_equal(review['run_id'], run_id, 'claimed run')
            offline_frames = [
                {'slot': slot, 'sequence': sequence, 'pixels': list(data)}
                for slot, sequence, data in review['frames']
            ]
            require_equal(offline_frames, expected['archive_frames'], 'claimed originals')
            offline_values = asdict(review['values'])
            require_equal(offline_values, expected['archive_values'], 'claimed values')

    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v3.live_window_seal',
        'input': {'camera_id': camera_id, 'config': config,
                  'events': {key: sample[key] for key in event_keys}},
        'expected': {
            'page_counts': {'at_freeze': 2, 'after_first_overwrites': 4, 'after_stage_failure': 4,
                            'after_first_collect': 3, 'after_second_collect': 2},
            'live_qualities': {key: expected[key] for key in (
                'after_first_overwrites', 'after_stage_failure_writes', 'after_seal_failure_writes')},
        },
        'actual': {'page_counts': page_counts, 'live_qualities': live_qualities},
        'downstream_expected': {'frames': expected['archive_frames'], 'values': expected['archive_values']},
        'downstream_actual': {'frames': offline_frames, 'values': offline_values},
        'code_paths': [
            'station/capture/ring.py', 'station/capture/recording.py', 'station/archive/live.py',
            'station/archive/store.py', 'station/archive/manifest.py', 'station/archive/verify.py',
        ],
        'oracle': 'Freeze registers slots 0 and 1 at sequences 10 and 14 without reading pixels. '
                  'Overwrites safeguard one extra page per member, a staging failure keeps its page, '
                  'and successful collects release them. The sealed window keeps the cut-point frames '
                  'while the live ring moves on to other writes.',
    }, sort_keys=True))


def main():
    run_stopped_demo()
    run_live_demo()


if __name__ == '__main__':
    main()
