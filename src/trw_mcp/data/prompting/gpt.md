# GPT-5.4 Prompting Guide

This guide provides best practices for interacting with GPT-5.4 models
(OpenAI API, gpt-5.4, GPT-5-Turbo).

## Key Strengths

- Excellent reasoning and task decomposition capabilities
- Strong understanding of complex requirements
- Good at multi-step planning and verification

## Recommended Patterns

### When to Delegate
- **Complex multi-file changes** → Use Agent Teams for peer coordination
- **Large refactoring** → Use subagents with focused scope
- **Research-heavy tasks** → Use explore-type subagent with specific queries

### When Self-Implement
- **Trivial edits** (≤3 lines, 1 file) → Self-implement directly
- **Simple bug fixes** → Self-implement with clear scope
- **Immediate feedback loop needed** → Self-implement, run tests quickly

### Context Management

**Do:**
- Use `trw_session_start()` at start of each session
- Call `trw_checkpoint()` after major phase transitions
- Call `trw_learn()` when discovering patterns

### GPT-Optimized Instructions

When delegating to GPT models:
1. **Start with "When to Delegate" decision tree** - helps GPT choose optimal approach
2. **Be specific about task boundaries** - GPT excels at bounded tasks
3. **Include test expectations** - GPT responds well to test-driven instructions
4. **Use step-by-step framing** - GPT is excellent at multi-step reasoning

Example:
```
Edit `src/auth.py` to add MFA support:

1. Add `verify_mfa(user_id, code)` function
2. Add tests: `test_mfa_verification()` and `test_mfa_timeout()`
3. Run `pytest tests/test_auth.py -v` after changes

When to Delegate:
- If changes span 2+ files → Use Agent Team
- If changes are bounded to 1 file → Subagent is sufficient
```

## Session Protocol

1. **Start**: Call `trw_session_start()` - loads all prior learnings
2. **Decompose**: Use GPT's reasoning for task breakdown
3. **Delegate**: Use Agent Teams for multi-file coordination, subagents for single-file
4. **Verify**: Run tests after each change - fix before moving on
5. **Learn**: Call `trw_learn(summary, detail)` for reusable insights
6. **Finish**: Call `trw_deliver()` - persists work for future sessions

## Key Gotchas

- **Over-delegation**: GPT may try to do everything itself - encourage delegation for non-trivial tasks
- **Test coverage**: GPT responds better to test-first instructions
- **Token budget**: GPT-5.4 has 200K context - leverage the full budget for complex tasks
- **Reasoning time**: GPT benefits from explicit step-by-step instructions

## Migration Notes

If migrating from another model family:
- Update instructions in `.opencode/INSTRUCTIONS.md` to reflect GPT patterns
- Review and update any GPT-specific learnings in the knowledge base
- Test delegation workflows with GPT-specific patterns
- Leverage Agent Teams for complex multi-file coordination
