from __future__ import annotations

from app.config.settings import get_settings
from app.models.db import configure_database, database_ready, init_db
from app.ops.safety import assert_boot_safe


def main() -> None:
    settings = get_settings()
    assert_boot_safe(settings)
    configure_database(settings)
    if not database_ready():
        raise SystemExit("database connectivity failed")
    init_db(settings)
    if not database_ready():
        raise SystemExit("database connectivity failed after migration")
    print("MIGRATION OK")


if __name__ == "__main__":
    main()
