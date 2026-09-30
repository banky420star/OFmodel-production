'use client'

import { useEffect, useState } from 'react'
import { Icons } from '@/lib/icons'
import { getDivisions } from '@/lib/api'
import type { Division, DivisionsResponse, SchedulerStatus } from '@/lib/types'

/**
 * The studio's operating structure.
 *
 * The model manager answers "which account needs what next". This answers the
 * bigger question the roster cannot: what does the studio actually *make*, who
 * makes it, how often does it run, and what is stopping it right now.
 *
 * The design rule this page exists to hold: a division is never rendered as if
 * it were working. `can_act` comes from resolving the real provider registry,
 * `maturity` from whether a route reaches a provider at all, and anything short
 * of live shows its `gap` as text. There is deliberately no spinner and no
 * optimistic state — a heading over a thing that does not run is exactly the
 * failure this page was built to end.
 */

const MATURITY_TONE: Record<string, string> = {
  live: 'var(--green)',
  partial: 'var(--amber)',
  unbuilt: 'var(--text-muted)',
}

const MATURITY_LABEL: Record<string, string> = {
  live: 'Live',
  partial: 'Partial',
  unbuilt: 'Not built',
}

// Count keys are the API's, and each division only reports the numbers it
// owns. Naming them here keeps the card honest about what a number means —
// "packs" under Video production is not a video count, and should not be
// labelled as one.
const COUNT_LABEL: Record<string, string> = {
  shoots: 'shoots',
  completed: 'completed',
  planned: 'planned',
  packs: 'content packs',
  accounts: 'accounts',
  live: 'live',
  scheduled: 'scheduled',
  posted: 'posted',
  personas: 'models',
}

export default function DivisionsPage() {
  const [data, setData] = useState<DivisionsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    getDivisions()
      .then((d: DivisionsResponse) => { setData(d); setError(''); setLoading(false) })
      .catch((e: Error) => { setError(e.message); setLoading(false) })
  }, [])

  return (
    <main className="workspace">
      <header className="topbar">
        <button className="mobile-menu" aria-label="Open menu">{Icons.menu}</button>
        <div className="crumb">
          <a href="/"><span>Persona Studio</span></a><b>/</b>
          <strong>Divisions</strong>
        </div>
      </header>

      <div className="content">
        <section className="page-heading">
          <h1>Divisions</h1>
          <p>
            What the studio makes, which unit makes it, and what is stopping it.
            A division is {'"'}live{'"'} when a route reaches a real provider —
            never because this page exists.
          </p>
        </section>

        {data && (
          <section style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
            <Stat label="Divisions" value={data.summary.total} />
            <Stat label="Can act now" value={data.summary.can_act} tone="var(--green)" />
            <Stat label="Blocked" value={data.summary.blocked} tone="var(--amber)" />
            <Stat
              label="On a timer"
              value={data.summary.scheduled_divisions}
              tone={data.summary.scheduled_divisions === 0 ? 'var(--amber)' : 'var(--green)'}
            />
          </section>
        )}

        {data?.note && (
          <p style={{
            fontSize: 12, color: 'var(--text-muted)', marginBottom: 20,
            borderLeft: '2px solid var(--amber)', paddingLeft: 10,
          }}>{data.note}</p>
        )}

        {data && <ClockCard clock={data.scheduler} />}

        {loading ? (
          <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>Loading divisions…</p>
        ) : error ? (
          <p style={{ color: 'var(--red)', fontSize: 13 }}>{error}</p>
        ) : !data ? null : (
          <div style={{ display: 'grid', gap: 14 }}>
            {data.divisions.map(d => (
              <DivisionCard key={d.key} division={d} />
            ))}
          </div>
        )}
      </div>
    </main>
  )
}

function DivisionCard({ division: d }: { division: Division }) {
  const tone = MATURITY_TONE[d.maturity] || 'var(--text-muted)'
  const countEntries = Object.entries(d.counts)

  return (
    <article style={{
      background: 'var(--bg-card)', border: '1px solid var(--border)',
      borderRadius: 12, padding: 18,
      // A blocked division carries its state on the card edge, so the page is
      // scannable without reading each card's text.
      borderLeft: `3px solid ${d.can_act ? tone : 'var(--red)'}`,
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 220 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <h3 style={{ fontSize: 16, margin: 0 }}>{d.name}</h3>
            <Badge tone={tone}>{MATURITY_LABEL[d.maturity] || d.maturity}</Badge>
            {!d.can_act && <Badge tone="var(--red)">Blocked</Badge>}
          </div>
          <p style={{ fontSize: 13, color: 'var(--text)', margin: '8px 0 0' }}>{d.owns}</p>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '6px 0 0' }}>
            {d.cadence}
          </p>
        </div>

        {countEntries.length > 0 && (
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            {countEntries.map(([k, v]) => (
              <div key={k} style={{ textAlign: 'right', minWidth: 64 }}>
                <small style={{ fontSize: 11, color: 'var(--text-muted)', display: 'block' }}>
                  {COUNT_LABEL[k] || k}
                </small>
                <b style={{ fontSize: 18 }}>{v}</b>
              </div>
            ))}
          </div>
        )}
      </div>

      {d.counts_error && (
        <p style={{
          marginTop: 12, fontSize: 12, color: 'var(--red)',
          borderLeft: '2px solid var(--red)', paddingLeft: 10,
        }}>
          {d.counts_error} — the numbers above are incomplete, not zero.
        </p>
      )}

      {d.blocked_by.length > 0 && (
        <div style={{
          marginTop: 12, padding: '10px 12px', borderRadius: 8,
          background: 'var(--amber-dim)', border: '1px solid var(--amber)',
        }}>
          <b style={{ fontSize: 11, color: 'var(--amber)', letterSpacing: '.04em' }}>
            CANNOT ACT
          </b>
          {d.blocked_by.map((b, i) => (
            <p key={i} style={{ fontSize: 12, color: 'var(--text)', margin: '6px 0 0' }}>{b}</p>
          ))}
        </div>
      )}

      {d.gap && (
        <p style={{
          marginTop: 12, fontSize: 12, color: 'var(--text-muted)',
          borderLeft: '2px solid var(--border)', paddingLeft: 10,
        }}>
          {d.gap}
        </p>
      )}

      <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {d.requires.map(cap => (
          <Badge key={cap} tone={d.capabilities_missing.includes(cap) ? 'var(--red)' : 'var(--green-dim)'}>
            {cap}{d.capabilities_missing.includes(cap) ? ' · missing' : ''}
          </Badge>
        ))}
        {d.workflow && (
          <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'monospace' }}>
            {d.workflow}
          </span>
        )}
      </div>

      {d.routes.length > 0 && (
        <div style={{ marginTop: 8, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          {d.routes.map(r => (
            <span key={r} style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'monospace' }}>
              {r}
            </span>
          ))}
        </div>
      )}
    </article>
  )
}

function Badge({ children, tone }: { children: React.ReactNode; tone: string }) {
  return (
    <span style={{
      fontSize: 10, letterSpacing: '.05em', textTransform: 'uppercase',
      padding: '3px 8px', borderRadius: 999, color: tone,
      border: `1px solid ${tone}`,
    }}>{children}</span>
  )
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div style={{
      background: 'var(--bg-card)', border: '1px solid var(--border)',
      borderRadius: 10, padding: '10px 16px', minWidth: 120,
    }}>
      <small style={{ fontSize: 11, color: 'var(--text-muted)', display: 'block' }}>{label}</small>
      <b style={{ fontSize: 20, color: tone || 'var(--text)' }}>{value}</b>
    </div>
  )
}

// The clock, shown as its own object because its state is not a division's: it
// can exist and be off, exist and be armed but blocked on credentials, or be
// running. Rendering that as "0 published" would hide which of those it is.
function ClockCard({ clock }: { clock: SchedulerStatus }) {
  const tone = !clock.enabled ? 'var(--text-muted)'
    : clock.blocked ? 'var(--amber)'
    : 'var(--green)'
  const state = !clock.enabled ? 'Off — the calendar is a list, not a queue'
    : clock.blocked ? `Armed, but blocked: ${clock.blocked}`
    : 'Armed and ticking'

  return (
    <section style={{
      background: 'var(--bg-card)', border: '1px solid var(--border)',
      borderLeft: `3px solid ${tone}`, borderRadius: 10,
      padding: '12px 16px', marginBottom: 20,
    }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <b style={{ fontSize: 13 }}>The clock</b>
        <span style={{ fontSize: 12, color: tone }}>{state}</span>
      </div>
      <div style={{
        display: 'flex', gap: 18, flexWrap: 'wrap', marginTop: 8,
        fontSize: 11, color: 'var(--text-muted)',
      }}>
        <span>every {clock.interval_seconds}s, {clock.max_per_tick}/tick</span>
        <span>due now: {clock.due_now}</span>
        <span>published: {clock.published_total}</span>
        <span>failed: {clock.failed_total}</span>
        <span>missed: {clock.missed_total}</span>
        <span>ticks: {clock.ticks}</span>
        {clock.last_tick_at && <span>last: {clock.last_tick_at}</span>}
      </div>
      {clock.blocked_detail && (
        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 8, marginBottom: 0 }}>
          {clock.blocked_detail}
        </p>
      )}
      {clock.last_error && (
        <p style={{ fontSize: 11, color: 'var(--red)', marginTop: 8, marginBottom: 0 }}>
          last error: {clock.last_error}
        </p>
      )}
    </section>
  )
}
