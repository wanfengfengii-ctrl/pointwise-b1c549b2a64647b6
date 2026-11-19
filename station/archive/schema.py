import sqlite3


_SCHEMA = '''
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS stopped_results (
    result_id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL,
    high_water TEXT NOT NULL,
    camera_id TEXT NOT NULL,
    capacity TEXT NOT NULL,
    width TEXT NOT NULL,
    height TEXT NOT NULL,
    threshold TEXT NOT NULL,
    frame_count TEXT NOT NULL,
    grayscale_total TEXT NOT NULL,
    over_limit_count TEXT NOT NULL,
    UNIQUE (run_id, high_water),
    UNIQUE (run_id, high_water, result_id)
);
CREATE TABLE IF NOT EXISTS stopped_frames (
    result_id TEXT NOT NULL REFERENCES stopped_results(result_id),
    position INTEGER NOT NULL CHECK (position >= 0),
    source_sequence TEXT NOT NULL,
    original_bytes BLOB NOT NULL,
    digest TEXT NOT NULL CHECK (length(digest) = 64),
    PRIMARY KEY (result_id, position)
);
CREATE TABLE IF NOT EXISTS handover_links (
    run_id TEXT PRIMARY KEY NOT NULL,
    high_water TEXT NOT NULL,
    result_id TEXT NOT NULL,
    FOREIGN KEY (run_id, high_water, result_id)
        REFERENCES stopped_results(run_id, high_water, result_id)
);
CREATE TABLE IF NOT EXISTS live_results (
    result_id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL,
    camera_id TEXT NOT NULL,
    capacity TEXT NOT NULL,
    width TEXT NOT NULL,
    height TEXT NOT NULL,
    threshold TEXT NOT NULL,
    frame_count TEXT NOT NULL,
    grayscale_total TEXT NOT NULL,
    over_limit_count TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS live_frames (
    result_id TEXT NOT NULL REFERENCES live_results(result_id),
    position INTEGER NOT NULL CHECK (position >= 0),
    slot INTEGER NOT NULL CHECK (slot >= 0),
    source_sequence TEXT NOT NULL,
    original_bytes BLOB NOT NULL,
    digest TEXT NOT NULL CHECK (length(digest) = 64),
    PRIMARY KEY (result_id, position)
);
COMMIT;
'''


def initialize_archive(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise RuntimeError('archive initialization requires an idle connection')
    connection.execute('PRAGMA foreign_keys = ON')
    try:
        connection.executescript(_SCHEMA)
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


def encode_exact_integer(value: int) -> str:
    if type(value) is not int:
        raise ValueError('archive integer must be an int')
    return format(value, 'x')


def decode_exact_integer(text: str) -> int:
    if type(text) is not str:
        raise ValueError('archive integer must be stored as text')
    value = int(text, 16)
    if encode_exact_integer(value) != text:
        raise ValueError('archive integer has a noncanonical encoding')
    return value
