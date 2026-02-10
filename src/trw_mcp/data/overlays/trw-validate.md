## VALIDATE PHASE OVERLAY (v18.1_TRW)

This overlay augments the shared core with validation-specific content.

---

### GATES

```
VALIDATE/DELIVER boundary?
├─ YES → FULL GATE (≥quorum judges, pairwise+rubric)
└─ NO → PLAN/REVIEW decision?
        ├─ YES → LIGHT GATE (2 judges, rubric only)
        └─ NO → Quality contested?
                ├─ YES → SPAWN CRITIC
                └─ NO → NO GATE (checkpoint only)
```

Rubric: correctness 35, tests 20, security 15, performance 10, maintainability 10, completeness 10.
Pass: `consensus ≥ quorum` AND `correlation ≥ CORRELATION_MIN`.
Fail: document reasons → revert to prior phase (add tests, refactor, fix) → retry gate. Two consecutive gate failures → escalate to user.

---

### REQUIREMENTS (Post-Development Traceability)

Before DELIVER:
```yaml
requirements_traceability:
  - req_id: REQ-001
    implemented_in: [src/auth/login.py]
    verified_by: [tests/test_auth.py::test_login]
    status: PASS
```

### AARE-F Tools

When `MCP_MODE: tool` and AARE-F framework file exists: `trw_traceability_check` at VALIDATE/DELIVER.

---

### RISK REGISTRY

`validation/risk-register.yaml` — each risk: `{id, description, impact, likelihood, mitigation, status: open|mitigated|accepted}`.

---

### Validation Testing Strategy

| Phase | Testing Activity |
|-------|-----------------|
| VALIDATE | Full test suite; coverage check; regression sweep |
| Coverage gate | global ≥85%, diff ≥90% |
