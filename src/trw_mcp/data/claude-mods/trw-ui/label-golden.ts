// Golden label cases shared by the mod's tests (register.test.ts) and the Python
// renderer's tests (trw-mcp/tests/test_status_line_render.py parses the JSON literal
// assigned to GOLDEN below). One table, so the two renderers cannot drift.
export const GOLDEN = {
  "cases": [
    {
      "name": "degraded wins",
      "snapshot": {
        "schema_version": 1,
        "generated_at": "2026-10-04T01:12:30.512345+00:00",
        "session_id": "b2ecd4c9-1ad3-4978-83b3-a9424cece258",
        "client": "claude-code",
        "run": {
          "state": "ok",
          "run_id": "20261004T001509Z-7eabc79e",
          "task": "claude-code-status-and-mods",
          "phase": "implement",
          "status": "active",
          "run_path": "/home/dev/project/.trw/runs/claude-code-status-and-mods/20261004T001509Z-7eabc79e",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "checkpoint": {
          "state": "ok",
          "count": 4,
          "last_ts": "2026-10-04T01:00:02.100000+00:00",
          "age_s": 748,
          "scope": "run",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "evidence": {
          "build": {
            "state": "passed",
            "scope": "run",
            "ts": "2026-10-04T00:58:11.000000+00:00",
            "test_count": 57,
            "build_scope": "targeted: tests/test_status_snapshot.py tests/test_status_line_render.py"
          },
          "review": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "deliver": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "gate_preview": {
          "state": "ready",
          "summary": "READY (advisory: no review recorded — run trw_review())",
          "preview": true,
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "project_aggregate": {
          "build_check_result": "passed",
          "review_verdict": "block",
          "deliver_called": false,
          "scope": "project_aggregate"
        },
        "inbox": {
          "state": "ok",
          "pending": 2,
          "formation_id": "status-surfaces",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "degraded": {
          "state": "yes"
        },
        "unknown": []
      },
      "width": null,
      "expect": "TRW ⚠ MCP not seen"
    },
    {
      "name": "no run",
      "snapshot": {
        "schema_version": 1,
        "generated_at": "2026-10-04T01:12:30.512345+00:00",
        "session_id": "b2ecd4c9-1ad3-4978-83b3-a9424cece258",
        "client": "claude-code",
        "run": {
          "state": "none",
          "run_id": "20261004T001509Z-7eabc79e",
          "task": "claude-code-status-and-mods",
          "phase": "implement",
          "status": "active",
          "run_path": "/home/dev/project/.trw/runs/claude-code-status-and-mods/20261004T001509Z-7eabc79e",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "checkpoint": {
          "state": "ok",
          "count": 4,
          "last_ts": "2026-10-04T01:00:02.100000+00:00",
          "age_s": 748,
          "scope": "run",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "evidence": {
          "build": {
            "state": "passed",
            "scope": "run",
            "ts": "2026-10-04T00:58:11.000000+00:00",
            "test_count": 57,
            "build_scope": "targeted: tests/test_status_snapshot.py tests/test_status_line_render.py"
          },
          "review": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "deliver": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "gate_preview": {
          "state": "ready",
          "summary": "READY (advisory: no review recorded — run trw_review())",
          "preview": true,
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "project_aggregate": {
          "build_check_result": "passed",
          "review_verdict": "block",
          "deliver_called": false,
          "scope": "project_aggregate"
        },
        "inbox": {
          "state": "ok",
          "pending": 2,
          "formation_id": "status-surfaces",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "degraded": {
          "state": "no"
        },
        "unknown": []
      },
      "width": null,
      "expect": "TRW"
    },
    {
      "name": "unknown run",
      "snapshot": {
        "schema_version": 1,
        "generated_at": "2026-10-04T01:12:30.512345+00:00",
        "session_id": "b2ecd4c9-1ad3-4978-83b3-a9424cece258",
        "client": "claude-code",
        "run": {
          "state": "unknown",
          "run_id": "20261004T001509Z-7eabc79e",
          "task": "claude-code-status-and-mods",
          "phase": "implement",
          "status": "active",
          "run_path": "/home/dev/project/.trw/runs/claude-code-status-and-mods/20261004T001509Z-7eabc79e",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "checkpoint": {
          "state": "ok",
          "count": 4,
          "last_ts": "2026-10-04T01:00:02.100000+00:00",
          "age_s": 748,
          "scope": "run",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "evidence": {
          "build": {
            "state": "passed",
            "scope": "run",
            "ts": "2026-10-04T00:58:11.000000+00:00",
            "test_count": 57,
            "build_scope": "targeted: tests/test_status_snapshot.py tests/test_status_line_render.py"
          },
          "review": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "deliver": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "gate_preview": {
          "state": "ready",
          "summary": "READY (advisory: no review recorded — run trw_review())",
          "preview": true,
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "project_aggregate": {
          "build_check_result": "passed",
          "review_verdict": "block",
          "deliver_called": false,
          "scope": "project_aggregate"
        },
        "inbox": {
          "state": "ok",
          "pending": 2,
          "formation_id": "status-surfaces",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "degraded": {
          "state": "no"
        },
        "unknown": []
      },
      "width": null,
      "expect": "TRW ?"
    },
    {
      "name": "active with inbox",
      "snapshot": {
        "schema_version": 1,
        "generated_at": "2026-10-04T01:12:30.512345+00:00",
        "session_id": "b2ecd4c9-1ad3-4978-83b3-a9424cece258",
        "client": "claude-code",
        "run": {
          "state": "ok",
          "run_id": "20261004T001509Z-7eabc79e",
          "task": "claude-code-status-and-mods",
          "phase": "implement",
          "status": "active",
          "run_path": "/home/dev/project/.trw/runs/claude-code-status-and-mods/20261004T001509Z-7eabc79e",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "checkpoint": {
          "state": "ok",
          "count": 4,
          "last_ts": "2026-10-04T01:00:02.100000+00:00",
          "age_s": 748,
          "scope": "run",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "evidence": {
          "build": {
            "state": "passed",
            "scope": "run",
            "ts": "2026-10-04T00:58:11.000000+00:00",
            "test_count": 57,
            "build_scope": "targeted: tests/test_status_snapshot.py tests/test_status_line_render.py"
          },
          "review": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "deliver": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "gate_preview": {
          "state": "ready",
          "summary": "READY (advisory: no review recorded — run trw_review())",
          "preview": true,
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "project_aggregate": {
          "build_check_result": "passed",
          "review_verdict": "block",
          "deliver_called": false,
          "scope": "project_aggregate"
        },
        "inbox": {
          "state": "ok",
          "pending": 2,
          "formation_id": "status-surfaces",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "degraded": {
          "state": "no"
        },
        "unknown": []
      },
      "width": null,
      "expect": "TRW ▸ implement · claude-code-status-and-… · ✉2"
    },
    {
      "name": "active, empty inbox",
      "snapshot": {
        "schema_version": 1,
        "generated_at": "2026-10-04T01:12:30.512345+00:00",
        "session_id": "b2ecd4c9-1ad3-4978-83b3-a9424cece258",
        "client": "claude-code",
        "run": {
          "state": "ok",
          "run_id": "20261004T001509Z-7eabc79e",
          "task": "claude-code-status-and-mods",
          "phase": "implement",
          "status": "active",
          "run_path": "/home/dev/project/.trw/runs/claude-code-status-and-mods/20261004T001509Z-7eabc79e",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "checkpoint": {
          "state": "ok",
          "count": 4,
          "last_ts": "2026-10-04T01:00:02.100000+00:00",
          "age_s": 748,
          "scope": "run",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "evidence": {
          "build": {
            "state": "passed",
            "scope": "run",
            "ts": "2026-10-04T00:58:11.000000+00:00",
            "test_count": 57,
            "build_scope": "targeted: tests/test_status_snapshot.py tests/test_status_line_render.py"
          },
          "review": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "deliver": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "gate_preview": {
          "state": "ready",
          "summary": "READY (advisory: no review recorded — run trw_review())",
          "preview": true,
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "project_aggregate": {
          "build_check_result": "passed",
          "review_verdict": "block",
          "deliver_called": false,
          "scope": "project_aggregate"
        },
        "inbox": {
          "state": "ok",
          "pending": 0,
          "formation_id": "status-surfaces",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "degraded": {
          "state": "no"
        },
        "unknown": []
      },
      "width": null,
      "expect": "TRW ▸ implement · claude-code-status-and-…"
    },
    {
      "name": "inbox count ignored unless state ok",
      "snapshot": {
        "schema_version": 1,
        "generated_at": "2026-10-04T01:12:30.512345+00:00",
        "session_id": "b2ecd4c9-1ad3-4978-83b3-a9424cece258",
        "client": "claude-code",
        "run": {
          "state": "ok",
          "run_id": "20261004T001509Z-7eabc79e",
          "task": "claude-code-status-and-mods",
          "phase": "implement",
          "status": "active",
          "run_path": "/home/dev/project/.trw/runs/claude-code-status-and-mods/20261004T001509Z-7eabc79e",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "checkpoint": {
          "state": "ok",
          "count": 4,
          "last_ts": "2026-10-04T01:00:02.100000+00:00",
          "age_s": 748,
          "scope": "run",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "evidence": {
          "build": {
            "state": "passed",
            "scope": "run",
            "ts": "2026-10-04T00:58:11.000000+00:00",
            "test_count": 57,
            "build_scope": "targeted: tests/test_status_snapshot.py tests/test_status_line_render.py"
          },
          "review": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "deliver": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "gate_preview": {
          "state": "ready",
          "summary": "READY (advisory: no review recorded — run trw_review())",
          "preview": true,
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "project_aggregate": {
          "build_check_result": "passed",
          "review_verdict": "block",
          "deliver_called": false,
          "scope": "project_aggregate"
        },
        "inbox": {
          "state": "none",
          "pending": 3,
          "formation_id": "status-surfaces",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "degraded": {
          "state": "no"
        },
        "unknown": []
      },
      "width": null,
      "expect": "TRW ▸ implement · claude-code-status-and-…"
    },
    {
      "name": "delivered",
      "snapshot": {
        "schema_version": 1,
        "generated_at": "2026-10-04T01:12:30.512345+00:00",
        "session_id": "b2ecd4c9-1ad3-4978-83b3-a9424cece258",
        "client": "claude-code",
        "run": {
          "state": "ok",
          "run_id": "20261004T001509Z-7eabc79e",
          "task": "claude-code-status-and-mods",
          "phase": "implement",
          "status": "active",
          "run_path": "/home/dev/project/.trw/runs/claude-code-status-and-mods/20261004T001509Z-7eabc79e",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "checkpoint": {
          "state": "ok",
          "count": 4,
          "last_ts": "2026-10-04T01:00:02.100000+00:00",
          "age_s": 748,
          "scope": "run",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "evidence": {
          "build": {
            "state": "passed",
            "scope": "run",
            "ts": "2026-10-04T00:58:11.000000+00:00",
            "test_count": 57,
            "build_scope": "targeted: tests/test_status_snapshot.py tests/test_status_line_render.py"
          },
          "review": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "deliver": {
            "state": "called",
            "scope": "run",
            "ts": null
          },
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "gate_preview": {
          "state": "ready",
          "summary": "READY (advisory: no review recorded — run trw_review())",
          "preview": true,
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "project_aggregate": {
          "build_check_result": "passed",
          "review_verdict": "block",
          "deliver_called": false,
          "scope": "project_aggregate"
        },
        "inbox": {
          "state": "ok",
          "pending": 0,
          "formation_id": "status-surfaces",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "degraded": {
          "state": "no"
        },
        "unknown": []
      },
      "width": null,
      "expect": "TRW ✓ claude-code-status-and-…"
    },
    {
      "name": "delivered keeps exceptions",
      "snapshot": {
        "schema_version": 1,
        "generated_at": "2026-10-04T01:12:30.512345+00:00",
        "session_id": "b2ecd4c9-1ad3-4978-83b3-a9424cece258",
        "client": "claude-code",
        "run": {
          "state": "ok",
          "run_id": "20261004T001509Z-7eabc79e",
          "task": "claude-code-status-and-mods",
          "phase": "implement",
          "status": "active",
          "run_path": "/home/dev/project/.trw/runs/claude-code-status-and-mods/20261004T001509Z-7eabc79e",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "checkpoint": {
          "state": "ok",
          "count": 4,
          "last_ts": "2026-10-04T01:00:02.100000+00:00",
          "age_s": 748,
          "scope": "run",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "evidence": {
          "build": {
            "state": "passed",
            "scope": "run",
            "ts": "2026-10-04T00:58:11.000000+00:00",
            "test_count": 57,
            "build_scope": "targeted: tests/test_status_snapshot.py tests/test_status_line_render.py"
          },
          "review": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "deliver": {
            "state": "called",
            "scope": "run",
            "ts": null
          },
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "gate_preview": {
          "state": "ready",
          "summary": "READY (advisory: no review recorded — run trw_review())",
          "preview": true,
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "project_aggregate": {
          "build_check_result": "passed",
          "review_verdict": "block",
          "deliver_called": false,
          "scope": "project_aggregate"
        },
        "inbox": {
          "state": "ok",
          "pending": 2,
          "formation_id": "status-surfaces",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "degraded": {
          "state": "no"
        },
        "unknown": []
      },
      "width": null,
      "expect": "TRW ✓ claude-code-status-and-… · ✉2"
    },
    {
      "name": "failures",
      "snapshot": {
        "schema_version": 1,
        "generated_at": "2026-10-04T01:12:30.512345+00:00",
        "session_id": "b2ecd4c9-1ad3-4978-83b3-a9424cece258",
        "client": "claude-code",
        "run": {
          "state": "ok",
          "run_id": "20261004T001509Z-7eabc79e",
          "task": "fix",
          "phase": "implement",
          "status": "active",
          "run_path": "/home/dev/project/.trw/runs/claude-code-status-and-mods/20261004T001509Z-7eabc79e",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "checkpoint": {
          "state": "ok",
          "count": 4,
          "last_ts": "2026-10-04T01:00:02.100000+00:00",
          "age_s": 748,
          "scope": "run",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "evidence": {
          "build": {
            "state": "failed",
            "scope": "run",
            "ts": null,
            "test_count": null,
            "build_scope": null
          },
          "review": {
            "state": "block",
            "scope": "run",
            "ts": null
          },
          "deliver": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "gate_preview": {
          "state": "ready",
          "summary": "READY (advisory: no review recorded — run trw_review())",
          "preview": true,
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "project_aggregate": {
          "build_check_result": "passed",
          "review_verdict": "block",
          "deliver_called": false,
          "scope": "project_aggregate"
        },
        "inbox": {
          "state": "ok",
          "pending": 0,
          "formation_id": "status-surfaces",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "degraded": {
          "state": "no"
        },
        "unknown": []
      },
      "width": null,
      "expect": "TRW ▸ implement · fix · build ✗ · review ✗"
    },
    {
      "name": "unscoped failure is not shown",
      "snapshot": {
        "schema_version": 1,
        "generated_at": "2026-10-04T01:12:30.512345+00:00",
        "session_id": "b2ecd4c9-1ad3-4978-83b3-a9424cece258",
        "client": "claude-code",
        "run": {
          "state": "ok",
          "run_id": "20261004T001509Z-7eabc79e",
          "task": "fix",
          "phase": "implement",
          "status": "active",
          "run_path": "/home/dev/project/.trw/runs/claude-code-status-and-mods/20261004T001509Z-7eabc79e",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "checkpoint": {
          "state": "ok",
          "count": 4,
          "last_ts": "2026-10-04T01:00:02.100000+00:00",
          "age_s": 748,
          "scope": "run",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "evidence": {
          "build": {
            "state": "failed",
            "scope": "unknown",
            "ts": null,
            "test_count": null,
            "build_scope": null
          },
          "review": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "deliver": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "gate_preview": {
          "state": "ready",
          "summary": "READY (advisory: no review recorded — run trw_review())",
          "preview": true,
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "project_aggregate": {
          "build_check_result": "passed",
          "review_verdict": "block",
          "deliver_called": false,
          "scope": "project_aggregate"
        },
        "inbox": {
          "state": "ok",
          "pending": 0,
          "formation_id": "status-surfaces",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "degraded": {
          "state": "no"
        },
        "unknown": []
      },
      "width": null,
      "expect": "TRW ▸ implement · fix"
    },
    {
      "name": "missing phase",
      "snapshot": {
        "schema_version": 1,
        "generated_at": "2026-10-04T01:12:30.512345+00:00",
        "session_id": "b2ecd4c9-1ad3-4978-83b3-a9424cece258",
        "client": "claude-code",
        "run": {
          "state": "ok",
          "run_id": "20261004T001509Z-7eabc79e",
          "task": "fix",
          "phase": "",
          "status": "active",
          "run_path": "/home/dev/project/.trw/runs/claude-code-status-and-mods/20261004T001509Z-7eabc79e",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "checkpoint": {
          "state": "ok",
          "count": 4,
          "last_ts": "2026-10-04T01:00:02.100000+00:00",
          "age_s": 748,
          "scope": "run",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "evidence": {
          "build": {
            "state": "passed",
            "scope": "run",
            "ts": "2026-10-04T00:58:11.000000+00:00",
            "test_count": 57,
            "build_scope": "targeted: tests/test_status_snapshot.py tests/test_status_line_render.py"
          },
          "review": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "deliver": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "gate_preview": {
          "state": "ready",
          "summary": "READY (advisory: no review recorded — run trw_review())",
          "preview": true,
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "project_aggregate": {
          "build_check_result": "passed",
          "review_verdict": "block",
          "deliver_called": false,
          "scope": "project_aggregate"
        },
        "inbox": {
          "state": "ok",
          "pending": 0,
          "formation_id": "status-surfaces",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "degraded": {
          "state": "no"
        },
        "unknown": []
      },
      "width": null,
      "expect": "TRW ▸ ? · fix"
    },
    {
      "name": "control characters cleaned",
      "snapshot": {
        "schema_version": 1,
        "generated_at": "2026-10-04T01:12:30.512345+00:00",
        "session_id": "b2ecd4c9-1ad3-4978-83b3-a9424cece258",
        "client": "claude-code",
        "run": {
          "state": "ok",
          "run_id": "20261004T001509Z-7eabc79e",
          "task": "a\u001b[31mb\u0007  c",
          "phase": "implement",
          "status": "active",
          "run_path": "/home/dev/project/.trw/runs/claude-code-status-and-mods/20261004T001509Z-7eabc79e",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "checkpoint": {
          "state": "ok",
          "count": 4,
          "last_ts": "2026-10-04T01:00:02.100000+00:00",
          "age_s": 748,
          "scope": "run",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "evidence": {
          "build": {
            "state": "passed",
            "scope": "run",
            "ts": "2026-10-04T00:58:11.000000+00:00",
            "test_count": 57,
            "build_scope": "targeted: tests/test_status_snapshot.py tests/test_status_line_render.py"
          },
          "review": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "deliver": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "gate_preview": {
          "state": "ready",
          "summary": "READY (advisory: no review recorded — run trw_review())",
          "preview": true,
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "project_aggregate": {
          "build_check_result": "passed",
          "review_verdict": "block",
          "deliver_called": false,
          "scope": "project_aggregate"
        },
        "inbox": {
          "state": "ok",
          "pending": 0,
          "formation_id": "status-surfaces",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "degraded": {
          "state": "no"
        },
        "unknown": []
      },
      "width": null,
      "expect": "TRW ▸ implement · a [31mb c"
    },
    {
      "name": "width: drop task first",
      "snapshot": {
        "schema_version": 1,
        "generated_at": "2026-10-04T01:12:30.512345+00:00",
        "session_id": "b2ecd4c9-1ad3-4978-83b3-a9424cece258",
        "client": "claude-code",
        "run": {
          "state": "ok",
          "run_id": "20261004T001509Z-7eabc79e",
          "task": "claude-code-status-and-mods",
          "phase": "implement",
          "status": "active",
          "run_path": "/home/dev/project/.trw/runs/claude-code-status-and-mods/20261004T001509Z-7eabc79e",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "checkpoint": {
          "state": "ok",
          "count": 4,
          "last_ts": "2026-10-04T01:00:02.100000+00:00",
          "age_s": 748,
          "scope": "run",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "evidence": {
          "build": {
            "state": "passed",
            "scope": "run",
            "ts": "2026-10-04T00:58:11.000000+00:00",
            "test_count": 57,
            "build_scope": "targeted: tests/test_status_snapshot.py tests/test_status_line_render.py"
          },
          "review": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "deliver": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "gate_preview": {
          "state": "ready",
          "summary": "READY (advisory: no review recorded — run trw_review())",
          "preview": true,
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "project_aggregate": {
          "build_check_result": "passed",
          "review_verdict": "block",
          "deliver_called": false,
          "scope": "project_aggregate"
        },
        "inbox": {
          "state": "ok",
          "pending": 2,
          "formation_id": "status-surfaces",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "degraded": {
          "state": "no"
        },
        "unknown": []
      },
      "width": 24,
      "expect": "TRW ▸ implement · ✉2"
    },
    {
      "name": "width: then drop phase, keep exceptions",
      "snapshot": {
        "schema_version": 1,
        "generated_at": "2026-10-04T01:12:30.512345+00:00",
        "session_id": "b2ecd4c9-1ad3-4978-83b3-a9424cece258",
        "client": "claude-code",
        "run": {
          "state": "ok",
          "run_id": "20261004T001509Z-7eabc79e",
          "task": "fix",
          "phase": "implement",
          "status": "active",
          "run_path": "/home/dev/project/.trw/runs/claude-code-status-and-mods/20261004T001509Z-7eabc79e",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "checkpoint": {
          "state": "ok",
          "count": 4,
          "last_ts": "2026-10-04T01:00:02.100000+00:00",
          "age_s": 748,
          "scope": "run",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "evidence": {
          "build": {
            "state": "failed",
            "scope": "run",
            "ts": null,
            "test_count": null,
            "build_scope": null
          },
          "review": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "deliver": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "gate_preview": {
          "state": "ready",
          "summary": "READY (advisory: no review recorded — run trw_review())",
          "preview": true,
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "project_aggregate": {
          "build_check_result": "passed",
          "review_verdict": "block",
          "deliver_called": false,
          "scope": "project_aggregate"
        },
        "inbox": {
          "state": "ok",
          "pending": 2,
          "formation_id": "status-surfaces",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "degraded": {
          "state": "no"
        },
        "unknown": []
      },
      "width": 22,
      "expect": "TRW · build ✗ · ✉2"
    },
    {
      "name": "width: clip the tail last",
      "snapshot": {
        "schema_version": 1,
        "generated_at": "2026-10-04T01:12:30.512345+00:00",
        "session_id": "b2ecd4c9-1ad3-4978-83b3-a9424cece258",
        "client": "claude-code",
        "run": {
          "state": "ok",
          "run_id": "20261004T001509Z-7eabc79e",
          "task": "claude-code-status-and-mods",
          "phase": "implement",
          "status": "active",
          "run_path": "/home/dev/project/.trw/runs/claude-code-status-and-mods/20261004T001509Z-7eabc79e",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "checkpoint": {
          "state": "ok",
          "count": 4,
          "last_ts": "2026-10-04T01:00:02.100000+00:00",
          "age_s": 748,
          "scope": "run",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "evidence": {
          "build": {
            "state": "failed",
            "scope": "run",
            "ts": null,
            "test_count": null,
            "build_scope": null
          },
          "review": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "deliver": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "gate_preview": {
          "state": "ready",
          "summary": "READY (advisory: no review recorded — run trw_review())",
          "preview": true,
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "project_aggregate": {
          "build_check_result": "passed",
          "review_verdict": "block",
          "deliver_called": false,
          "scope": "project_aggregate"
        },
        "inbox": {
          "state": "ok",
          "pending": 2,
          "formation_id": "status-surfaces",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "degraded": {
          "state": "no"
        },
        "unknown": []
      },
      "width": 12,
      "expect": "TRW · build…"
    },
    {
      "name": "width: no ellipsis when it fits",
      "snapshot": {
        "schema_version": 1,
        "generated_at": "2026-10-04T01:12:30.512345+00:00",
        "session_id": "b2ecd4c9-1ad3-4978-83b3-a9424cece258",
        "client": "claude-code",
        "run": {
          "state": "none",
          "run_id": "20261004T001509Z-7eabc79e",
          "task": "claude-code-status-and-mods",
          "phase": "implement",
          "status": "active",
          "run_path": "/home/dev/project/.trw/runs/claude-code-status-and-mods/20261004T001509Z-7eabc79e",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "checkpoint": {
          "state": "ok",
          "count": 4,
          "last_ts": "2026-10-04T01:00:02.100000+00:00",
          "age_s": 748,
          "scope": "run",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "evidence": {
          "build": {
            "state": "passed",
            "scope": "run",
            "ts": "2026-10-04T00:58:11.000000+00:00",
            "test_count": 57,
            "build_scope": "targeted: tests/test_status_snapshot.py tests/test_status_line_render.py"
          },
          "review": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "deliver": {
            "state": "none",
            "scope": "run",
            "ts": null
          },
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "gate_preview": {
          "state": "ready",
          "summary": "READY (advisory: no review recorded — run trw_review())",
          "preview": true,
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "project_aggregate": {
          "build_check_result": "passed",
          "review_verdict": "block",
          "deliver_called": false,
          "scope": "project_aggregate"
        },
        "inbox": {
          "state": "ok",
          "pending": 2,
          "formation_id": "status-surfaces",
          "as_of": "2026-10-04T01:12:30.512345+00:00"
        },
        "degraded": {
          "state": "no"
        },
        "unknown": []
      },
      "width": 3,
      "expect": "TRW"
    }
  ]
}
