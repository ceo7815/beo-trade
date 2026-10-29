"""Process and cgroup memory for the worker log.

Missing /proc or cgroup files return empty fields. They must not stop the worker.
"""

from __future__ import annotations

from pathlib import Path

_MIB = 1024 * 1024
# v1 reports this when the controller has no limit.
_UNLIMITED = 1 << 60


def rss_mb(status_path: Path | None = None) -> int | None:
    """Resident set from /proc/self/status VmRSS, in mebibytes."""
    path = status_path or Path("/proc/self/status")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if not line.startswith("VmRSS:"):
            continue
        parts = line.split()
        try:
            return int(parts[1]) // 1024
        except (IndexError, ValueError):
            return None
    return None


def cgroup_dirs() -> list[Path]:
    """Candidate directories, cgroup v2 first, then the v1 memory controller."""
    root = Path("/sys/fs/cgroup")
    found: list[Path] = []
    try:
        text = Path("/proc/self/cgroup").read_text(encoding="utf-8")
    except OSError:
        text = ""
    for line in text.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        relative = parts[2].strip().lstrip("/")
        if parts[0] == "0" and parts[1] == "":
            found.append(root / relative)
        elif "memory" in parts[1].split(","):
            found.append(root / "memory" / relative)
    found.extend((root, root / "memory"))
    unique: list[Path] = []
    for item in found:
        if item not in unique:
            unique.append(item)
    return unique


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _read_bytes(path: Path) -> int | None:
    raw = _read_text(path)
    if raw is None or raw == "" or raw == "max":
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    if value >= _UNLIMITED:
        return None
    return value


def _oom_kill(text: str | None) -> int | None:
    if not text:
        return None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "oom_kill":
            try:
                return int(parts[1])
            except ValueError:
                return None
    return None


def _mb(value: int | None) -> int | None:
    if value is None:
        return None
    return value // _MIB


def read_cgroup(directories: list[Path] | None = None) -> dict:
    """memory.max, memory.current, memory.peak, and oom_kill when the files exist."""
    fields = {
        "cgroup": None,
        "memory_max": None,
        "memory_current": None,
        "memory_peak": None,
        "cgroup_current_mb": None,
        "oom_kill": None,
    }
    for directory in directories if directories is not None else cgroup_dirs():
        if (directory / "memory.current").is_file() or (directory / "memory.max").is_file():
            current = _read_bytes(directory / "memory.current")
            peak = _read_bytes(directory / "memory.peak")
            raw_max = _read_text(directory / "memory.max")
            fields.update(
                {
                    "cgroup": "v2",
                    "memory_max": raw_max if raw_max else None,
                    "memory_current": current,
                    "memory_peak": peak,
                    "cgroup_current_mb": _mb(current),
                    "oom_kill": _oom_kill(_read_text(directory / "memory.events")),
                }
            )
            return fields
        if (directory / "memory.usage_in_bytes").is_file() or (directory / "memory.limit_in_bytes").is_file():
            current = _read_bytes(directory / "memory.usage_in_bytes")
            peak = _read_bytes(directory / "memory.max_usage_in_bytes")
            limit = _read_bytes(directory / "memory.limit_in_bytes")
            fields.update(
                {
                    "cgroup": "v1",
                    "memory_max": None if limit is None else str(limit),
                    "memory_current": current,
                    "memory_peak": peak,
                    "cgroup_current_mb": _mb(current),
                    "oom_kill": _oom_kill(_read_text(directory / "memory.oom_control")),
                }
            )
            return fields
    return fields


def step_memory() -> dict:
    """Fields attached to every submit step: rss_mb and cgroup memory.current."""
    cgroup = read_cgroup()
    return {
        "rss_mb": rss_mb(),
        "cgroup_current_mb": cgroup.get("cgroup_current_mb"),
        "memory_current": cgroup.get("memory_current"),
    }


def cgroup_fields() -> dict:
    """Worker-start snapshot. Every value is null when the file is missing."""
    fields = read_cgroup()
    fields["rss_mb"] = rss_mb()
    return fields
