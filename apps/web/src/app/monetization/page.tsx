'use client'

import { useEffect, useMemo, useState } from 'react'
import {
  connectFanvue,
  disconnectFanvue,
  fanvueStatus,
  getConversations,
  getDashboardSummary,
  getMonetizationReadiness,
  listPersonas,
} from '@/lib/api'
import type {
  ConversationRow,
  ConversationsReading,
  DashboardSummary,
  MonetizationReadiness,
  PersonaDetail,
} from '@/lib/types'
import { formatCurrency, formatMoney } from '@/lib/utils'

// What each state means for an operator, and what clears it. Kept beside the
// panel rather than inlined, because the difference between them is the whole
// point of carrying a state at all: `forbidden` and `error` are both "we could
// not read", and only one of them is fixed by retrying.
const INBOX_STATES: Record<string, { label: string; tone: 'ok' | 'warn' | 'bad' }> = {
  ok: { label: 'Read from the platform', tone: 'ok' },
  not_configured: { label: 'No platform connected', tone: 'warn' },
  disabled: { label: 'Connected, publishing not armed', tone: 'warn' },
  unsupported: { label: 'Platform has no inbox API', tone: 'warn' },
  forbidden: { label: 'Token missing `read:chat` — reconnect', tone: 'bad' },
  error: { label: 'The platform could not be read', tone: 'bad' },
}

const offers = [
  { name: 'Discovery', price: 0, detail: 'Safe-for-work teaser content and profile discovery', color: 'var(--blue)' },
  { name: 'Core subscription', price: 14.99, detail: 'Consistent weekly drops, behind-the-scenes, and member updates', color: 'var(--green)' },
  { name: 'Premium tier', price: 29.99, detail: 'Higher-frequency drops, polls, and priority requests', color: 'var(--amber)' },
  { name: 'Custom work', price: 75, detail: 'Human-reviewed custom briefs with explicit rights and delivery terms', color: '#e695ff' },
]

/**
 * A paid message carries no amount — the chat record has no price field, so the
 * size of a sale is only knowable from the earnings rows above. Naming the type
 * is the honest maximum; a figure here would be invented and would look precise.
 */
function messageLabel(row: ConversationRow): string {
  if (row.last_message_text) return row.last_message_text
  if (row.last_message_type === 'LOCKED_MESSAGE_UNLOCKED') {
    return row.last_message_from_creator ? 'A locked message you sent' : 'Unlocked a locked message'
  }
  if (row.last_message_type === 'TIP') return 'Sent a tip'
  // A message with media and no text is a real message, not an absent one.
  return row.last_message_type ? `(${row.last_message_type})` : 'No message preview'
}

function InboxPanel({ inbox, error }: { inbox: ConversationsReading | null; error: string }) {
  // `error` here is the panel failing to *reach* the API at all — a different
  // problem from the API reaching Fanvue and being refused. Both must land
  // somewhere that is visibly not "nobody has written".
  const state = error ? 'error' : inbox?.state ?? '...'
  const meta = INBOX_STATES[state] || { label: 'Reading the inbox…', tone: 'warn' as const }
  const tone = meta.tone === 'ok' ? 'var(--green)' : meta.tone === 'bad' ? 'var(--red)' : 'var(--amber)'
  const chats = inbox?.chats ?? null
  const unread = inbox?.counts?.unreadChatsCount ?? null

  return (
    <section className="panel" style={{ marginBottom: 18 }}>
      <div className="panel-title">
        <div>
          <span className="kicker">Read-only · never replies</span>
          <h3>Inbox</h3>
        </div>
        <span style={{ color: tone, fontSize: 11, fontWeight: 600, textAlign: 'right' }}>
          {meta.label}
          {unread ? ` · ${unread} unread` : ''}
        </span>
      </div>

      {/* Why this panel exists at all, said once where it is read rather than
          buried in a tooltip: on this platform the money is in paid DMs, so a
          fan who wrote and was not answered is a sale that did not happen. */}
      <p className="muted-sm" style={{ marginBottom: 14 }}>
        Paid DMs are the revenue engine on Fanvue — a fan who writes and is not answered is a sale
        that did not happen. This is the platform&apos;s own conversation list, and this app only
        reads it: no reply, no marking read, nothing changed on the account.
      </p>

      {error && (
        <div className="panel" style={{ background: 'var(--bg-card)', borderColor: 'var(--red)' }}>
          <p style={{ fontSize: 12, color: 'var(--red)', marginBottom: 6 }}>
            The inbox could not be reached at all — this is not an empty inbox.
          </p>
          <p className="muted-sm">{error}</p>
        </div>
      )}

      {!error && inbox && inbox.state !== 'ok' && (
        <div className="panel" style={{ background: 'var(--bg-card)', borderColor: 'var(--border-light)' }}>
          <p style={{ fontSize: 12, color: tone, marginBottom: 6 }}>
            {/* The distinction the state machine exists to draw. */}
            {inbox.chats === null && 'No conversation list is shown, because none could be read.'}
          </p>
          <p className="muted-sm" style={{ lineHeight: 1.55 }}>{inbox.detail || inbox.note}</p>
        </div>
      )}

      {!error && inbox?.state === 'ok' && chats && chats.length === 0 && (
        <p className="muted-sm">
          The platform answered and nobody has written yet. That is a real zero — it is not the same
          answer as an unreadable inbox, which is what the states above are for.
        </p>
      )}

      {!error && chats && chats.length > 0 && (
        <div>
          {chats.slice(0, 8).map((row, i) => (
            <div
              key={row.user_uuid || `${row.handle}-${i}`}
              style={{
                display: 'flex', gap: 10, alignItems: 'flex-start',
                padding: '10px 0', borderTop: i ? '1px solid var(--border)' : 'none',
              }}
            >
              <span
                title={row.online ? 'Online now' : 'Offline'}
                style={{
                  width: 7, height: 7, borderRadius: 99, marginTop: 6, flexShrink: 0,
                  background: row.online ? 'var(--green)' : 'var(--border)',
                }}
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <b style={{ fontSize: 13 }}>{row.display_name || row.handle || 'Unnamed fan'}</b>
                  {row.handle && <span className="muted-sm">@{row.handle}</span>}
                  {row.is_top_spender && <span style={{ fontSize: 10, color: 'var(--amber)' }}>top spender</span>}
                  {row.is_muted && <span className="muted-sm">muted</span>}
                  {!row.is_read && (
                    <span style={{
                      fontSize: 10, fontWeight: 600, padding: '1px 7px', borderRadius: 10,
                      background: 'var(--green-dim)', color: 'var(--green)',
                    }}>
                      {row.unread_messages > 0 ? `${row.unread_messages} new` : 'unread'}
                    </span>
                  )}
                  {row.last_message_is_paid && (
                    <span style={{
                      fontSize: 10, fontWeight: 600, padding: '1px 7px', borderRadius: 10,
                      background: 'var(--amber-dim, rgba(245,181,66,0.15))', color: 'var(--amber)',
                    }}>
                      paid
                    </span>
                  )}
                </div>
                <p className="muted-sm" style={{
                  marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {row.last_message_from_creator ? 'You: ' : ''}{messageLabel(row)}
                </p>
              </div>
              <span className="muted-sm" style={{ whiteSpace: 'nowrap' }}>
                {row.last_message_at ? new Date(row.last_message_at).toLocaleDateString() : ''}
              </span>
            </div>
          ))}
          {chats.length > 8 && (
            <p className="muted-sm" style={{ marginTop: 10 }}>
              Showing 8 of {chats.length} read. More exist than are shown.
            </p>
          )}
        </div>
      )}

      <p className="muted-sm" style={{ marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
        {/* Said on every render, in every state: what this cannot do is the
            property that matters most about it. */}
        This panel cannot send, reply, or mark anything read — {inbox?.can_send === false ? 'and it says so in its own response' : 'the API reports no sending capability'}. A paid
        message is marked by its type and carries no amount; the money is in the earnings figures above.
      </p>
    </section>
  )
}

/**
 * The one control that unblocks everything else.
 *
 * `platform_connected` is the first launch gate and the only one no amount of
 * application code can pass on its own — Fanvue issues no static API keys, so
 * the grant exists only if the account owner opens a consent screen and
 * approves it. Until this pass the gate's `next_step` told the operator to open
 * `GET /api/v1/fanvue/connect` in a browser by hand, and no button anywhere in
 * the app did it.
 *
 * Configured / authorized / armed are shown as three separate lights rather
 * than one, because they have three different fixes: a missing client id, a
 * consent that has not happened yet, and a deliberate `FANVUE_PUBLISH_ENABLED`
 * switch that stays off until the operator wants posts going out.
 */
function PlatformConnectPanel() {
  const [status, setStatus] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const load = () => fanvueStatus().then(setStatus).catch(() => setStatus(null))
  useEffect(() => { load() }, [])

  const connect = async () => {
    setBusy(true); setMsg('')
    try {
      const { authorize_url } = await connectFanvue()
      // A full-page navigation: Fanvue has to render its own consent screen.
      window.location.href = authorize_url
    } catch (e: any) {
      // A 503 here is the honest "set FANVUE_CLIENT_ID" answer, not a crash.
      setMsg(e.message || 'Could not start the Fanvue authorization')
      setBusy(false)
    }
  }

  const lights: Array<[string, boolean, string]> = status ? [
    ['Configured', !!status.configured, 'FANVUE_CLIENT_ID and FANVUE_CREATOR_UUID are set'],
    ['Authorized', !!status.authorized, status.authorized ? `token from ${status.token_source}` : 'no grant yet — press Connect'],
    ['Armed', !!status.armed, status.armed ? 'FANVUE_PUBLISH_ENABLED=true' : 'FANVUE_PUBLISH_ENABLED is not true'],
  ] : []

  return (
    <section className="panel" style={{ marginBottom: 18 }}>
      <div className="panel-title">
        <div>
          <span className="kicker">Platform</span>
          <h3>Fanvue — the only platform this app can sell on</h3>
        </div>
        <span style={{ fontSize: 11, fontWeight: 600, color: status?.ok ? 'var(--green)' : 'var(--amber)' }}>
          {status === null ? 'Status unreadable' : status.ok ? 'Token live' : 'Not ready'}
        </span>
      </div>

      {status === null ? (
        <p className="muted-sm">
          The status could not be read — that is not the same as “nothing is configured”. Is the API
          running?
        </p>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginBottom: 14 }}>
            {lights.map(([label, on, hint]) => (
              <div key={label} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', minWidth: 0 }}>
                <span style={{
                  width: 7, height: 7, borderRadius: 99, marginTop: 5, flexShrink: 0,
                  background: on ? 'var(--green)' : 'var(--border)',
                }} />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 600 }}>{label}</div>
                  <div className="muted-sm" style={{ overflowWrap: 'anywhere' }}>{hint}</div>
                </div>
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <button className="primary-button" onClick={connect} disabled={busy}>
              {status.authorized ? 'Re-authorize Fanvue' : 'Connect Fanvue'}
            </button>
            {status.authorized && (
              <button
                className="secondary-button"
                onClick={async () => { await disconnectFanvue().catch(() => {}); load() }}
              >
                Disconnect
              </button>
            )}
            <a className="text-button" href="/socials">Platform accounts →</a>
          </div>

          {msg && <p className="muted-sm" style={{ marginTop: 10, color: 'var(--red)' }}>{msg}</p>}

          {status.detail && !status.ok && (
            <p className="muted-sm" style={{ marginTop: 10, lineHeight: 1.55 }}>{status.detail}</p>
          )}
          {status.next_step && (
            <p className="muted-sm" style={{ marginTop: 8, lineHeight: 1.55 }}>{status.next_step}</p>
          )}

          <p className="muted-sm" style={{ marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
            {/* Said plainly, because the button opens a consent screen the
                operator is about to approve and they should know what it asks
                for before they see it. The list is the one the API reports in
                `scopes_requested` — `write:post` publishes, `write:media`
                uploads, `read:creator`/`read:self` are the account records,
                `read:insights` is what can see money, `read:chat` is the inbox. */}
            Connecting opens Fanvue&apos;s own consent screen, which you approve yourself. The scopes it
            asks for are listed by the API and rendered here rather than described in prose, so this
            line cannot drift from what the consent screen actually says:{' '}
            <code style={{ overflowWrap: 'anywhere' }}>
              {(status.scopes_requested || []).join(' ') || 'none reported'}
            </code>. {' '}
            Note what is missing: <code>write:chat</code> is not requested. Nothing here messages a
            fan as you.
          </p>
        </>
      )}
    </section>
  )
}

export default function MonetizationPage() {  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [personas, setPersonas] = useState<PersonaDetail[]>([])
  const [readiness, setReadiness] = useState<MonetizationReadiness | null>(null)
  // The inbox is fetched on its own, not folded into the Promise.all below, so
  // that an unreachable inbox leaves the rest of the page readable. Its failure
  // is a *state of the page*, and the one thing it must never decay into is an
  // empty conversation list.
  const [inbox, setInbox] = useState<ConversationsReading | null>(null)
  const [inboxError, setInboxError] = useState('')
  const [members, setMembers] = useState(100)
  const [conversion, setConversion] = useState(3)
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([getDashboardSummary(), listPersonas(), getMonetizationReadiness()])
      .then(([dashboard, models, gates]) => {
        setSummary(dashboard); setPersonas(models); setReadiness(gates)
      })
      .catch(err => setError(err instanceof Error ? err.message : 'Could not load monetization data'))
  }, [])

  useEffect(() => {
    getConversations()
      .then(setInbox)
      .catch(err => setInboxError(err instanceof Error ? err.message : 'Could not reach the inbox'))
  }, [])

  const projection = useMemo(() => members * (conversion / 100) * 14.99, [members, conversion])
  const active = personas.filter(p => p.status === 'active').length
  const real = summary?.real_revenue
  const cleared = readiness ? readiness.checks.filter(c => c.done).length : 0

  return (
    <main className="workspace">
      <header className="topbar">
        <div className="crumb"><a href="/">Persona Studio</a><b>/</b><strong>Monetization</strong></div>
      </header>
      <div className="content">
        <section className="page-heading">
          <div>
            <p className="eyebrow">Creator business</p>
            <h1>Monetization studio</h1>
            <p>Turn approved synthetic personas into a transparent, repeatable subscription business.</p>
          </div>
          <a className="secondary-button" href="/settings">Review rights &amp; consent</a>
        </section>

        {error && <div className="panel" style={{ borderColor: 'var(--red)', color: 'var(--red)', marginBottom: 18 }}>{error}</div>}

        <section className="metrics-grid">
          {/* This card used to read "Recorded revenue … From verified analytics",
              showing `AnalyticsSnapshot.revenue` — the figure the Instagram and
              TikTok syncs write as 0, because neither API reports money. There
              are no "verified analytics" here, and the number is always zero.
              The card now carries the platform's own ledger, which is the only
              real-money reading this app can produce. */}
          <article className="metric-card">
            <div className="metric-top"><span>Received this month</span><span className="metric-icon">$</span></div>
            <strong>{formatMoney(real?.this_month_net, real?.currency)}</strong>
            <div className="metric-foot">
              <span style={{ color: summary?.real_revenue_state === 'ok' ? 'var(--green)' : 'var(--amber)' }}>
                {summary?.real_revenue_state === 'ok' ? 'From the platform ledger' : 'Ledger not readable'}
              </span>
              <small>{real?.net !== null && real?.net !== undefined
                ? `${formatMoney(real.net, real.currency)} all time`
                : `Recorded analytics ${formatCurrency(summary?.revenue || 0)} — not payments`}</small>
            </div>
          </article>
          <article className="metric-card"><div className="metric-top"><span>Active personas</span><span className="metric-icon">◎</span></div><strong>{active}</strong><div className="metric-foot"><span className={active ? 'positive' : 'warning'}>{active ? 'Ready for offers' : 'Build an identity first'}</span><small>{personas.length} total</small></div></article>
          <article className="metric-card"><div className="metric-top"><span>Projected MRR</span><span className="metric-icon">↗</span></div><strong>${projection.toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong><div className="metric-foot"><span className="warning">Planning scenario</span><small>{members} visitors · {conversion}% convert</small></div></article>
          <article className="metric-card"><div className="metric-top"><span>Provider readiness</span><span className="metric-icon">◉</span></div><strong>{summary ? `${summary.health.online}/${summary.health.total}` : '—'}</strong><div className="metric-foot"><span className={summary?.health.online === summary?.health.total ? 'positive' : 'warning'}>{summary?.health.online === summary?.health.total ? 'Operational' : 'Needs attention'}</span><small>Required services</small></div></article>
        </section>

        <PlatformConnectPanel />

        <InboxPanel inbox={inbox} error={inboxError} />

        <section className="dashboard-grid">
          <article className="panel">
            <div className="panel-title"><div><span className="kicker">Planning tool</span><h3>Subscription scenario</h3></div><span className="count-badge" style={{ background: 'var(--green-dim)', color: 'var(--green)' }}>ESTIMATE</span></div>
            <label style={{ display: 'block', marginBottom: 18, color: 'var(--text-secondary)' }}>Monthly profile visitors <b style={{ float: 'right', color: 'var(--text)' }}>{members}</b><input aria-label="Monthly profile visitors" type="range" min="0" max="10000" step="50" value={members} onChange={e => setMembers(Number(e.target.value))} style={{ width: '100%', accentColor: 'var(--green)', marginTop: 10 }} /></label>
            <label style={{ display: 'block', color: 'var(--text-secondary)' }}>Subscriber conversion <b style={{ float: 'right', color: 'var(--text)' }}>{conversion}%</b><input aria-label="Subscriber conversion" type="range" min="0" max="20" step="0.5" value={conversion} onChange={e => setConversion(Number(e.target.value))} style={{ width: '100%', accentColor: 'var(--green)', marginTop: 10 }} /></label>
            <div className="panel" style={{ marginTop: 22, background: 'var(--bg-card)', borderColor: 'var(--border-light)' }}><span className="kicker">Base tier gross</span><strong style={{ display: 'block', fontSize: 28, marginTop: 4 }}>${projection.toLocaleString(undefined, { maximumFractionDigits: 0 })}<small style={{ fontSize: 12, color: 'var(--text-muted)', fontWeight: 400 }}> / month</small></strong><p className="muted-sm" style={{ marginTop: 6 }}>Planning only. Platform fees, taxes, refunds, and chargebacks are not included.</p></div>
          </article>

          <article className="panel">
            <div className="panel-title"><div><span className="kicker">Offer ladder</span><h3>What to sell</h3></div><a href="/production" className="text-button">Create content →</a></div>
            {offers.map(offer => <div key={offer.name} style={{ display: 'flex', gap: 12, padding: '12px 0', borderBottom: '1px solid var(--border)' }}><span style={{ width: 8, height: 8, borderRadius: 99, background: offer.color, marginTop: 6, flexShrink: 0 }} /><div style={{ flex: 1 }}><b style={{ fontSize: 13 }}>{offer.name}</b><p className="muted-sm">{offer.detail}</p></div><strong style={{ color: offer.color, whiteSpace: 'nowrap' }}>{offer.price ? `$${offer.price}` : 'Free'}</strong></div>)}
            {/* The prices above are a menu to think with, not what this app
                charges. The only price it can actually send is
                DEFAULT_PPV_PRICE, and the gate above says whether one is
                stated — so the two are labelled apart rather than left to look
                like the same number. */}
            <p className="muted-sm" style={{ marginTop: 12 }}>
              These are illustrative tiers for planning. The price this studio actually
              charges per post is the single stated default, checked by the launch gate above.
            </p>
          </article>
        </section>

        {/* Launch gates, measured.
            What stood here was four constants dressed as gates: two returned
            `true` unconditionally, one was `Boolean(summary?.health)` — true
            whenever the dashboard loaded — and "synthetic identity declared"
            was inferred from a persona row existing, which is not evidence that
            any profile carries a disclosure. A gate that cannot fail is
            decoration on the one page whose job is to say when money can start
            arriving. Each of these is read from configuration or the calendar,
            and each carries the action that would clear it. */}
        <section className="panel" style={{ marginBottom: 18 }}>
          <div className="panel-title">
            <div>
              <span className="kicker">Launch gates</span>
              <h3>What is still between this studio and a payment</h3>
            </div>
            <span className="count-badge" style={{
              background: readiness?.ready ? 'var(--green-dim)' : 'var(--amber-dim)',
              color: readiness?.ready ? 'var(--green)' : 'var(--amber)',
            }}>
              {readiness ? `${cleared}/${readiness.checks.length} clear` : '—'}
            </span>
          </div>
          {readiness ? (
            <>
              <div className="persona-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
                {readiness.checks.map(item => (
                  <div key={item.key} style={{ padding: 14, background: 'var(--bg-card)', borderRadius: 'var(--radius-sm)', border: `1px solid ${item.done ? 'var(--border)' : 'rgba(240,173,78,.5)'}` }}>
                    <div style={{ color: item.done ? 'var(--green)' : 'var(--amber)', fontWeight: 700, marginBottom: 8 }}>{item.done ? '✓ Ready' : '○ Blocked'}</div>
                    <b style={{ fontSize: 13 }}>{item.label}</b>
                    <p className="muted-sm" style={{ marginTop: 6 }}>{item.detail}</p>
                    {!item.done && item.next_step && (
                      <p className="muted-sm" style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--border)' }}>{item.next_step}</p>
                    )}
                  </div>
                ))}
              </div>
              <p className="muted-sm" style={{ marginTop: 14 }}>{readiness.note}</p>
            </>
          ) : (
            <p className="muted-sm">{error ? 'Gates could not be read — see the error above.' : 'Reading the gates…'}</p>
          )}
        </section>

        <section className="panel" style={{ borderColor: 'rgba(91,156,246,.35)', background: 'linear-gradient(135deg, rgba(91,156,246,.08), var(--bg-panel))' }}>
          <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}><span style={{ fontSize: 22 }}>ⓘ</span><div><h3 style={{ marginBottom: 6 }}>Platform-safe operating model</h3><p className="muted-sm" style={{ maxWidth: 760 }}>Only use platforms and account actions that permit synthetic creators and provide an official integration or an explicit human-in-the-loop process. This workspace does not scrape creator sites, impersonate real people, bypass platform controls, or auto-publish to platforms without an approved API path. For any platform without an official API, keep publishing and account verification manual.</p></div></div>
        </section>
      </div>
    </main>
  )
}
