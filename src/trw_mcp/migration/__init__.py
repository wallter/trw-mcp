"""Migration utilities — one-shot data-shape converters.

Each submodule is a one-shot migration — runs idempotently against the
target artifact set, reports what it changed, and does not require
coordination across sessions.
"""
