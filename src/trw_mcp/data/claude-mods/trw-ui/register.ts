// Portions adapted from anthropics/claude-code-playground (token-weather,
// blast-radius):
//   Copyright 2026 Anthropic PBC
//   SPDX-License-Identifier: Apache-2.0
// Changes: rewritten in TypeScript, new data source, panes and doorbell. The rest
// of this file is TRW content under BUSL-1.1. See NOTICE.
//
// trw-ui: a read-only TRW status pane, an optional band and an inbox doorbell.
//
// Structure adapted from Anthropic's Apache-2.0 sample mods (see NOTICE):
//   token-weather   band hook, session.start + turn.complete refresh, invalidate
//   blast-radius    Pane open with isPlaced, band fallback when not placed
//   code-modernization (official plugins)  snapshot reader with a fallback text
//
// Rules this module keeps (PRD-CORE-354 FR07/FR08/NFR02):
//   - Read-only. The only registered hooks are session.start, turn.complete,
//     command.run (its own /trw command), ui.close and ui.render. Every handler
//     is wrapped in try/catch and passes the event on with next(e).
//   - Never a tool.check allow/deny, prompt.section, session.append, prompt
//     rewrite, $.tool.register or asUser. The one prompt the mod can submit is
//     the fixed pointer-only doorbell text, and only with doorbell=wake.
//   - Missing, stale or malformed data shows as unknown or fallback text.
//   - At most one CLI process in flight; refreshes are debounced.
//
// The host reads on(...) and $.noun.method(...) from source, so they are spelled
// literally, and helpers that take $ are top-level functions.

import type { Register } from 'claude-code'

import {
  FRESH_MEMORY,
  ageText,
  bandText,
  buildView,
  doorbell,
  fallbackText,
  footerLayout,
  hintText,
  inboxBadge,
  labelSegments,
  parseSnapshot,
} from './snapshot'
import type { DoorbellMode, HintMode, Memory, Row, Segment, Snap } from './snapshot'

const PANE = 'trw-status'
const MIN_GAP_MS = 2000
const CLI_TIMEOUT_MS = 8000

// Module state: shared by the hooks, reset on every reload.
let bandOn = false
// The footer label: TRW status right-justified on Claude Code's prompt-hint line,
// the same row as the permission-mode badge (no extra row, unlike a statusLine).
let footerOn = true
let hintMode: HintMode = 'keep'
let mode: DoorbellMode = 'notify'
let pollS = 15
let hiddenPollS = 60
let minIntervalS = 300

let snap: Snap | null = null
let snapAt = 0
let failure = 'loading'
let inFlight = false
let lastStartAt = -Infinity
let paneOpen = false
let paneWhere: 'pane' | 'band' = 'pane'
let badge = 0
let mem: Memory = FRESH_MEMORY
let lastSid: string | null = null
let cli: string | null = null
let ticker: { cancel: () => void } | null = null

function visible(): boolean {
  return paneOpen || bandOn
}

// The doorbell poll interval in force: poll_s while the pane or band is visible,
// at least hidden_poll_s (default 60) when neither is.
function effectivePollS(): number {
  return visible() ? pollS : Math.max(pollS, hiddenPollS)
}

function readOptions(options: Record<string, unknown> | undefined): void {
  const o = options ?? {}
  bandOn = o.band === 'on'
  footerOn = o.footer !== 'off'
  hintMode = o.hint === 'trim' || o.hint === 'hide' ? o.hint : 'keep'
  mode = o.doorbell === 'off' || o.doorbell === 'wake' ? o.doorbell : 'notify'
  const poll = Number(o.poll_s)
  pollS = Number.isFinite(poll) && poll >= 5 ? Math.min(poll, 600) : 15
  const hidden = Number(o.hidden_poll_s)
  hiddenPollS = Number.isFinite(hidden) && hidden >= 5 ? Math.min(hidden, 3600) : 60
  const gap = Number(o.wake_min_interval_s)
  minIntervalS = Number.isFinite(gap) && gap >= 30 ? Math.min(gap, 86400) : 300
}

export const register: Register = (on, options) => {
  readOptions(options as Record<string, unknown> | undefined)

  on('session.start', async ($, e, next) => {
    const result = await next(e)
    try {
      try {
        await $.command.register({
          name: 'trw',
          description: 'Show TRW run, checkpoint and evidence status (read-only pane)',
          immediate: true,
        })
      } catch {
        // The name is taken: the band and doorbell still work.
      }
      if (ticker) ticker.cancel()
      ticker = $.clock.every(pollS * 1000, () => {
        void tick($)
      })
      if (mode !== 'off' || visible() || footerOn) void refresh($, true)
    } catch {
      // The mod stays quiet rather than break session start.
    }
    return result
  })

  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    try {
      if (!e.agentId && (visible() || mode !== 'off' || footerOn)) void refresh($, false)
    } catch {
      // Nothing to do: the next poll tries again.
    }
    return result
  })

  on('command.run', { command: 'trw' }, async ($, e, next) => {
    try {
      if (paneOpen) {
        paneOpen = false
        await $.ui.close({ id: PANE })
        $.ui.invalidate('ui.render')
        return {}
      }
      const opened = await $.ui.open({ id: PANE, title: 'TRW', closeOnEscape: true })
      paneOpen = true
      paneWhere = opened.isPlaced ? 'pane' : 'band'
      $.ui.invalidate('ui.render')
      void refresh($, true)
      return {}
    } catch {
      return next(e)
    }
  }).catch(async ($, e, next) => next(e))

  on('ui.close', async ($, e, next) => {
    const result = await next(e)
    try {
      if (e.id === PANE) {
        paneOpen = false
        $.ui.invalidate('ui.render')
      }
    } catch {
      // The pane is gone either way.
    }
    return result
  }).catch(async ($, e, next) => next(e))

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e, next) => {
    try {
      return await paneTree($, e)
    } catch {
      return next(e)
    }
  })

  on('ui.render', { component: 'PromptHint' }, async ($, e, next) => {
    try {
      if (!footerOn) return next(e)
      const tree = footerTree($, e)
      return tree === null ? next(e) : tree
    } catch {
      return next(e)
    }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    try {
      if (e.hasSurvey) return next(e)
      const tree = await bandTree($, e)
      return tree === null ? next(e) : tree
    } catch {
      return next(e)
    }
  })
}

// --------------------------------------------------------------------- refresh

async function tick($: any): Promise<void> {
  try {
    if (!visible() && mode === 'off' && !footerOn) return
    // The timer fires every poll_s; a hidden mod spawns the CLI only every
    // effective interval (1 s slack for timer jitter).
    const since = (await $.clock.now()) - lastStartAt
    if (since < effectivePollS() * 1000 - 1000) return
    await refresh($, false)
  } catch {
    // Next tick.
  }
}

// One CLI process at a time: a call that arrives while one runs is dropped, and
// the running one redraws when it finishes.
async function refresh($: any, force: boolean): Promise<void> {
  if (inFlight) return
  inFlight = true
  try {
    const now = await $.clock.now()
    if (!force && now - lastStartAt < MIN_GAP_MS) return
    lastStartAt = now
    const sid: string = await $.session.id()
    const root: string = await $.session.root()
    if (sid !== lastSid) {
      mem = FRESH_MEMORY
      lastSid = sid
    }
    const res = await runCli($, root, sid)
    if (res.snap) {
      snap = res.snap
      snapAt = await $.clock.now()
      failure = ''
      await ring($, sid)
    } else {
      snap = null
      failure = res.failure
      badge = 0
    }
    $.ui.invalidate('ui.render')
  } catch {
    snap = null
    failure = 'unavailable'
    badge = 0
    try {
      $.ui.invalidate('ui.render')
    } catch {
      // Nothing more to try.
    }
  } finally {
    inFlight = false
  }
}

// Candidates, in order: the sibling trw-mcp of the absolute trw launcher named
// in the project .mcp.json, <project>/.venv/bin/trw-mcp, trw-mcp on PATH.
async function candidates($: any, root: string): Promise<string[]> {
  const out: string[] = []
  if (cli) out.push(cli)
  try {
    const parsed = JSON.parse(await $.fs.read(root + '/.mcp.json'))
    const servers = parsed && typeof parsed === 'object' ? parsed.mcpServers : null
    if (servers && typeof servers === 'object') {
      const names = Object.keys(servers).sort((a, b) => (a === 'trw' ? -1 : b === 'trw' ? 1 : 0))
      for (const name of names) {
        const cmd = servers[name] && servers[name].command
        if (typeof cmd === 'string' && cmd.startsWith('/') && /\/trw[^/]*$/.test(cmd)) {
          out.push(cmd.slice(0, cmd.lastIndexOf('/')) + '/trw-mcp')
        }
      }
    }
  } catch {
    // No readable .mcp.json: the other candidates still apply.
  }
  out.push(root + '/.venv/bin/trw-mcp', 'trw-mcp')
  return out.filter((c, i) => out.indexOf(c) === i)
}

async function runCli($: any, root: string, sid: string): Promise<{ snap?: Snap; failure: string }> {
  let failureKind = 'unavailable'
  for (const c of await candidates($, root)) {
    let r: any
    try {
      r = await $.process.run([c, 'local', 'status', '--json', '--session-id', sid, '--cache-ttl', '5'], {
        cwd: root,
        timeoutMs: CLI_TIMEOUT_MS,
      })
    } catch {
      continue
    }
    if (r.exitCode === 0) {
      const parsed = parseSnapshot(String(r.stdout ?? ''))
      if (parsed.ok) {
        cli = c
        return { snap: parsed.snap, failure: '' }
      }
      failureKind = 'malformed'
    } else if (/unrecognized arguments|invalid choice|no such option/i.test(String(r.stderr ?? ''))) {
      if (failureKind !== 'malformed') failureKind = 'outdated'
    }
  }
  return { failure: failureKind }
}

// Doorbell: toast on a fresh increase, optional fixed pointer-only wake prompt.
async function ring($: any, sid: string): Promise<void> {
  if (!snap) return
  const nowMs: number = await $.clock.now()
  const out = doorbell({ mode, pollS: effectivePollS(), minIntervalS, nowMs, sessionId: sid, snap, mem })
  mem = out.mem
  badge = mode !== 'off' && out.trusted && out.pending !== null ? out.pending : 0
  if (out.toast) $.ui.toast(out.toast)
  if (out.wake) {
    $.ui.log('trw-ui: doorbell wake, ' + out.pending + ' TRW peer message(s) pending; submitted a pointer-only prompt')
    // Not awaited: the prompt runs once the session is idle.
    $.prompt.submit({ text: out.wake }).catch(() => {})
  }
}

// ---------------------------------------------------------------------- render

function paint(tone: string): { color?: string; dimColor?: boolean } {
  if (tone === 'ok') return { color: 'green' }
  if (tone === 'bad') return { color: 'red' }
  if (tone === 'warn') return { color: 'yellow' }
  return { dimColor: true }
}

async function freshnessNote($: any): Promise<string> {
  if (!snap) return ''
  const age = Math.max(0, Math.round(((await $.clock.now()) - snapAt) / 1000))
  const stale = age > 2 * effectivePollS()
  return `updated ${ageText(age)} ago${stale ? ' (stale, refresh pending)' : ''}`
}

async function paneTree($: any, e: any): Promise<unknown> {
  const { Box, Text } = $.ui.resolve(e)
  const children: unknown[] = []
  if (!snap) {
    children.push(Text({ children: fallbackText(failure), ...paint('warn') }))
  } else {
    const rows: Row[] = buildView(snap)
    if (badge > 0) children.push(Text({ bold: true, color: 'yellow', children: inboxBadge(badge) }))
    for (const row of rows) {
      children.push(
        Box({
          flexDirection: 'row',
          children: [
            Text({ bold: true, children: row.label.padEnd(15) }),
            Text({ wrap: 'wrap', ...paint(row.tone), children: row.text }),
          ],
        }),
      )
    }
    children.push(Text({ dimColor: true, children: `read-only · ${await freshnessNote($)} · /trw closes this` }))
  }
  return Box({ flexDirection: 'column', paddingX: 1, children })
}

// Returns null when the band has nothing to show (the caller passes the event on).
async function bandTree($: any, e: any): Promise<unknown | null> {
  const showFallback = paneOpen && paneWhere === 'band'
  // With the footer on, the inbox count is in the footer label: no extra band row.
  const bandBadge = badge > 0 && !footerOn
  if (!showFallback && !bandOn && !bandBadge) return null
  const { Box, Text } = $.ui.resolve(e)
  const lines: unknown[] = []
  if (showFallback) {
    // The pane did not fit: draw the same report here, within the band's rows.
    const room = Math.max(1, Number(e.maxRows ?? 8))
    if (!snap) {
      lines.push(Text({ children: fallbackText(failure), ...paint('warn') }))
    } else {
      const rows = buildView(snap).slice(0, room)
      for (const row of rows) {
        lines.push(Text({ ...paint(row.tone), children: `${row.label}: ${row.text}` }))
      }
    }
  } else {
    if (bandOn) {
      lines.push(
        snap
          ? Text({ children: bandText(snap) })
          : Text({ children: fallbackText(failure), ...paint('warn') }),
      )
    }
    if (bandBadge) lines.push(Text({ bold: true, color: 'yellow', children: inboxBadge(badge) }))
  }
  return Box({ flexDirection: 'column', paddingX: 1, children: lines })
}

// The footer label segments when no snapshot could be read: short, quiet, and
// never the same text as a real state ("TRW" alone means no run is pinned).
function footerFallback(): Segment[] {
  if (failure === 'outdated') return [{ text: 'TRW: update trw-mcp', tone: 'warn' }]
  if (failure === 'loading') return [{ text: 'TRW …', tone: 'dim' }]
  return [{ text: 'TRW ?', tone: 'dim' }]
}

// Claude Code's own hint (optionally trimmed) on the left, the TRW label
// right-justified on the same line. The label is fitted first; the hint gets what
// is left and is cut, never wrapped, so the line can never grow a second row.
function footerTree($: any, e: any): unknown {
  const layout = footerLayout(e.viewport?.columns, e.props?.hint, hintMode, room =>
    snap ? labelSegments(snap, room) : footerFallback(),
  )
  if (layout === null) return null
  const { Box, Text } = $.ui.resolve(e)
  const label = layout.segs.map(x => Text({ ...paint(x.tone), wrap: 'truncate', children: x.text }))
  return Box({
    flexDirection: 'row',
    width: layout.width,
    justifyContent: 'space-between',
    children: [Text({ dimColor: true, wrap: 'truncate', children: layout.hint }), Box({ flexDirection: 'row', children: label })],
  })
}
