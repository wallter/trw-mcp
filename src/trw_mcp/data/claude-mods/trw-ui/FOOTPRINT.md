# trw-ui footprint

Everything this mod hooks and calls, as reported by `claude plugin validate`
(Claude Code 2.1.293). Nothing here runs code to produce it.

```
Validating plugin manifest: <repo>/trw-mcp/src/trw_mcp/data/claude-mods/trw-ui/.claude-plugin/plugin.json

Validating hooks: <repo>/trw-mcp/src/trw_mcp/data/claude-mods/trw-ui/hooks/hooks.json

  ❯ ../register.ts hooks: session.start, turn.complete, command.run{command=trw}, ui.close, ui.render{component=Pane, requestId=trw-status}, ui.render{component=PromptHint}, ui.render{component=AbovePrompt}
  ❯ ../register.ts gating hook with .catch: command.run{command=trw}
  ❯ ../register.ts gating hook with .catch: ui.close
  ❯ ../register.ts calls: $.clock.every, $.clock.now (via freshnessNote, refresh, ring, tick), $.command.register, $.fs.read (via candidates), $.process.run (via runCli), $.prompt.submit (via ring), $.session.id (via refresh), $.session.root (via refresh), $.ui.close, $.ui.invalidate, $.ui.log (via ring), $.ui.open, $.ui.resolve (via bandTree, footerTree, paneTree), $.ui.toast (via ring)

✔ Validation passed
```

## Reading it

- Hooks: `session.start`, `turn.complete`, `ui.close` observe and call `next(e)`.
  `command.run{command=trw}` answers only its own `/trw` command. `ui.render{PromptHint}` draws the footer label beside Claude Code's own hint (or passes the event on). `ui.render`
  draws only the `trw-status` Pane and the AbovePrompt band, and returns `next(e)`
  when it has nothing to show.
- Forbidden, and absent: any `tool.check`, `tool.call`, `prompt.section`,
  `prompt.compose`, `session.append` or `prompt.submit` hook, `$.tool.register`,
  prompt rewriting, `asUser`. `register.test.ts` asserts the hook list.
- The one process it starts: `trw-mcp local status --json --session-id <id> --cache-ttl 5`
  (read-only; the CLI's only write is its own status cache file).
- The one prompt it can submit: `$.prompt.submit({ text })` with the fixed
  pointer-only doorbell text, only when `doorbell` is `wake` (default `notify`).
- Reads: the project `.mcp.json` (to find the `trw-mcp` launcher) via `$.fs.read`.
  Writes: none.
- Test run: `claude plugin test` reports 28 pass, 0 fail.
