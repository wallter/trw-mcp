# /trw-sprint-init

Create a sprint coordination contract and resumable TRW run only for an explicitly requested sprint. Ordinary work does not require a sprint; do not launch implementation.

1. Resolve the PRD catalogue and sprint locations from `.trw/config.yaml`, using project conventions when paths differ.
2. Present exact candidate PRDs and inspection evidence; do not confuse document quality with lifecycle status or infer completion from identifier counts.
3. Read and reference each selected PRD's accepted governing plan by path and task IDs or section anchors, including project-required separate plans. Do not recreate per-PRD tasks, status, ownership, dependencies, or verification commands. Resolve missing/unaccepted plans through the existing governing artifact and readiness workflow before scheduling them as ready; selection grants no approval. Surface conflicts rather than overriding authority. Confirm scope and execution mode.
4. Write one active sprint contract with PRD IDs, run identity/path, project-derived aggregate/manual acceptance and optional/null coverage policy. Add only cross-PRD ordering, shared-file coordination and integration ownership not already governed by referenced plans. Preserve project-required separate formats and existing sprint artifacts; do not auto-migrate them.
5. Call `trw_init` with the selected PRD scope, insert the returned run path, and checkpoint the contract and plan references. Keep per-PRD progress in its governing artifact; resolve the next task and verification from those references after resume.
6. Report the sprint path, run path, conflicts, and next approval/action.

Delegation is optional and harness/policy-dependent; do not launch helpers automatically. Shared files or ordered interfaces stay sequential. A sequential plan is always valid.
