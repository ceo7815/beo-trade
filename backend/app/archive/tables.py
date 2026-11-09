"""Archive tables. Rows are inserted or updated, never deleted by cleanup."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.tables import Base, _id, _now


class ArchiveRun(Base):
    __tablename__ = "archive_paper_100k_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    detail: Mapped[str] = mapped_column(Text, default="")
    counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class ArchiveAccount(Base):
    __tablename__ = "archive_paper_100k_account"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    equity: Mapped[str] = mapped_column(String(40), default="")
    cash: Mapped[str] = mapped_column(String(40), default="")
    last_equity: Mapped[str] = mapped_column(String(40), default="")
    account_last4: Mapped[str] = mapped_column(String(8), default="")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ArchiveOrder(Base):
    __tablename__ = "archive_paper_100k_orders"
    alpaca_order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="", index=True)
    symbol: Mapped[str] = mapped_column(String(64), default="")
    submitted_at: Mapped[str] = mapped_column(String(40), default="", index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ArchiveActivity(Base):
    __tablename__ = "archive_paper_100k_activities"
    activity_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    activity_type: Mapped[str] = mapped_column(String(32), default="", index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ArchiveEquity(Base):
    __tablename__ = "archive_paper_100k_equity"
    __table_args__ = (UniqueConstraint("period", "timeframe", "ts", name="uq_archive_100k_equity"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    period: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    ts: Mapped[int] = mapped_column(nullable=False, index=True)
    equity: Mapped[str] = mapped_column(String(40), default="")
    profit_loss: Mapped[str] = mapped_column(String(40), default="")
    profit_loss_pct: Mapped[str] = mapped_column(String(40), default="")
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ArchivePosition(Base):
    __tablename__ = "archive_paper_100k_positions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ArchiveLocal(Base):
    __tablename__ = "archive_paper_100k_local"
    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_archive_100k_local"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
