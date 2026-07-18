# /trw-sprint-init

Create a sprint contract and resumable TRW run; do not launch implementation.

1. Resolve the PRD catalogue and sprint locations from `.trw/config.yaml`, using project conventions when paths differ.
2. Present exact candidate PRDs and inspection evidence; do not confuse document quality with lifecycle status or infer completion from identifier counts.
3. Confirm the selected PRDs, dependencies, owned files, and execution mode.
4. Write one active sprint contract with project-derived gates and optional/null coverage policy.
5. Call `trw_init` with the selected PRD scope, insert the returned run path, and checkpoint the contract.
6. Report the sprint path, run path, conflicts, and next approval/action.

Delegation is optional and harness/policy-dependent. A sequential plan is always valid.
