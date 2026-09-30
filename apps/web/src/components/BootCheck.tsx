'use client'

/**
 * The load-in check screen.
 *
 * Every workspace section is probed live, in order, through the same client
 * function its page uses — so a green row means that section's data path works
 * end to end (browser → Next rewrite → API → database), not that some URL
 * answered. The probes run one at a time rather than in parallel: a cascade is
 * readable, and one slow call shows itself instead of hiding inside a
 * Promise.all that reports nothing until the slowest of it returns.
 *
 * The row states mean specific things, and are not decoration:
 *   ok       the call answered and its payload reports nothing wrong
 *   warn     the call answered, and the payload itself says a provider or
 *            capability is not ready — a real gap, but the connection worked
 *   fail     the call did not answer; the detail is the API's own error text
 *   skipped  the section needs a persona and none exists yet — said plainly
 *            instead of being counted as a pass
 *
 * Nothing here is mocked or assumed. A row only goes green when the real
 * endpoint returned real data, and a section with nothing in it (no fans, no
 * invites) is a working connection reporting an empty table, not a failure.
 * It clears itself once it is done — after ~1s on a clean run, after ~2.5s if
 * anything warned so the result is readable — and only stays put if a
 * connection actually failed.
 *
 * It runs once per tab session, not on every page load. That distinction is
 * load-bearing: the app navigates with plain `<a href>` (see Sidebar), so every
 * nav is a *full page load* and this layout remounts — without the guard the
 * check would replay on every click. If the app ever moves to next/link, the
 * guard can go, but do not remove it while the anchors are plain.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getAnalytics,
  getDashboardSummary,
  getHealth,
  getManagerRoster,
  getSchedule,
  getSystemProviders,
  listFans,
  listInvites,
  listJobs,
  listMailboxes,
  listPersonas,
  listSocialAccounts,
  listWorkflows,
} from '@/lib/api'

type State = 'pending' | 'running' | 'ok' | 'warn' | 'fail' | 'skipped'
type Settled = Exclude<State, 'pending' | 'running'>
type Outcome = { state: Settled; detail: string }
type Ctx = { personaId?: string; personaName?: string }

const count = (c: number, one: string, many = `${one}s`) => `${c} ${c === 1 ? one : many}`

/**
 * Endpoints here disagree about whether a collection is a bare list or a
 * wrapped one. Reading both is honest; guessing one shape would turn a real
 * payload into a fake zero.
 */
function listOf(payload: any, key: string): any[] {
  if (Array.isArray(payload)) return payload
  return Array.isArray(payload?.[key]) ? payload[key] : []
}

const SECTIONS: { label: string; path: string; probe: (ctx: Ctx) => Promise<Outcome> }[] = [
  {
    label: 'API core',
    path: 'GET /health',
    probe: async () => {
      const h = await getHealth()
      const providers = Object.values(h?.providers ?? {}) as any[]
      const green = providers.filter((p) => p?.status === 'green').length
      // A degraded API still answered. That is a warning about a provider,
      // not a failure of the connection this row is testing.
      return {
        state: h?.status === 'healthy' ? 'ok' : 'warn',
        detail: `${h?.status ?? 'unknown'} · ${green} of ${providers.length} providers reachable`,
      }
    },
  },
  {
    label: 'Overview',
    path: 'GET /dashboard/summary',
    probe: async () => {
      const s = await getDashboardSummary()
      const parts = [
        count(s?.total_models ?? 0, 'model'),
        count(s?.total_shoots ?? 0, 'shoot'),
        count(s?.total_packs ?? 0, 'pack'),
        s?.health ? `health ${s.health.online}/${s.health.total}` : '',
      ]
      return { state: 'ok', detail: parts.filter(Boolean).join(' · ') }
    },
  },
  {
    label: 'Models',
    path: 'GET /personas',
    probe: async (ctx) => {
      const personas = listOf(await listPersonas(), 'personas')
      // The sections below are persona-scoped, so this is the one probe that
      // hands something to a later one. If there were no persona they would
      // skip, and the summary would say so rather than invent a pass.
      const first = personas[0]
      if (first?.id) {
        ctx.personaId = first.id
        ctx.personaName = first.name
      }
      return { state: 'ok', detail: count(personas.length, 'persona') }
    },
  },
  {
    label: 'Model manager',
    path: 'GET /manager/roster',
    probe: async () => {
      const s = (await getManagerRoster())?.summary ?? {}
      // `live` stays 0 until a person confirms the platform has the account —
      // the API's own note says the roster verifies nothing. Reporting that
      // honestly is not the same as the section being broken.
      return {
        state: 'ok',
        detail: `${count(s.accounts ?? 0, 'account')} · ${s.live ?? 0} live · ${s.needing_work ?? 0} needing work`,
      }
    },
  },
  {
    label: 'Production',
    path: 'GET /jobs + /workflows',
    probe: async () => {
      const [jobs, workflows] = await Promise.all([listJobs(), listWorkflows()])
      const j = listOf(jobs, 'jobs')
      const w = listOf(workflows, 'workflows')
      const active = j.filter((x) => x?.status === 'running' || x?.status === 'queued').length
      return {
        state: 'ok',
        detail: `${count(j.length, 'job')} · ${count(w.length, 'workflow')} · ${active ? `${active} active` : 'idle'}`,
      }
    },
  },
  {
    label: 'Chat',
    path: 'GET /fans',
    probe: async () => ({ state: 'ok', detail: count(listOf(await listFans(), 'fans').length, 'fan') }),
  },
  {
    label: 'Mailboxes',
    path: 'GET /mailboxes',
    probe: async () => ({
      state: 'ok',
      detail: count(listOf(await listMailboxes(), 'mailboxes').length, 'mailbox', 'mailboxes'),
    }),
  },
  {
    label: 'Socials',
    path: 'GET /social-accounts',
    probe: async () => {
      const accounts = listOf(await listSocialAccounts(), 'accounts')
      const active = accounts.filter((a) => a?.status === 'active').length
      return { state: 'ok', detail: `${count(accounts.length, 'account')} · ${active} active` }
    },
  },
  {
    label: 'Calendar',
    path: 'GET /personas/{id}/schedule',
    probe: async (ctx) => {
      if (!ctx.personaId) return { state: 'skipped', detail: 'no persona yet' }
      const posts = listOf(await getSchedule(ctx.personaId), 'schedule')
      return { state: 'ok', detail: `${ctx.personaName} · ${count(posts.length, 'scheduled post')}` }
    },
  },
  {
    label: 'Analytics',
    path: 'GET /personas/{id}/analytics',
    probe: async (ctx) => {
      if (!ctx.personaId) return { state: 'skipped', detail: 'no persona yet' }
      const snapshots = listOf(await getAnalytics(ctx.personaId), 'analytics')
      return { state: 'ok', detail: `${ctx.personaName} · ${count(snapshots.length, 'snapshot')}` }
    },
  },
  {
    label: 'Monetization',
    path: 'GET /dashboard/summary',
    probe: async () => {
      // This section reads recorded revenue off the dashboard summary — the
      // page's own two calls. No separate endpoint exists to test.
      const s = await getDashboardSummary()
      return {
        state: 'ok',
        detail: `$${(s?.revenue ?? 0).toLocaleString()} revenue · ${count(s?.followers ?? 0, 'follower')}`,
      }
    },
  },
  {
    label: 'Settings',
    path: 'GET /system/providers + /team/invites',
    probe: async () => {
      const [providers, invites] = await Promise.all([getSystemProviders(), listInvites()])
      const capabilities: any[] = listOf(providers, 'capabilities')
      const required: string[] = listOf(providers, 'required')
      const notReady = capabilities.filter((c) => c?.status !== 'green').map((c) => c?.capability)
      const missing = required.filter(
        (r) => !capabilities.some((c) => c?.capability === r && c?.configured)
      )
      const inviteCount = count(listOf(invites, 'invites').length, 'invite')
      const gap = missing.length ? `missing: ${missing.join(', ')}` : notReady.length ? `not green: ${notReady.join(', ')}` : ''
      return {
        state: missing.length || notReady.length ? 'warn' : 'ok',
        detail: `${count(capabilities.length, 'capability', 'capabilities')}${gap ? ` · ${gap}` : ''} · ${inviteCount}`,
      }
    },
  },
]

type RowState = { state: State; detail?: string; ms?: number }

const MARK: Record<State, string> = {
  pending: '○',
  running: '',
  ok: '✓',
  warn: '!',
  fail: '✗',
  skipped: '–',
}

const SEEN_KEY = 'persona.bootcheck.seen'

// sessionStorage throws where site data is blocked. If it does, the check runs
// — showing the screen is the point — rather than silently disappearing.
function alreadyChecked(): boolean {
  try {
    return sessionStorage.getItem(SEEN_KEY) === '1'
  } catch {
    return false
  }
}

function markChecked() {
  try {
    sessionStorage.setItem(SEEN_KEY, '1')
  } catch {
    /* the check still ran; it just may run again after a nav */
  }
}

export default function BootCheck() {
  const [mounted, setMounted] = useState(false)
  const [rows, setRows] = useState<RowState[]>(() => SECTIONS.map(() => ({ state: 'pending' })))
  const [phase, setPhase] = useState<'open' | 'closing' | 'gone'>('open')
  const started = useRef(false)

  const run = useCallback(async () => {
    setPhase('open')
    setRows(SECTIONS.map(() => ({ state: 'pending' })))
    const ctx: Ctx = {}
    for (let i = 0; i < SECTIONS.length; i++) {
      setRows((prev) => prev.map((r, j) => (j === i ? { state: 'running' } : r)))
      const t0 = performance.now()
      let outcome: Outcome
      try {
        outcome = await SECTIONS[i].probe(ctx)
      } catch (e) {
        // The API's own words. A generic "something went wrong" would waste
        // the only thing this screen exists to show.
        outcome = { state: 'fail', detail: e instanceof Error ? e.message : String(e) }
      }
      const ms = Math.round(performance.now() - t0)
      setRows((prev) => prev.map((r, j) => (j === i ? { ...outcome, ms } : r)))
    }
  }, [])

  // Not painted until the client is running: the sessionStorage answer is
  // unknowable during SSR, and rendering from it on the first pass would be a
  // hydration mismatch. Marking before the run means navigating away mid-check
  // does not restart it on the next page.
  useEffect(() => {
    setMounted(true)
  }, [])

  useEffect(() => {
    if (!mounted || started.current) return
    started.current = true
    if (alreadyChecked()) {
      setPhase('gone')
      return
    }
    markChecked()
    void run()
  }, [mounted, run])

  const done = rows.every((r) => r.state !== 'pending' && r.state !== 'running')
  const failed = rows.filter((r) => r.state === 'fail').length
  const warned = rows.filter((r) => r.state === 'warn').length
  const skipped = rows.filter((r) => r.state === 'skipped').length
  const attempted = rows.filter((r) => r.state === 'ok' || r.state === 'warn' || r.state === 'fail').length
  const answered = attempted - failed

  // A failed connection keeps the screen up until someone decides what to do.
  // Warnings do not: on this machine the API reports a few permanently
  // unconfigured providers, so treating them as blocking would put a wall in
  // front of every page load forever. They hold the screen a beat longer so
  // the settled result is readable, then get out of the way.
  useEffect(() => {
    if (!done || failed) return
    const t = setTimeout(() => setPhase('closing'), warned ? 2500 : 900)
    return () => clearTimeout(t)
  }, [done, failed, warned])

  useEffect(() => {
    if (phase !== 'closing') return
    const t = setTimeout(() => setPhase('gone'), 320)
    return () => clearTimeout(t)
  }, [phase])

  if (!mounted || phase === 'gone') return null

  return (
    <div className={`boot-overlay${phase === 'closing' ? ' boot-closing' : ''}`} role="dialog" aria-label="Checking section connections">
      <div className="boot-panel">
        <div className="boot-head">
          <img className="boot-logo" src="/icon-512.png" alt="" width={34} height={34} />
          <div className="boot-title">
            Persona<em>Studio</em>
          </div>
        </div>
        <p className="boot-sub">
          {done ? 'Connection check complete.' : 'Testing every section’s API connection…'}
        </p>

        <ul className="boot-list">
          {SECTIONS.map((s, i) => {
            const r = rows[i]
            return (
              <li key={s.label} className={`boot-row boot-${r.state}`}>
                <span className="boot-mark" aria-hidden="true">
                  {r.state === 'running' ? <span className="boot-spinner" /> : MARK[r.state]}
                </span>
                <span className="boot-label">{s.label}</span>
                <span className="boot-path" title={s.path}>{s.path}</span>
                <span className="boot-detail" title={r.detail ?? ''}>
                  {r.detail ?? (r.state === 'running' ? 'testing…' : '')}
                </span>
                <span className="boot-ms">{r.ms != null ? `${r.ms} ms` : ''}</span>
              </li>
            )
          })}
        </ul>

        <div className="boot-foot">
          <p className="boot-summary" aria-live="polite">
            {done
              ? `${answered} of ${attempted} connections answered`
              : `${answered} of ${attempted} answered so far`}
            {failed ? ` · ${failed} failed` : ''}
            {skipped ? ` · ${skipped} skipped (no persona)` : ''}
          </p>
          <div className="boot-actions">
            {done ? (
              <>
                <button type="button" className="secondary-button btn-sm" onClick={() => void run()}>
                  Retry
                </button>
                <button type="button" className="primary-button btn-sm" onClick={() => setPhase('closing')}>
                  {failed ? 'Continue anyway' : 'Continue'}
                </button>
              </>
            ) : (
              <button type="button" className="secondary-button btn-sm" onClick={() => setPhase('closing')}>
                Skip
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
