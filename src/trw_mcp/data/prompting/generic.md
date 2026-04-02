# Generic Model Prompting Guide

This guide provides best practices for generic AI coding models that don't
specifically map to Qwen, GPT, or Claude families.

## Key Strengths

- Base-level coding capabilities
- Can understand and follow clear instructions
- May have varying context windows and capabilities

## Recommended Patterns

### When to Delegate
- **Complex multi-file changes** → Use subagents or Agent Teams
- **Codebase research** → Use explore-type subagent with specific queries
- **Test-heavy tasks** → Delegate to focused tester subagent

### When Self-Implement
- **Trivial edits** (≤3 lines, 1 file) → Self-implement directly
- **Simple bug fixes** → Self-implement with clear scope
- **Immediate feedback loop needed** → Self-implement, run tests quickly

### Context Management

**Do:**
- Use `trw_session_start()` at start of each session
- Call `trw_learn()` when discovering gotchas or patterns
- Be explicit about file paths and task boundaries

### Generic Model Instructions

When delegating:
1. **Start with clear task description** - specify what needs to be done
2. **Provide specific file paths** - don't assume the model knows project structure
3. **Include test expectations** - specify test names and expected behavior
4. **Break into bounded tasks** - 1-3 files per subagent task is optimal

Example:
```
Edit `src/auth.py` to add MFA support:

- Add `verify_mfa(user_id, code)` function
- Add tests: `test_mfa_verification()` and `test_mfa_timeout()`
- Run `pytest tests/test_auth.py -v` after changes
- Check that tests pass before committing

When to Delegate:
- If changes span 2+ files → Use Agent Team
- If changes are bounded to 1 file → Subagent is sufficient
```

## Session Protocol

1. **Start**: Call `trw_session_start()` - loads all prior learnings
2. **Assess**: Determine if delegation or self-implementation is better
3. **Delegate**: Use focused subagents for bounded tasks
4. **Verify**: Run tests after each change - fix before moving on
5. **Learn**: Call `trw_learn(summary, detail)` for reusable insights
6. **Finish**: Call `trw_deliver()` - persists work for future sessions

## Key Gotchas

- **Context compaction**: Always checkpoint before major changes
- **Test coverage**: Generic models respond better to test-first instructions
- **File navigation**: Be explicit about file paths, don't assume
- **Model limitations**: Generic models may have limited context windows (32K)
- **Unknown capabilities**: Test delegation patterns to understand model strengths

## Migration Notes

If migrating from another model family:
- Update instructions in `.opencode/INSTRUCTIONS.md` to reflect generic patterns
- Review and update any generic-specific learnings in the knowledge base
- Test delegation workflows with generic model patterns
- Add model-specific learnings for your specific model
