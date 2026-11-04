---
name: Beo-Trade
description: Night-glass desk for a Hebrew paper options book.
colors:
  ground: "#07080c"
  glass: "rgba(14, 16, 22, 0.55)"
  gold: "#e4c27a"
  gold-deep: "#d7b36a"
  silver: "#d5d8e0"
  ink: "#f7f2e8"
  muted: "#a3a9b3"
  up: "#22c38e"
  down: "#f23645"
  glass-line: "rgba(228, 194, 122, 0.38)"
typography:
  body:
    fontFamily: "Rubik, Arial, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: "0"
  display:
    fontFamily: "Rubik, Arial, sans-serif"
    fontSize: "clamp(40px, 12vw, 76px)"
    fontWeight: 300
    lineHeight: 1
    letterSpacing: "-0.04em"
rounded:
  sm: "12px"
  md: "18px"
  lg: "22px"
  pill: "999px"
spacing:
  xs: "8px"
  sm: "12px"
  md: "16px"
  lg: "24px"
components:
  button-primary:
    backgroundColor: "#d7b36a"
    textColor: "#1c1508"
    rounded: "{rounded.pill}"
    padding: "0 14px"
    height: "44px"
  panel:
    backgroundColor: "{colors.glass}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "12px 14px"
---

## Overview

Beo-Trade is a single-user paper desk. The screens stay where they are. Night glass is the case around the figures: frosted panels, a thin gold rim, a silver inner edge, Rubik, Hebrew right-to-left. Green and red are only for profit and loss.

## Colors

The ground is near-black. Panels are translucent over that ground, not a second opaque gray. Gold marks the current account, the selected tab, and the rim. Silver is the inset highlight and secondary metal. Do not introduce a blue accent for selection.

## Typography

Rubik is already self-hosted, including Hebrew. Figures use tabular numerals, stay on one line, and shrink to fit a narrow plate. The day number is the largest type on the desk. There is no eyebrow above it.

## Layout

Desktop is a side rail and a stage. The top bar and the phone’s bottom nav float as glass. The phone does not scroll sideways. The history chart sits above the day card with space between them.

## Elevation & Depth

Depth is blur plus a soft shadow and the silver inset. The account list is a fixed glass sheet, not a row of pills. Motion eases out and turns off when the visitor asks for less motion.

## Shapes

Panels use a large radius. The account trigger, badges, and tabs are pills. Scrollbars on a fine pointer are thin and gold-silver. A coarse pointer hides them.

## Components

The account control shows one name, a badge (פעיל or קריאה), and a chevron. The list opens with the keyboard and with touch. Live headlines for the current trading account follow the stream. A read-only period keeps its own read. The live mark says חי, מתחבר, or רענון.

## Do's and Don'ts

Do keep existing screens, copy, and trading behavior. Do keep the logo gold and silver. Don't stack a money figure one character per line. Don't put a tracked label above the day number. Don't use a thick colored side stripe on a card.
