---
prd:
  id: PRD-TEST-901
  title: "Valid current seam suppresses the wiring gate"
  version: "1.0"
  status: draft
  priority: P1
  category: CORE

ip_tier: public
functionality_level: planned
stubs: []

seams:
  - kind: deferred
    target_prd: PRD-TEST-999
    owner: platform-team
    expiry_date: 2099-12-31
    description: "Consumer lands in the follow-on PRD."
---

# PRD-TEST-901: Valid current seam

## 1. Problem Statement

A public surface is declared but its consumer ships in a follow-on PRD.

## 3. Functional Requirements

### FR01 — Public surface with no consumer yet
**Priority**: Must Have

This FR declares a public surface but has no `consumer:` or `wiring_test:`
field. It is covered by the valid, current seam entry in frontmatter.

surface: public
