"""Proactive posts: holiday notes and the #it-help pulse check (ADR-020).

Text comes only from code templates, never a model. A pure scheduler decides what is
due; a runner sends it through the output guard, behind a 24-hour preview with a cancel
button, a one-broadcast-per-day limit, a kill switch and dry-run mode (on by default).
"""
