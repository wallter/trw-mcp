"""CI-gate tests — PRD-HPO-MEAS-001 NFR-7/NFR-8/NFR-10/NFR-11.

Tests in this directory are wired into ``make check`` and block merge to
``main`` on any failure. They are the contractual gates for the H1
measurement substrate: emitter coverage (NFR-8), field population
(NFR-7), nullable-by-design parity (NFR-10), and per-run isolation
(NFR-11).
"""
