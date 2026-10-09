from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.ai.budget import BudgetLedger, make_ledger
from app.config.settings import Settings
from app.infra.redis_client import publish_quiet
from app.providers.base import ProviderNotConfigured, ProviderUnavailable
from app.providers.registry import build_providers
from app.recommendations.live_scan import MarketDataMissing, execute_scan


def _open_symbols(settings: Settings) -> set[str]:
    from app.broker.runtime import open_option_symbols

    return open_option_symbols(settings)


def _session_state(settings: Settings, adapter, moment: datetime) -> tuple[bool | None, datetime | None]:
    from app.core.sessions import session_bounds

    try:
        clock = adapter.get_market_clock()
    except Exception:
        return None, None
    flag = clock.get("is_open")
    if flag is not True and flag is not False:
        return None, None
    bounds = session_bounds(moment, settings.calendar())
    close = None if bounds is None else bounds[1]
    return bool(flag), close


def _analyzer(settings: Settings, ledger: BudgetLedger):
    from app.ai.client import OpenAIResponsesClient
    from app.integrations.secrets import resolve_secret

    key, _origin = resolve_secret(settings, "openai_api_key")
    if not key or settings.openai_price_input_per_million <= 0 or settings.openai_price_output_per_million <= 0:
        return None
    return OpenAIResponsesClient(settings, ledger)


def _cash(settings: Settings) -> Decimal | None:
    from app.broker.runtime import paper_equity

    return paper_equity(settings)


def _progress_writer(started: datetime):
    """The running scan's last finished stage, so a stuck scan shows where it stopped."""

    def write(stage: str, seconds: dict, theta: dict | None = None) -> None:
        try:
            from app.analytics.routes import write_scan_progress

            write_scan_progress(
                {
                    "started_at": started.isoformat(),
                    "stage_done": stage,
                    "seconds": seconds,
                    "theta_requests": theta,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        except (TypeError, ValueError, OSError):
            pass

    return write


def record_scan_blocked(moment: datetime, reason: str) -> None:
    try:
        from app.analytics.routes import write_last_scan

        write_last_scan({"observed_at": moment.isoformat(), "blocked": reason, "recommendations": 0, "buys": 0, "ai_calls": 0, "universe": None})
    except (TypeError, ValueError, OSError):
        pass


def scan_once(settings: Settings, now: datetime | None = None) -> dict:
    moment = now or datetime.now(timezone.utc)
    publish_quiet("scan.requested", moment.isoformat())
    progress = _progress_writer(moment)
    progress("started", {})
    cash = _cash(settings)
    if cash is None:
        record_scan_blocked(moment, "אין נתוני חשבון")
        return {"ok": False, "blocked": "אין נתוני חשבון"}
    providers = build_providers(settings)
    entries = []
    from app.models.db import database_ready, session_scope
    from app.models.store import load_usage

    if database_ready():
        with session_scope() as session:
            entries = load_usage(session, moment - timedelta(days=31), make_ledger(settings).cost_of)
    ledger = make_ledger(settings, entries)
    try:
        result = execute_scan(
            providers,
            settings.trading(),
            _analyzer(settings, ledger),
            ledger,
            moment,
            moment.date(),
            cash,
            _open_symbols(settings),
            daily_pnl=_measured_pnl(settings),
            sector_book=_measured_sectors(settings),
            enforce_loss=True,
            enforce_exposure=True,
            open_underlyings=_open_underlyings(settings),
            open_planned_risk=_open_planned_risk(settings),
            progress=progress,
        )
    except (ProviderUnavailable, ProviderNotConfigured, MarketDataMissing) as exc:
        record_scan_blocked(moment, str(exc))
        return {"ok": False, "blocked": str(exc)}
    publish_quiet("scan.completed", str(len(result.recommendations)))
    for row in result.recommendations:
        if row.decision.value == "BUY":
            publish_quiet("recommendation.created", row.recommendation_id)
    if result.underlyings:
        from app.market.snapshots import store_underlying_snapshots
        from app.models.db import session_scope

        with session_scope() as session:
            store_underlying_snapshots(session, result.underlyings)
            session.commit()
    if database_ready():
        from app.models.db import session_scope
        from app.models.store import save_recommendation

        with session_scope() as session:
            for row in result.recommendations:
                save_recommendation(session, row, settings.dev_user_id)
            session.commit()
    started = time.monotonic()
    outcomes: list[dict] = []
    seconds = result.stages.get("seconds") if isinstance(result.stages.get("seconds"), dict) else {}
    progress("submitting", seconds)
    _submit_autonomous_buys(settings, result.recommendations, moment, outcomes)
    seconds["submit"] = round(time.monotonic() - started, 2)
    progress("done", seconds)
    result.stages["orders"] = sum(1 for item in outcomes if item.get("result") == "submitted")
    result.stages["buy_outcomes"] = outcomes
    try:
        from app.analytics.routes import write_last_scan

        write_last_scan(
            {
                "observed_at": moment.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "recommendations": len(result.recommendations),
                "buys": sum(1 for row in result.recommendations if row.decision.value == "BUY"),
                "ai_calls": result.ai_calls,
                "universe": result.universe,
                "stages": result.stages,
                "filter": result.filter_profile,
                "blocked": "",
            }
        )
    except (TypeError, ValueError, OSError):
        pass
    return {"ok": True, "recommendations": len(result.recommendations), "blocked": ""}


# Wide-spread hits for an open contract. One check is not an exit. Reset when the process restarts.
_liquidity_hits: dict[str, int] = {}


def monitor_once(settings: Settings, quotes: dict | None = None, adapter=None, now: datetime | None = None, submit: bool = True) -> dict:
    """A mark or an exit exists only when a real quote was supplied or loaded. Nothing is invented."""
    from app.broker.execution import place_order
    from app.broker.runtime import open_paper_broker
    from app.broker.store import record_trade_event
    from app.integrations.alpaca import AlpacaNotConfigured, PaperOnlyError
    from app.models.db import session_scope
    from app.paper_trading.engine import _spread_wide, exit_signal
    from app.schemas.domain import PaperFill

    moment = now or datetime.now(timezone.utc)
    owns_adapter = adapter is None
    if adapter is None:
        try:
            adapter = open_paper_broker(settings)
        except (AlpacaNotConfigured, PaperOnlyError):
            return {"positions": 0, "exits": []}
    try:
        positions = adapter.sync_positions()
        _retire_closed_states({str(row.get("symbol") or "") for row in positions})
        if quotes is None and positions:
            quotes = _loaded_bundles(settings, moment, _held_underlyings(positions))
        bundles = quotes if quotes is not None else {}
        exits = []
        for position in positions:
            symbol = str(position.get("symbol") or "").replace(" ", "")
            bundle = bundles.get(symbol)
            if not bundle:
                continue
            option = bundle.get("option")
            underlying = bundle.get("underlying")
            entry = position.get("avg_entry_price")
            if option is None or underlying is None or entry in {None, ""}:
                continue
            trade_id = str(position.get("trade_id") or symbol)
            fill, locked = _position_fill(settings, symbol, trade_id, entry, position, option, underlying, moment)
            signal = None
            session_open, session_close = _session_state(settings, adapter, moment)
            if session_open is not None:
                trading = settings.trading()
                prior_hits = _liquidity_hits.get(symbol, 0)
                signal = exit_signal(fill, option, underlying, moment, trading, session_open, session_close, locked, prior_hits)
                _liquidity_hits[symbol] = prior_hits + 1 if _spread_wide(option, trading) else 0
            if signal is None:
                continue
            exits.append({"symbol": symbol, "reason": signal.reason, "observed": str(signal.exit_price), "source": "thetadata"})
            working = _working_exit(adapter, symbol) if submit else None
            with session_scope() as session:
                if working is not None:
                    moved = _reprice_exit(adapter, session, trade_id, working, signal, moment, session_close, settings.trading())
                    if moved:
                        exits[-1]["repriced"] = moved
                    session.commit()
                    continue
                record_trade_event(session, trade_id, "EXIT_TRIGGERED", "beo-trade", f"{signal.reason} {signal.exit_price}")
                if submit:
                    try:
                        place_order(
                            settings,
                            session,
                            adapter,
                            {
                                "symbol": symbol,
                                "qty": int(float(position.get("qty") or 0)),
                                "limit_price": str(signal.exit_price),
                                "position_intent": "sell_to_close",
                                "approved": False,
                                "system_validated": True,
                                "trade_id": trade_id,
                            },
                            moment,
                        )
                    except Exception as exc:
                        record_trade_event(session, trade_id, "BLOCKED", "beo-trade", str(exc))
                session.commit()
        return {"positions": len(positions), "exits": exits}
    finally:
        if owns_adapter:
            adapter.close()


def _working_exit(adapter, symbol: str) -> dict | None:
    from app.broker.normalize import OPEN_ORDER_STATES

    if not hasattr(adapter, "get_orders"):
        return None
    try:
        rows = adapter.get_orders("open")
    except Exception:
        return None
    for row in rows:
        if str(row.get("symbol") or "").replace(" ", "") != symbol:
            continue
        if row.get("position_intent") == "sell_to_close" and row.get("internal_state") in OPEN_ORDER_STATES:
            return row
    return None


def _reprice_exit(adapter, session, trade_id: str, order: dict, signal, moment: datetime, session_close: datetime | None, trading) -> str | None:
    """A resting sell above the bid does not fill. Move it to the bid instead of waiting for the close."""
    from app.broker.store import record_trade_event, remember_orders

    try:
        limit = Decimal(str(order.get("limit_price")))
    except Exception:
        return None
    bid = signal.exit_price
    if bid <= 0 or bid >= limit:
        return None
    stamp = str(order.get("submitted_at") or order.get("created_at") or "")
    try:
        placed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        placed = None
    age = None if placed is None else (moment - placed).total_seconds()
    closing = session_close is not None and 0 <= (session_close - moment).total_seconds() / 60 <= trading.exit_reprice_close_minutes
    if not closing and age is not None and age < trading.exit_reprice_seconds:
        return None
    order_id = str(order.get("broker_order_id") or order.get("id") or "")
    if not order_id:
        return None
    try:
        replaced = adapter.replace_order(order_id, {"limit_price": str(bid)})
    except Exception as exc:
        record_trade_event(session, trade_id, "BLOCKED", "beo-trade", f"EXIT_REPRICE {limit} -> {bid}: {str(exc)[:200]}")
        return None
    record_trade_event(session, trade_id, "EXIT_REPRICED", "alpaca-paper", f"{signal.reason} {limit} -> {bid}")
    if replaced:
        replaced.setdefault("trade_id", trade_id)
        remember_orders(session, [replaced])
    return str(bid)


def _held_underlyings(positions: list[dict]) -> tuple[str, ...]:
    from app.broker.normalize import parse_option_symbol

    held = []
    for position in positions:
        contract = parse_option_symbol(str(position.get("symbol") or "").replace(" ", "").upper())
        if contract is not None:
            held.append(str(contract["underlying"]).upper())
    return tuple(dict.fromkeys(held))


def _loaded_bundles(settings: Settings, now: datetime, underlyings_held: tuple[str, ...] = ()) -> dict:
    try:
        providers = build_providers(settings)
        if providers.market is None or providers.options is None:
            return {}
        feed = getattr(providers.market, "feed", None)
        if underlyings_held and feed is not None and hasattr(feed, "bind_symbols"):
            feed.bind_symbols(underlyings_held)
        underlyings = {row.symbol: row for row in providers.market.load_underlyings(now)}
        bundles = {}
        for option in providers.options.load_options(now):
            underlying = underlyings.get(option.underlying)
            if underlying is None:
                continue
            bundles[option.option_symbol.replace(" ", "")] = {"option": option, "underlying": underlying}
        return bundles
    except (ProviderNotConfigured, ProviderUnavailable, OSError):
        return {}


def _retire_closed_states(held_symbols: set[str]) -> None:
    from app.models.db import session_scope
    from app.positions.state import retire_absent

    with session_scope() as session:
        if retire_absent(session, held_symbols):
            session.commit()


def _position_fill(settings: Settings, symbol: str, trade_id: str, entry: object, position: dict, option, underlying, moment: datetime):
    """Read the stored entry. A missing record is not saved from the current quote."""
    from app.models.db import session_scope
    from app.models.tables import PositionState
    from app.positions.state import apply_quote, fill_from_state, load_open_state, locked_levels, rebase_entry
    from app.schemas.domain import PaperFill

    with session_scope() as session:
        state = session.get(PositionState, trade_id) if trade_id else None
        if state is None or state.status != "open":
            state = load_open_state(session, symbol)
        if state is not None:
            rebase_entry(state, Decimal(str(entry)), int(float(position.get("qty") or 0)), settings.trading())
            iv = option.implied_volatility
            apply_quote(
                state,
                bid=option.bid,
                ask=option.ask,
                underlying_price=underlying.price,
                iv=iv,
                now=moment,
                config=settings.trading(),
            )
            session.commit()
            return fill_from_state(state), locked_levels(state)
    qty = int(float(position.get("qty") or 0))
    return PaperFill(trade_id, "", symbol, qty, Decimal(str(entry)), moment), None


def reconcile_once(settings: Settings) -> None:
    from app.broker.runtime import reconcile_if_configured

    reconcile_if_configured(settings)


def _open_underlyings(settings: Settings) -> set[str]:
    from app.positions.admission import contract_underlying

    rows = _broker_positions(settings) or []
    names = {contract_underlying(str(row.get("symbol") or "")) for row in rows}
    names.discard("")
    return names


def _open_planned_risk(settings: Settings) -> Decimal:
    from app.positions.admission import book_planned_risk

    rows = _broker_positions(settings) or []
    planned = book_planned_risk(rows, [], {}, settings.trading())
    if planned is None:
        return Decimal("999999999")
    return planned


def _broker_positions(settings: Settings) -> list[dict] | None:
    from app.broker.runtime import open_paper_broker
    from app.integrations.alpaca import AlpacaNotConfigured, PaperOnlyError

    try:
        adapter = open_paper_broker(settings)
    except (AlpacaNotConfigured, PaperOnlyError):
        return None
    try:
        return list(adapter.get_positions())
    except Exception:
        return None
    finally:
        adapter.close()


def _measured_pnl(settings: Settings) -> Decimal | None:
    from app.analytics.finance import trading_day_pnl
    from app.broker.runtime import open_paper_broker
    from app.core.sessions import exchange_zone
    from app.integrations.alpaca import AlpacaNotConfigured, PaperOnlyError

    try:
        adapter = open_paper_broker(settings)
    except (AlpacaNotConfigured, PaperOnlyError):
        return None
    try:
        account = adapter.get_account()
        activities = adapter.get_activities()
    except Exception:
        return None
    finally:
        adapter.close()
    session_date = datetime.now(timezone.utc).astimezone(exchange_zone(settings.calendar())).date()
    return trading_day_pnl(account.get("equity"), account.get("last_equity"), activities, session_date)


def entry_window_open(settings: Settings, clock: dict, moment: datetime) -> bool:
    """A new entry needs the session open and more than entry_cutoff_minutes left before the close."""
    if clock.get("is_open") is not True:
        return False
    close = None
    raw = clock.get("next_close")
    if raw:
        try:
            close = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            close = None
    if close is None:
        from app.core.sessions import session_bounds

        bounds = session_bounds(moment, settings.calendar())
        close = None if bounds is None else bounds[1]
    if close is None:
        return False
    if close.tzinfo is None:
        close = close.replace(tzinfo=timezone.utc)
    return (close - moment).total_seconds() > settings.trading().entry_cutoff_minutes * 60


def _measured_sectors(settings: Settings) -> dict[str, Decimal] | None:
    rows = _broker_positions(settings)
    if rows is None:
        return None
    from app.broker.normalize import parse_option_symbol
    from app.options.risk_limits import sector_key

    book: dict[str, Decimal] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "")
        contract = parse_option_symbol(symbol)
        underlying = str(row.get("underlying") or (contract or {}).get("underlying") or symbol)
        sector = str(row.get("sector") or sector_key(underlying))
        raw = row.get("market_value")
        if raw in {None, ""}:
            return None
        book[sector] = book.get(sector, Decimal("0")) + abs(Decimal(str(raw)))
    return book


def _submit_autonomous_buys(settings: Settings, recommendations, moment: datetime, outcomes: list[dict] | None = None) -> None:
    buys = [row for row in recommendations if getattr(row.decision, "value", row.decision) == "BUY"]
    if not buys:
        return
    outcomes = [] if outcomes is None else outcomes

    def blocked_all(reason: str) -> None:
        outcomes.extend({"symbol": row.option_symbol, "result": "blocked", "reason": reason} for row in buys)

    from app.broker.execution import place_order
    from app.broker.runtime import open_paper_broker
    from app.integrations.alpaca import AlpacaNotConfigured, PaperOnlyError
    from app.market.regime import last_regime
    from app.market.regime_policy import regime_trade_policy
    from app.models.db import session_scope
    from app.models.store import save_recommendation
    from app.options.risk_limits import daily_loss_reason, exposure_reason, sector_exposure_reason

    from app.quant.scenarios import enrich_option

    try:
        providers = build_providers(settings)
        symbols = tuple(dict.fromkeys(row.underlying for row in buys))
        # Revalidate the same way the scan priced the contract: IV and greeks are solved from the live mid when the feed omits them.
        live = datetime.now(timezone.utc)
        bundles = _loaded_bundles(settings, live, symbols)
        quotes = [enrich_option(item["option"], item["underlying"], settings.trading(), live) for item in bundles.values()]
        news = providers.news.load_news(moment, symbols) if providers.news.name != "unconfigured" else []
    except Exception as exc:
        blocked_all(f"REVALIDATION_DATA: {type(exc).__name__}")
        return
    equity = _cash(settings)
    pnl = _measured_pnl(settings)
    sectors = _measured_sectors(settings)
    if equity is None or pnl is None or sectors is None:
        blocked_all("ACCOUNT_DATA_MISSING")
        return
    regime = last_regime()
    try:
        adapter = open_paper_broker(settings)
    except (AlpacaNotConfigured, PaperOnlyError):
        blocked_all("BROKER_UNAVAILABLE")
        return
    try:
        clock = adapter.get_market_clock()
        session_open = clock.get("is_open") is True
        window_open = entry_window_open(settings, clock, datetime.now(timezone.utc))
        try:
            positions = list(adapter.get_positions())
            open_orders = list(adapter.get_orders("open"))
        except Exception:
            blocked_all("BROKER_POSITIONS_UNAVAILABLE")
            return
        open_premium = _open_premium(positions, open_orders, settings.trading().contract_multiplier)
        if open_premium is None:
            blocked_all("OPEN_PREMIUM_UNKNOWN")
            return
        from app.news.confirm import fresh_news, news_confirmed
        from sqlalchemy import select

        from app.models.tables import PositionState
        from app.positions.admission import admit_buy

        state_risk: dict[str, Decimal] = {}
        with session_scope() as session:
            for stored in session.scalars(select(PositionState).where(PositionState.status == "open")):
                state_risk[stored.symbol.replace(" ", "")] = Decimal(str(stored.planned_risk))
        account = adapter.get_account()
        if account.get("equity") not in {None, ""}:
            equity = Decimal(str(account["equity"]))
        buying_power = account.get("options_buying_power") or account.get("buying_power")
        buying = None if buying_power in {None, ""} else Decimal(str(buying_power))
        from app.broker.runtime import broker_risk_snapshot
        from app.models.tables import AuditLog
        from app.options.risk_limits import sector_key

        for row in buys:
            bucket = sector_key(row.underlying)
            sector_premium = Decimal(str(sectors.get(bucket, 0)))
            fresh = fresh_news(news, row.underlying, moment, settings.trading().news_max_age_seconds)
            action, _why = regime_trade_policy(regime, row.call_put.value)
            decision = admit_buy(
                _option_for_admission(row),
                equity,
                row.quantity,
                settings.trading(),
                positions=positions,
                orders=open_orders,
                state_risk=state_risk,
                open_premium=open_premium,
                sector_premium=sector_premium,
                buying_power=buying,
                open_positions=len(positions),
            )
            snapshot = broker_risk_snapshot(
                account,
                equity,
                decision.quantity,
                Decimal(str(row.ask)),
                settings.trading().contract_multiplier,
                Decimal(str(settings.trading().risk_per_trade_pct)),
            )
            if snapshot is None or decision.quantity < 1:
                with session_scope() as session:
                    from app.broker.store import record_trade_event

                    reason = decision.reason if decision.quantity < 1 else "EQUITY_SOURCE_MISMATCH"
                    record_trade_event(session, row.recommendation_id, "BLOCKED", "beo-trade", reason, row.recommendation_id)
                    session.add(
                        AuditLog(
                            actor="risk",
                            action="pre_order_blocked",
                            entity="order",
                            entity_id=row.recommendation_id[:36],
                            payload={"reason": reason, "risk_equity_used": str(equity), "snapshot": snapshot},
                            created_at=moment,
                        )
                    )
                    session.commit()
                outcomes.append({"symbol": row.option_symbol, "result": "blocked", "reason": reason})
                continue
            row.quantity = decision.quantity
            added = Decimal(str(row.ask)) * Decimal(row.quantity) * Decimal(settings.trading().contract_multiplier)
            open_orders.append(
                {
                    "symbol": row.option_symbol,
                    "qty": row.quantity,
                    "limit_price": str(row.ask),
                    "position_intent": "buy_to_open",
                    "internal_state": "SUBMITTED",
                }
            )
            state_risk[row.option_symbol.replace(" ", "")] = added * Decimal(str(settings.trading().initial_stop_decline_pct))
            context = {
                "news_fresh": news_confirmed(fresh, settings.trading()) or settings.trading().allow_buy_without_news,
                "regime_allowed": action == "ALLOW",
                "session_open": session_open,
                "entry_window_open": window_open,
                "exposure_ok": exposure_reason(equity, open_premium, added, settings.trading()) is None,
                "daily_loss_ok": daily_loss_reason(equity, pnl, settings.trading()) is None,
                "sector_ok": sector_exposure_reason(equity, bucket, sectors, added, settings.trading()) is None,
                "reference_price": str(row.option_price),
            }
            open_premium += added
            sectors[bucket] = sector_premium + added
            with session_scope() as session:
                save_recommendation(session, row, settings.dev_user_id)
                session.add(
                    AuditLog(
                        actor="risk",
                        action="pre_order",
                        entity="order",
                        entity_id=row.recommendation_id[:36],
                        payload=snapshot,
                        created_at=moment,
                    )
                )
                try:
                    place_order(
                        settings,
                        session,
                        adapter,
                        {
                            "symbol": row.option_symbol,
                            "qty": row.quantity,
                            "limit_price": str(row.max_entry_price),
                            "position_intent": "buy_to_open",
                            "approved": False,
                            "system_validated": True,
                            "recommendation_id": row.recommendation_id,
                            "revalidation": context,
                        },
                        live,
                        quotes,
                    )
                    outcomes.append({"symbol": row.option_symbol, "result": "submitted", "reason": ""})
                except Exception as exc:
                    outcomes.append({"symbol": row.option_symbol, "result": "blocked", "reason": str(exc)[:300]})
                session.commit()
    finally:
        adapter.close()


def _option_for_admission(row):
    from app.schemas.domain import OptionSnapshot

    return OptionSnapshot(
        row.underlying,
        row.option_symbol,
        row.call_put,
        Decimal(str(row.strike)),
        row.expiration,
        Decimal(str(row.bid)),
        Decimal(str(row.ask)),
        Decimal(str(row.option_price)),
        int(row.volume),
        int(row.open_interest),
        row.timestamp,
        None if row.iv is None else Decimal(str(row.iv)),
        None if row.delta is None else Decimal(str(row.delta)),
        None if row.gamma is None else Decimal(str(row.gamma)),
        None if row.theta is None else Decimal(str(row.theta)),
        None if row.vega is None else Decimal(str(row.vega)),
    )


def _open_premium(positions: list[dict], orders: list[dict], multiplier: int) -> Decimal | None:
    total = Decimal("0")
    for row in positions:
        raw = row.get("market_value")
        if raw in {None, ""}:
            return None
        total += abs(Decimal(str(raw)))
    for row in orders:
        if str(row.get("position_intent") or "") != "buy_to_open":
            continue
        state = str(row.get("internal_state") or row.get("status") or "").upper()
        if state in {"FILLED", "CANCELED", "CANCELLED", "EXPIRED", "REJECTED"}:
            continue
        qty = row.get("qty")
        price = row.get("limit_price")
        if qty in {None, ""} or price in {None, ""}:
            return None
        total += abs(Decimal(str(qty)) * Decimal(str(price)) * Decimal(multiplier))
    return total


def close_all_positions(settings: Settings, now: datetime | None = None) -> dict:
    """Sell every open paper position at the live bid. A missing quote blocks that leg."""
    from app.broker.execution import place_order
    from app.broker.runtime import open_paper_broker
    from app.broker.store import record_trade_event
    from app.integrations.alpaca import AlpacaNotConfigured, PaperOnlyError
    from app.models.db import session_scope

    moment = now or datetime.now(timezone.utc)
    try:
        adapter = open_paper_broker(settings)
    except (AlpacaNotConfigured, PaperOnlyError) as exc:
        return {"ok": False, "blocked": str(exc), "submitted": [], "blocked_positions": []}
    submitted = []
    blocked = []
    try:
        positions = list(adapter.sync_positions())
        quotes = _loaded_bundles(settings, moment, _held_underlyings(positions)) if positions else {}
        with session_scope() as session:
            record_trade_event(session, "close-all", "CLOSE_ALL", "beo-trade", "בקשת סגירת כל הפוזיציות")
            for position in positions:
                symbol = str(position.get("symbol") or "").replace(" ", "")
                qty = int(float(position.get("qty") or 0))
                bundle = quotes.get(symbol) or {}
                option = bundle.get("option")
                trade_id = str(position.get("trade_id") or symbol or "close-all")
                if option is None or option.bid <= 0 or qty < 1:
                    blocked.append({"symbol": symbol, "reason": "אין bid לחוזה"})
                    record_trade_event(session, trade_id, "BLOCKED", "beo-trade", "אין bid לחוזה")
                    continue
                try:
                    place_order(
                        settings,
                        session,
                        adapter,
                        {
                            "symbol": symbol,
                            "qty": qty,
                            "limit_price": str(option.bid),
                            "position_intent": "sell_to_close",
                            "approved": False,
                            "system_validated": True,
                            "trade_id": trade_id,
                        },
                        moment,
                    )
                    submitted.append(symbol)
                except Exception as exc:
                    blocked.append({"symbol": symbol, "reason": str(exc)})
                    record_trade_event(session, trade_id, "BLOCKED", "beo-trade", str(exc))
            session.commit()
    finally:
        adapter.close()
    return {"ok": True, "submitted": submitted, "blocked_positions": blocked}
