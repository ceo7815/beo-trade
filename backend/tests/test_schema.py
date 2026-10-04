from pathlib import Path

from app.models.tables import Base


def test_migration_names_every_table():
    folder = Path(__file__).resolve().parents[2].joinpath("supabase", "migrations")
    sql = "\n".join(path.read_text(encoding="utf-8") for path in sorted(folder.glob("*.sql")))
    for name in Base.metadata.tables:
        assert f"public.{name}" in sql
    assert "enable row level security" in sql
    assert "decision = 'BUY'" in sql
