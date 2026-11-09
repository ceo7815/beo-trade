"""The deleted 10K view account must not open a broker client."""

from app.broker.accounts import (
    ViewAccountUnavailable,
    _keys_for_period,
    is_retired,
    open_view_broker,
    period_read_source,
)
from app.analytics.periods import resolve_period
from app.config.settings import Settings


def test_deleted_10k_account_is_not_the_trading_account():
    period = resolve_period("paper_10k_ils")
    assert period is not None
    assert period.alpaca_account_number == "PA3LU0NE5"
    assert is_retired(period)
    assert period.trading is False
    assert _keys_for_period("paper_10k_ils") == ("", "")
    assert _keys_for_period("paper_20k_ils") == ("alpaca_api_key", "alpaca_api_secret")
    assert _keys_for_period("current") == ("alpaca_api_key", "alpaca_api_secret")
    assert _keys_for_period("paper_100k") == ("alpaca_100k_api_key", "alpaca_100k_api_secret")


def test_retired_view_does_not_construct_a_client(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("client")

    monkeypatch.setattr("app.broker.accounts.AlpacaPaperClient", boom)
    settings = Settings(app_env="local", database_url="sqlite://", auth_required=False, trading_mode="PAPER", paper_only=True)
    try:
        open_view_broker(settings, "paper_10k_ils")
    except ViewAccountUnavailable:
        return
    raise AssertionError("expected the retired account to stop before a client")


def test_current_period_still_reads_the_trading_account():
    settings = Settings(app_env="local", database_url="sqlite://", auth_required=False, trading_mode="PAPER", paper_only=True)
    assert period_read_source(settings, "current") == "trading"
    assert period_read_source(settings, "paper_20k_ils") == "trading"
    assert period_read_source(settings, "paper_10k_ils") == "none"
