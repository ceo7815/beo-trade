from __future__ import annotations

import os
from pathlib import Path


def acquire_single_worker_lock(path: Path):
    """Hold an exclusive lock until this process exits.

    A second autonomous worker on the same data directory exits instead of trading.
    The returned handle must stay open for the life of the process.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        if os.name == "posix":
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            import msvcrt

            if handle.seek(0, os.SEEK_END) < 1:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        raise SystemExit("another autonomous worker already holds the lock") from None
    if os.name == "posix":
        handle.seek(0)
        handle.truncate()
    else:
        handle.seek(1)
    handle.write(str(os.getpid()).encode())
    handle.flush()
    return handle
