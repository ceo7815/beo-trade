"""Avi options paper engine.

Isolated from the options desk and from the Eli/SPY engine: own process,
own SQLite file, own Alpaca paper keys, own brakes. Long debit spreads only.
Nothing here writes beo_trade.db or spy.sqlite.
"""
