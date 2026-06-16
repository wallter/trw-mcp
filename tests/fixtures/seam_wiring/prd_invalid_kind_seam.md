---
prd:
  id: PRD-TEST-905
  title: "Seam with an out-of-enum kind — rejected by both parsers"
  version: "1.0"
  status: draft
  priority: P1
  category: CORE

ip_tier: public
functionality_level: planned
stubs: []

seams:
  - kind: not-a-real-kind
    target_prd: PRD-TEST-999
    owner: platform-team
    expiry_date: 2099-12-31
    description: "kind is out of the allowed Literal set."
---

# PRD-TEST-905: Invalid seam kind

## 1. Problem Statement

A seam whose `kind` is outside the allowed set must be rejected consistently by
both the Pydantic `SeamEntry` parser and the standalone CI script's kind check.

## 3. Functional Requirements

### FR01 — Public surface covered (badly) by an invalid-kind seam
**Priority**: Must Have

surface: public
