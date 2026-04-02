# Qwen-Coder-Next Prompting Guide

This guide provides best practices for interacting with Qwen-Coder-Next models
(vllm/qwen3-coder-next, Qwen3-R, Qwen3-Q).

## Key Strengths

- Strong coding capabilities with good context retention
- Efficient for code generation and analysis tasks
- Good at understanding project structure and intent

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
- Call `trw_checkpoint()` after completing major phases
- Call `trw_learn()` when discovering gotchas or patterns

**Don't:**
- Skip ceremony tools - they compound across sessions
- Implement complex tasks without delegation - context is too limited
- Forget to run `trw_deliver()` at session end

### Qwen-Optimized Instructions

When delegating, provide:
1. Clear task description with expected outcome
2. Specific file paths and line numbers where changes are needed
3. Test expectations (test names, expected behavior)
4. Context about existing patterns to match

Example:
```
Edit `src/auth.py` to add MFA support:

- Add `verify_mfa(user_id, code)` function
- Use `src/auth/mfa.py` as reference implementation
- Add tests: `test_mfa_verification()` and `test_mfa_timeout()`
- Run `pytest tests/test_auth.py -v` after changes
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
- **Test coverage**: Qwen responds better to test-first instructions
- **File navigation**: Be explicit about file paths, don't assume
- **Model limitations**: Qwen-30b has ~128K context - watch token budget

## Migration Notes

If migrating from another model family:
- Update instructions in `.opencode/INSTRUCTIONS.md` to reflect Qwen patterns
- Review and update any Qwen-specific learnings in the knowledge base
- Test delegation workflows with Qwen-specific patterns
