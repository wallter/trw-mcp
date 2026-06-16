---
prd:
  id: PRD-TEST-902
  title: "Expired seam fails ci-seam-expiry"
  version: "1.0"
  status: draft
  priority: P1
  category: CORE

ip_tier: public
functionality_level: planned
stubs: []

seams:
  - kind: unimplemented
    target_prd: PRD-TEST-999
    owner: platform-team
    expiry_date: 2020-01-01
    description: "This seam is long overdue."
---

# PRD-TEST-902: Expired seam

## 1. Problem Statement

A seam entry whose expiry date is in the past should fail the CI expiry gate.

## 3. Functional Requirements

### FR01 — Public surface covered by an expired seam
**Priority**: Must Have

surface: public
