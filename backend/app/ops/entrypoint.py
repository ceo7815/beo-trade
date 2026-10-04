"""Prepare the data directory, drop root, then replace this process."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    data = Path("/app/data")
    if data.exists() or os.name == "posix":
        data.mkdir(parents=True, exist_ok=True)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        import pwd

        user = pwd.getpwnam("beo")
        os.chown(data, user.pw_uid, user.pw_gid)
        for child in data.rglob("*"):
            try:
                os.chown(child, user.pw_uid, user.pw_gid)
            except OSError:
                continue
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
    argv = sys.argv[1:]
    if not argv:
        raise SystemExit("missing command")
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main()
