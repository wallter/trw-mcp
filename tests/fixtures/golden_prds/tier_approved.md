---
prd:
  id: PRD-FIX-137
  title: Non-Linux hosts force meta-tune off instead of aborting trw-mcp boot
  version: '1.0'
  status: implemented
  priority: P0
  category: FIX
  risk_level: medium
  ip_tier: public
  ip_rationale: All changes land in trw-mcp (BSL-1.1 public); no proprietary package is touched.
  safety_critical: true
  evidence:
    level: strong
    sources:
    - 'Reproduced 2026-09-16 on an arm64 macOS checkout: `.venv/bin/trw-mcp --version` raised MetaTuneBootValidationError "sandbox: platform=Darwin" because the committed .trw/config.yaml carries meta_tune_enabled: true from the Linux GPU box'
    - trw-mcp/src/trw_mcp/meta_tune/boot_checks.py:98-106 (_validate_sandbox refuses platform != Linux)
    - trw-mcp/src/trw_mcp/server/_app.py:226-243 (_run_meta_tune_boot_validation raises from _build_middleware, i.e. at import of trw_mcp.server)
    - trw-mcp/src/trw_mcp/models/config/_loader.py (single config cascade shared by the server and the doctor)
    - learning L-w4SX (project memory store)
  confidence:
    implementation_feasibility: 0.98
    requirement_clarity: 0.95
    estimate_confidence: 0.9
    test_coverage_target: 0.95
  traceability:
    implements:
    - PRD-HPO-SAFE-001 fail-safe intent (no meta-tune without a sandbox) on hosts that cannot provide one
    depends_on: []
    enables:
    - macOS / Windows development checkouts of a Linux-tuned project
    - PRD-FIX-139
  metrics:
    success_criteria:
    - trw-mcp boots (serve, doctor, --version) on Darwin with meta_tune_enabled: true in the project config, with meta_tune reported disabled and one WARNING naming the override.
    - Linux behaviour is byte-identical (fail-loud validator still runs).
    measurement_method:
    - Unit tests in trw-mcp/tests/unit/meta_tune/test_platform_gate.py plus a manual boot on the macOS checkout.
  quality_gates:
    ambiguity_rate_max: 0.05
    completeness_min: 0.85
    traceability_coverage_min: 0.9
    consistency_validation_min: 0.95
  verification:
    mappings:
    - requirement_id: PRD-FIX-137-FR01
      acceptance_criteria:
      - On a non-Linux host, a resolved config with meta_tune.enabled=true yields meta_tune.enabled=false and meta_tune_enabled=false, and exactly one WARNING event meta_tune_disabled_unsupported_platform is emitted naming the platform and the remedy.
      method: test
      evidence_artifact: trw-mcp/tests/unit/meta_tune/test_platform_gate.py::TestApplyPlatformMetaTuneGate
      pass_condition: All assertions pass.
    - requirement_id: PRD-FIX-137-FR02
      acceptance_criteria:
      - On Linux the gate is a no-op; the fail-loud validator in boot_checks is unchanged and still raises for a missing sandbox extra.
      method: test
      evidence_artifact: trw-mcp/tests/unit/meta_tune/test_platform_gate.py::TestApplyPlatformMetaTuneGate::test_linux_is_left_untouched_so_the_fail_loud_validator_still_runs; trw-mcp/tests/unit/meta_tune/test_boot_checks.py
      pass_condition: All assertions pass.
    - requirement_id: PRD-FIX-137-FR03
      acceptance_criteria:
      - Both config construction paths (get_config singleton and the doctor's _resolve_target_config) apply the gate, so the doctor reports the config the server runs with.
      method: test
      evidence_artifact: trw-mcp/tests/unit/meta_tune/test_platform_gate.py::TestGateIsWiredIntoEveryConfigConstructionPath
      pass_condition: All assertions pass.
    - requirement_id: PRD-FIX-137-NFR01
      acceptance_criteria:
      - No new dependency, no config key, no change to the SAFE-001 validator; the gate adds one platform.system() call per config build.
      method: inspection
      evidence_artifact: git diff of trw-mcp/src/trw_mcp/models/config/_loader.py and trw-mcp/src/trw_mcp/server/_subcommands_doctor.py
      pass_condition: Diff touches only the two files named plus tests.
  verification_commands:
  - cd trw-mcp && ../.venv/bin/python -m pytest -q tests/unit/meta_tune/test_platform_gate.py tests/unit/meta_tune/test_boot_checks.py tests/test_app_middleware_helpers.py
  dates:
    created: '2026-09-16'
    updated: '2026-09-16'
    target_completion: '2026-09-16'
  partially_implemented_frs: []
  activation_gates: []
  template_version: '3.2'
  slos: []
---

# PRD-FIX-137: Non-Linux hosts force meta-tune off instead of aborting boot

## 1. Problem Statement

SAFE-001 (PRD-HPO-SAFE-001) validates the meta-tune defaults at boot and refuses to run
when the sandbox cannot exist. Its sandbox, `subprocess-seccomp-v1`, is Linux-only, so
`_validate_sandbox` returns a failure for every other platform. That validator is called
from `_build_middleware`, which runs when `trw_mcp.server` is imported. The result on a
macOS checkout of a project whose committed `.trw/config.yaml` says `meta_tune_enabled:
true` (this monorepo, tuned on an x86 Linux GPU box) is that **every** entry point dies:
`trw-mcp --version`, `trw-mcp doctor`, and the MCP server the client spawns over stdio.
The MCP client then reports the server as absent and every `trw_*` tool as unavailable,
which reads as "TRW is not installed", not as "meta-tune cannot run here".

The property SAFE-001 protects is *no meta-tune without a sandbox*. Disabling meta-tune
on such a host preserves that property exactly. Aborting the process only adds collateral
and, worse, hides the real cause behind an installer symptom. Reproduced 2026-09-16 while
setting up the maintainer's arm64 MacBook; the workaround (`TRW_META_TUNE_ENABLED=false`
in `.mcp.json`) works because env beats file in the cascade, but nobody discovers it
without reading `_loader.py`.

## 2. Functional Requirements

### PRD-FIX-137-FR01: Force meta-tune off on non-Linux hosts with a loud warning
**Status**: active
**Priority**: Must Have
**Confidence**: [0.95]

When the resolved `TRWConfig` has `meta_tune.enabled=True` and `platform.system()` is not
`Linux`, the config loader SHALL set both `meta_tune.enabled` and the legacy mirror
`meta_tune_enabled` to `False` and emit exactly one WARNING event
`meta_tune_disabled_unsupported_platform` carrying `platform`, `required_platform` and a
`detail` that names the remedy (`meta_tune_enabled: false` or `TRW_META_TUNE_ENABLED=false`).

**Acceptance** [0.95]: `apply_platform_meta_tune_gate(cfg, system="Darwin")` disables both
flags and logs once; an already-disabled config is a silent no-op.

**Evidence**: `trw-mcp/src/trw_mcp/models/config/_loader.py::apply_platform_meta_tune_gate`.

**Assertions**:
- `grep_present` `def apply_platform_meta_tune_gate` in `trw-mcp/src/trw_mcp/models/config/_loader.py`
- `grep_present` `meta_tune_disabled_unsupported_platform` in `trw-mcp/src/trw_mcp/models/config/_loader.py`

### PRD-FIX-137-FR02: Linux keeps the fail-loud validator
**Status**: active
**Priority**: Must Have
**Confidence**: [0.95]

On Linux the gate SHALL leave the config untouched so `boot_checks.validate_defaults`
still raises for a missing `pyseccomp`, missing `unshare`, or an invalid sandbox tag. A
Linux operator who enabled meta-tune without the extra installed must be told, not
silently downgraded.

**Acceptance** [0.95]: `apply_platform_meta_tune_gate(cfg, system="Linux")` leaves
`meta_tune.enabled` True and logs nothing; the SAFE-001 test module is unchanged and green.

**Evidence**: `trw-mcp/tests/unit/meta_tune/test_boot_checks.py` (unchanged).

**Assertions**:
- `grep_present` `_META_TUNE_SANDBOX_PLATFORM = "Linux"` in `trw-mcp/src/trw_mcp/models/config/_loader.py`
- `grep_present` `platform={platform.system()}` in `trw-mcp/src/trw_mcp/meta_tune/boot_checks.py`

### PRD-FIX-137-FR03: Every config construction path applies the gate
**Status**: active
**Priority**: Must Have
**Confidence**: [0.9]

The gate SHALL be applied by `_build_config` (the `get_config()` singleton source, hence
the server, the CLI and the boot validator) and by the doctor's `_resolve_target_config`,
so the doctor reports the configuration the server would run with rather than the raw
file value.

**Acceptance** [0.9]: with `platform.system` patched to `Darwin`, `_build_config()` and
`_resolve_target_config(target)` both return `meta_tune.enabled=False` for a project file
that says `meta_tune_enabled: true`; the gated config does not trigger
`validate_meta_tune_defaults`.

**Evidence**: `trw-mcp/src/trw_mcp/models/config/_loader.py::_build_config`,

**Assertions**:
- `grep_present` `return apply_platform_meta_tune_gate(_build_config_unguarded())` in `trw-mcp/src/trw_mcp/models/config/_loader.py`
- `grep_present` `apply_platform_meta_tune_gate(TRWConfig(**overrides))` in `trw-mcp/src/trw_mcp/server/_subcommands_doctor.py`
`trw-mcp/src/trw_mcp/server/_subcommands_doctor.py::_resolve_target_config`.

## 3. Non-Functional Requirements

### PRD-FIX-137-NFR01: Surgical change
No new dependency, config key, or change to `boot_checks.py`. The gate costs one
`platform.system()` call per config build. `TRW_META_TUNE_ENABLED=true` on a non-Linux
host is still gated (there is no sandbox to opt into), which is the intended reading of
the safety property.

## 4. Test Strategy

### Unit Tests
`trw-mcp/tests/unit/meta_tune/test_platform_gate.py::TestApplyPlatformMetaTuneGate` — Darwin disables both
flags and warns once; Linux untouched; disabled config is a no-op; platform defaults to `platform.system()`.

### Integration Tests
`trw-mcp/tests/unit/meta_tune/test_platform_gate.py::TestGateIsWiredIntoEveryConfigConstructionPath` — `_build_config` and the doctor's
`_resolve_target_config` both return the gated config for a project file saying `meta_tune_enabled: true`;
the gated config never reaches `validate_meta_tune_defaults`.

### Acceptance Tests
Manual on the macOS checkout: `env -u TRW_META_TUNE_ENABLED .venv/bin/trw-mcp --version` prints the
version and `trw-mcp doctor` runs to completion (verified 2026-09-16).

### Regression Tests
`trw-mcp/tests/unit/meta_tune/test_boot_checks.py` and `trw-mcp/tests/test_app_middleware_helpers.py`
unchanged and green: the SAFE-001 validator still raises on Linux for a missing sandbox extra.

### Negative / Fallback Tests
`test_linux_is_left_untouched_so_the_fail_loud_validator_still_runs` proves the gate does not swallow the
Linux failure; `test_already_disabled_is_a_silent_no_op_on_any_platform` proves no spurious warning.

## 5. Open Questions

None. A future SAFE-001 revision that ships a macOS sandbox would simply change
`_META_TUNE_SANDBOX_PLATFORM` into a set.

## 6. Traceability Matrix

| Requirement | Source | Test |
|---|---|---|
| FR01 | `trw-mcp/src/trw_mcp/models/config/_loader.py::apply_platform_meta_tune_gate` | `trw-mcp/tests/unit/meta_tune/test_platform_gate.py::TestApplyPlatformMetaTuneGate` |
| FR02 | `trw-mcp/src/trw_mcp/models/config/_loader.py::apply_platform_meta_tune_gate` | `test_linux_is_left_untouched_so_the_fail_loud_validator_still_runs`, `trw-mcp/tests/unit/meta_tune/test_boot_checks.py` |
| FR03 | `trw-mcp/src/trw_mcp/models/config/_loader.py::_build_config`, `trw-mcp/src/trw_mcp/server/_subcommands_doctor.py::_resolve_target_config` | `trw-mcp/tests/unit/meta_tune/test_platform_gate.py::TestGateIsWiredIntoEveryConfigConstructionPath` |
| NFR01 | `trw-mcp/src/trw_mcp/models/config/_loader.py`, `trw-mcp/src/trw_mcp/server/_subcommands_doctor.py` | inspection of the diff |

## 7. Root Cause Analysis

### Root Cause
`_validate_sandbox` returns a hard failure for `platform.system() != "Linux"`, and `validate_defaults`
raises on any failure. It is called from `_build_middleware`, executed at import of `trw_mcp.server`, so a
non-Linux host with `meta_tune.enabled=true` cannot start any entry point.

### Contributing Factors
- The framework's own repository commits `.trw/config.yaml` with `meta_tune_enabled: true` (tuned on the
  Linux GPU box), so every non-Linux clone inherits the setting.
- The config cascade (`env > project file > machine file`) offers an override, but nothing at boot names it.
- The MCP client reports a dead stdio server as "tools unavailable", which reads as an installer defect.

### Fix Verification
Seven unit tests in `test_platform_gate.py`; the pre-existing SAFE-001 suite unchanged; live boot on the
macOS checkout without the env override.

## 8. Rollback Plan

### Key Files
- `trw-mcp/src/trw_mcp/models/config/_loader.py` — `apply_platform_meta_tune_gate`, `_build_config` wrapper
- `trw-mcp/src/trw_mcp/server/_subcommands_doctor.py` — `_resolve_target_config` applies the gate
- `trw-mcp/tests/unit/meta_tune/test_platform_gate.py`

### Rollback Steps
Revert the two source files and delete the test module. The `.mcp.json` env override
(`TRW_META_TUNE_ENABLED=false`) remains a valid manual workaround.

### Completion Evidence (Definition of Done)
Verification command green; `ruff check`, `ruff format --check`, `mypy` clean on the touched modules; the
macOS checkout boots without the env override.

### Migration / Backward Compatibility
No config key, schema, or CLI change. Linux behaviour is byte-identical. Non-Linux hosts that previously
crashed now run with meta-tune disabled and one WARNING.

## Execution plan

Implemented 2026-09-16 in one owner pass: gate function + `_build_config` wrapper in
`_loader.py`; doctor call site; tests. Verified with the verification command above,
`ruff check`/`ruff format --check`, `mypy` on the touched modules, and a live boot on the
macOS checkout without the env override.
