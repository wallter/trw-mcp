---
prd:
  id: PRD-TEST-904
  title: "Unwired public surface, no seam — emits a wiring_gate_warning"
  version: "1.0"
  status: draft
  priority: P1
  category: CORE

ip_tier: public
functionality_level: planned
stubs: []
---

# PRD-TEST-904: Unwired public surface, no seam

## 1. Problem Statement

A Must-Have public surface declares no `consumer:` / `wiring_test:` field and
the PRD carries no `seams:` block, so the wiring gate must emit a
`wiring_gate_warning` (default warn mode) — no silent pass.

## 3. Functional Requirements

### FR01 — Public surface with no consumer and no covering seam
**Priority**: Must Have

surface: public

This FR is a public surface with no wiring declaration and no seam coverage.
