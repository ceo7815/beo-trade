# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Inferred (no interview tool was available; the brief and the repo are the source): one owner, Hebrew-speaking, running a private paper options desk. They check the desk on a phone during the New York session and on a desktop otherwise. The job is to see the account, the day, open positions, and whether the book is allowed to buy.

## Product Purpose

Beo-Trade is a single-user paper options desk. It recommends and tracks long option buys that are later sold, on an Alpaca paper account, with ThetaData quotes. Success is a readable, live picture of that desk: equity, day P&L, positions, and the rules that gate new buys.

## Positioning

The desk buys one option and sells that same option. It does not sell premium, trade stock, or use a second broker. The screen is the operator’s view of that one book, not a retail brokerage.

## Operating Context

Hebrew, right-to-left. The owner switches among paper periods (the live trading account and read-only views). Market clocks are New York and Dubai. A git push does not change the live site; deploy is a separate step.

## Capabilities and Constraints

Confirmed in the repo and this request:

- Screens that already exist stay: dashboard, positions, trade history, decisions, engine, finance, risk, learning, scanner, AI, integrations, status, audit, settings, trading rules, versions, login.
- Do not change trading logic, risk rules, brakes, sizing, order code, or config values.
- Live marks for the current trading account come from `/api/v1/stream` (SSE). Read-only periods keep their period-scoped reads.
- Auth headers and the session proxy stay as they are.

Undecided, left open: whether a read-only period should ever get its own live broker stream.

## Brand Commitments

The owner bound these: the name Beo-Trade, the gold and silver logo, the Rubik family (already self-hosted), Hebrew RTL, a dark luxurious glass dashboard (translucent frosted panels, depth, restrained glow).

## Evidence on Hand

Real desk data is served by the FastAPI backend. This repo does not invent fills, P&L, or account numbers. Empty and loading states stay in the product’s Hebrew.

## Product Principles

- The number on screen is the number the desk is using.
- One account is current; the others are views.
- Structure and copy stay; the glass is the surface.
- The phone and the desktop are both the desk, not a reduced copy and a full copy.
- Motion explains a change. It does not compete with the figure.

## Accessibility & Inclusion

Hebrew RTL is required. Respect `prefers-reduced-motion`. Controls are keyboard and touch reachable. Status is not conveyed by color alone.
