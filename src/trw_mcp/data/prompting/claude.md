# Claude Prompting Guide

This guide provides best practices for interacting with Claude models
(Anthropic API, claude-3.7, claude-sonnet-4, Claude-3-Opus).

## Key Strengths

- Excellent at reading and understanding codebases
- Strong file navigation and change management
- Good at Agent Team orchestration and peer coordination

## Recommended Patterns

### When to Delegate
- **Complex multi-file changes** → Use Agent Teams for peer coordination
- **Large refactoring** → Use subagents with focused scope
- **Research-heavy tasks** → Use researcher-type subagent with specific queries

### When Self-Implement
- **Trivial edits** (≤3 lines, 1 file) → Self-implement directly
- **Simple bug fixes** → Self-implement with clear scope
- **Immediate feedback loop needed** → Self-implement, run tests quickly

### Context Management

**Do:**
- Use `trw_session_start()` at start of each session
- Call `trw_checkpoint()` after major phase transitions
- Call `trw_learn()` when discovering patterns

### Claude-Optimized Instructions

When delegating to Claude models:
1. **Explicit file ownership** - Claude uses Agent Teams well when ownership is defined
2. **Clear delegation boundaries** - Claude excels when tasks have clear boundaries
3. **Phase-based framing** - Claude responds well to 6-phase execution model

Example:
```
Edit `src/auth.py` to add MFA support:

When to Delegate:
- Changes span 2+ files → Use Agent Team with LEAD/teammate structure
- Changes are bounded to 1 file → Subagent is sufficient

Task Decomposition:
1. Implement verify_mfa(user_id, code) function in src/auth.py
2. Add tests: test_mfa_verification(), test_mfa_timeout() in tests/test_auth.py
3. Run pytest tests/test_auth.py -v after changes
4. Update documentation if needed

File Ownership:
- src/auth.py → implementer
- tests/test_auth.py → tester
- documentation → researcher (if needed)
```

## Session Protocol

1. **Start**: Call `trw_session_start()` - loads all prior learnings
2. **Assess**: Determine if delegation or self-implementation is better
3. **Delegate**: Use Agent Teams for multi-file coordination, subagents for single-file
4. **Verify**: Run tests after each change - fix before moving on
5. **Learn**: Call `trw_learn(summary, detail)` for reusable insights
6. **Finish**: Call `trw_deliver()` - persists work for future sessions

## Key Gotchas

- **Agent Teams**: Claude uses Agent Teams very effectively - leverage them
- **File ownership**: Define ownership explicitly for Agent Teams to work well
- **Phase transitions**: Claude responds well to clear phase boundaries
- **Test coverage**: Claude responds better to test-first instructions

## Migration Notes

If migrating from another model family:
- Update instructions in `.opencode/INSTRUCTIONS.md` to reflect Claude patterns
- Review and update any Claude-specific learnings in the knowledge base
- Test Agent Team workflows with Claude-specific patterns
- Leverage Claude's file navigation capabilities
