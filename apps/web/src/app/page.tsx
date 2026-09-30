'use client'

import { useEffect, useState } from 'react'
import { getDashboardSummary } from '@/lib/api'
import type { DashboardSummary, RealRevenue } from '@/lib/types'
import { GRADIENTS } from '@/lib/constants'
import { Icons } from '@/lib/icons'
import { formatCurrency, formatMoney, getGreeting, getDayString } from '@/lib/utils'

/* ── Money, and what it is not ────────────────────────────
 *
 * This panel used to draw a sparkline whose every point was hardcoded (only
 * the last one moved), over a fabricated SEP–AUG axis, next to three scenario
 * buttons that did nothing — presenting a revenue trajectory that no data in
 * this app produces. It was replaced by the figures that do exist, with a note
 * claiming there was no series here to chart.
 *
 * That claim was wrong, and this file carried it for a day: Fanvue's earnings
 * summary includes `overTime`, its own ledger bucketed by day or week. So the
 * series below is drawn from that, and the rule is the old one inverted — the
 * chart shows what the platform actually sent, and shows nothing at all when it
 * sent no buckets. An empty chart with a drawn axis reads as a month of zeros,
 * which is the same lie in a nicer shape.
 *
 * The one number that is real is the connected platform's own ledger. The one
 * that is not is `data.revenue`, which is still rendered below it — labelled,
 * because an unlabelled R 0 on a dashboard is read as "we earned nothing". */

const SOURCE_LABELS: Record<string, string> = {
  messages: 'Paid messages',
  subs: 'Subscriptions',
  posts: 'Paid posts',
  tips: 'Tips',
  referrals: 'Referrals',
  renewals: 'Renewals',
  other: 'Other',
}

/* Each state is a different next action, so each gets its own words. 'ok' is
   the only one that carries a figure, and it is the only one that reads green. */
const REVENUE_STATES: Record<string, string> = {
  ok: 'From the platform’s own ledger',
  not_configured: 'No platform connected',
  disabled: 'Connected, but publishing is not armed',
  unsupported: 'This platform exposes no earnings endpoint',
  error: 'The platform could not be read',
}

/** A bucket boundary as a short date. `2026-09-03T00:00:00Z` → `Sep 3`. */
function bucketLabel(iso: string): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  return at.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

/* The platform's own ledger, drawn to scale.
 *
 * Bars, not a line: the buckets are discrete periods and a line between two of
 * them implies money arriving in between, which is not what the platform
 * reported. Every bar's height is its value over the window's peak, so the
 * tallest bar is always the largest bucket that actually exists — the old
 * sparkline's y-axis named magnitudes nothing had reached.
 *
 * Requires two buckets. One bucket is a number, and that number is already in
 * the rows above; a chart of it would be a chart of a single point dressed as a
 * trend. Returns null rather than an empty frame. */
function RevenueSeries({ real }: { real: RealRevenue }) {
  const points = (real.over_time || []).filter(
    (p): p is { period_start: string; gross: number | null; net: number } =>
      typeof p.net === 'number',
  )
  if (points.length < 2) return null

  const peak = Math.max(...points.map(p => p.net), 0)
  if (peak <= 0) {
    // Every bucket the platform reported was zero or negative (refund rows
    // carry negatives). A chart of that is a flat line that reads as broken;
    // the sentence says the same thing without pretending to be a chart.
    return (
      <p className="money-note">
        No bucket in this window was positive. The platform reported {points.length}{' '}
        {bucketLabel(points[0].period_start)}–{bucketLabel(points[points.length - 1].period_start)}.
      </p>
    )
  }

  const unit = real.period?.granularity === 'week' ? 'week' : 'day'
  return (
    <figure className="money-series-figure">
      <div
        className="money-series"
        role="img"
        aria-label={`Net earnings per ${unit} from the platform ledger: ${points
          .map(p => `${bucketLabel(p.period_start)} ${formatMoney(p.net, real.currency)}`)
          .join(', ')}`}
      >
        {points.map(p => (
          <i
            key={p.period_start}
            style={{ height: `${Math.max(2, (p.net / peak) * 100)}%` }}
            title={`${bucketLabel(p.period_start)} — ${formatMoney(p.net, real.currency)}`}
          />
        ))}
      </div>
      <figcaption className="money-series-axis">
        <span>{bucketLabel(points[0].period_start)}</span>
        <span>
          net per {unit} · peak {formatMoney(peak, real.currency)}
        </span>
        <span>{bucketLabel(points[points.length - 1].period_start)}</span>
      </figcaption>
    </figure>
  )
}

/* ── Main Dashboard ────────────────────────────────────── */
export default function Dashboard() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getDashboardSummary()
      .then(data => { setSummary(data); setLoading(false) })
      .catch(err => { setError(err.message); setLoading(false) })
  }, [])

  if (loading) {
    return (
      <main className="workspace">
        <header className="topbar">
          <button className="mobile-menu" aria-label="Open menu">{Icons.menu}</button>
          <div className="crumb"><span>Persona Studio</span><b>/</b><strong>Overview</strong></div>
        </header>
        <div className="content">
          <section className="page-heading">
            <div>
              <p className="eyebrow" style={{ opacity: 0.4 }}>Loading</p>
              <h1 style={{ opacity: 0.3, width: 260, height: 28, background: 'var(--bg-panel)', borderRadius: 6 }}>&nbsp;</h1>
              <p style={{ opacity: 0.2, width: 360, height: 16, background: 'var(--bg-panel)', borderRadius: 4, marginTop: 8 }}>&nbsp;</p>
            </div>
          </section>
          <section className="metrics-grid">
            {[0,1,2,3].map(i => (
              <article key={i} className="metric-card" style={{ opacity: 0.4 }}>
                <div style={{ width: 100, height: 14, background: 'var(--bg-card)', borderRadius: 4 }}>&nbsp;</div>
                <div style={{ width: 60, height: 28, background: 'var(--bg-card)', borderRadius: 4, marginTop: 4 }}>&nbsp;</div>
                <div style={{ width: 120, height: 14, background: 'var(--bg-card)', borderRadius: 4, marginTop: 8 }}>&nbsp;</div>
              </article>
            ))}
          </section>
        </div>
      </main>
    )
  }

  if (error) {
    return (
      <main className="workspace">
        <header className="topbar">
          <button className="mobile-menu" aria-label="Open menu">{Icons.menu}</button>
          <div className="crumb"><span>Persona Studio</span><b>/</b><strong>Overview</strong></div>
        </header>
        <div className="content" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '60vh' }}>
          <div style={{ textAlign: 'center', maxWidth: 360 }}>
            <div style={{ fontSize: 32, marginBottom: 12 }}>⚠️</div>
            <p style={{ color: 'var(--text)', fontWeight: 600, fontSize: 15, marginBottom: 6 }}>Could not reach the API</p>
            <p style={{ color: 'var(--text-muted)', fontSize: 13, marginBottom: 16, lineHeight: 1.5 }}>{error}. Make sure the API server is running on port 8000.</p>
            <button className="primary-button" onClick={() => { setLoading(true); setError(null); window.location.reload() }}>{Icons.activity} Retry</button>
          </div>
        </div>
      </main>
    )
  }

  const data = summary!
  const attentionCount = data.attention_items.length

  const real = data.real_revenue
  const revenueOk = data.real_revenue_state === 'ok'
  const revenueLabel = REVENUE_STATES[data.real_revenue_state] || 'The ledger could not be read'
  const revenueTone = revenueOk ? 'var(--green)' : 'var(--amber)'
  const sources = Object.entries(real.by_source || {})

  return (
    <main className="workspace">
      <header className="topbar">
        <button className="mobile-menu" aria-label="Open menu">{Icons.menu}</button>
        <div className="crumb"><span>Persona Studio</span><b>/</b><strong>Overview</strong></div>
        <div className="top-actions">
          <button className="search-button">{Icons.search}<span>Search or jump to…</span><kbd>⌘ K</kbd></button>
          <a href="/models/create"><button className="primary-button">{Icons.plus} Create model</button></a>
        </div>
      </header>

      <div className="content">
        <section className="page-heading">
          <p className="eyebrow">{getDayString()}</p>
          <h1>{getGreeting()}, Persona.</h1>
          <p>
            {data.active_models > 0
              ? `Your studio is stable. ${data.training_models > 0 ? `${data.training_models} model${data.training_models > 1 ? 's' : ''} training.` : 'All models operational.'}`
              : 'No models yet. Create your first persona to get started.'}
          </p>
          <a href="/production"><button className="secondary-button">{Icons.activity} Open live production</button></a>
        </section>

        {/* Metrics — each card navigates to its section */}
        <section className="metrics-grid">
          <a href="/models" className="section-link" style={{ textDecoration: 'none' }}>
            <article className="metric-card">
              <div className="metric-top"><span>Active models</span><span className="metric-icon">{Icons.users}</span></div>
              <strong>{data.active_models}</strong>
              <div className="metric-foot">
                <span className="positive">{data.training_models > 0 ? `${data.training_models} training` : 'All active'}</span>
                <small>{data.total_models} total identities</small>
              </div>
            </article>
          </a>
          <a href="/production" className="section-link" style={{ textDecoration: 'none' }}>
            <article className="metric-card">
              <div className="metric-top"><span>Packs in production</span><span className="metric-icon">{Icons.package}</span></div>
              <strong>{data.total_packs}</strong>
              <div className="metric-foot">
                <span className={attentionCount > 0 ? 'warning' : 'positive'}>
                  {attentionCount > 0 ? `${attentionCount} need attention` : 'No pending items'}
                </span>
                <small>{data.total_shoots} total shoots</small>
              </div>
            </article>
          </a>
          <a href="/analytics" className="section-link" style={{ textDecoration: 'none' }}>
            <article className="metric-card">
              <div className="metric-top"><span>Paid this month</span><span className="metric-icon">{Icons.dollar}</span></div>
              <strong>{formatMoney(real.this_month_net, real.currency)}</strong>
              <div className="metric-foot">
                <span style={{ color: revenueTone }}>{revenueLabel}</span>
                <small>
                  {real.net !== null
                    ? `${formatMoney(real.net, real.currency)} all time, net of the platform's fee`
                    : `Recorded analytics ${formatCurrency(data.revenue)} — not payments`}
                </small>
              </div>
            </article>
          </a>
          <a href="/settings" className="section-link" style={{ textDecoration: 'none' }}>
            <article className="metric-card">
              <div className="metric-top"><span>System health</span><span className="metric-icon">{Icons.trending}</span></div>
              <strong>{data.health.online}/{data.health.total}</strong>
              <div className="metric-foot">
                <span className={data.health.online === data.health.total ? 'positive' : 'warning'}>
                  {data.health.online === data.health.total ? 'All services online' : `${data.health.total - data.health.online} degraded`}
                </span>
                <small>Provider status</small>
              </div>
            </article>
          </a>
        </section>

        {/* Dashboard grid */}
        <section className="dashboard-grid">
          <article className="panel revenue-panel">
            <div className="panel-head">
              <div>
                <span className="kicker">Money in</span>
                <h2>{formatMoney(real.this_month_net, real.currency)}</h2>
                <p><b style={{ color: revenueTone }}>{revenueLabel}</b></p>
              </div>
              <a href="/analytics"><button className="text-button">Analytics {Icons.arrowRight}</button></a>
            </div>

            {revenueOk ? (
              <div className="money-rows">
                {sources.length === 0 ? (
                  <p className="money-note">
                    The platform reported a total but no breakdown by source for this window.
                  </p>
                ) : sources.map(([name, net]) => (
                  <div key={name} className="money-row">
                    <span>{SOURCE_LABELS[name] || name}</span>
                    <b>{formatMoney(net, real.currency)}</b>
                  </div>
                ))}
                <div className="money-row money-row-total">
                  <span>All time, net of fee</span>
                  <b>{formatMoney(real.net, real.currency)}</b>
                </div>
                {/* Renders itself or nothing — see RevenueSeries. */}
                <RevenueSeries real={real} />
              </div>
            ) : (
              <div className="money-note">
                <p>
                  {data.real_revenue_detail ||
                    'Nothing has been read from a platform ledger, so there is no figure to show — which is not the same as zero.'}
                </p>
                <p>
                  Nothing in this app can take a payment. The wallet and ledger under
                  <code>/fan</code> are simulated and the only payment processor configured is
                  <code>fake</code>, so real money can only arrive through a platform the studio
                  is connected to and publishing to.
                </p>
              </div>
            )}

            <div className="money-recorded">
              <span>Recorded analytics revenue</span>
              <b>{formatCurrency(data.revenue)}</b>
              <small>{data.revenue_note}</small>
            </div>
          </article>

          <article className="panel production-panel">
            <div className="panel-title">
              <div><span className="kicker">Production queue</span><h3>Live jobs</h3></div>
              <a href="/production"><button className="text-button">View all {Icons.arrowRight}</button></a>
            </div>
            {data.shoots.length === 0 ? (
              <div className="attention-item" style={{ opacity: 0.5, cursor: 'default' }}>
                <span className="attention-icon" style={{ color: 'var(--muted)' }}>{Icons.image}</span>
                <p><b>No active shoots</b><small>Create a shoot from a persona to get started</small></p>
              </div>
            ) : (
              <div className="job-table">
                {data.shoots.map(shoot => {
                  const imgs = shoot.generated_images || []
                  const firstImg = imgs.length > 0 ? imgs[0] : null
                  const shootImgUrl = firstImg ? (() => {
                    // Convert 'storage/shoots/abc123/shot_01.png' -> '/api/v1/shoots/abc123/images/shot_01.png'
                    const parts = firstImg.split('/')
                    if (parts.length >= 4) return `/api/v1/shoots/${parts[2]}/images/${parts[3]}`
                    return null
                  })() : null
                  return (
                    <a key={shoot.id} href="/production" className="job-row-link" style={{ textDecoration: 'none', color: 'inherit', display: 'block' }}>
                    <div className="job-row">
                      <span className="job-icon" style={shootImgUrl ? { overflow: 'hidden', borderRadius: 6 } : {}}>
                        {shootImgUrl ? (
                          <img src={shootImgUrl} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                        ) : (
                          shoot.asset_type === 'video' ? Icons.activity : shoot.asset_type === 'training' ? Icons.cpu : Icons.image
                        )}
                      </span>
                      <p><b>{shoot.name}</b><small>{shoot.theme || 'No theme'} · {shoot.persona_name}</small></p>
                      <div className="progress-wrap"><div><i style={{ width: `${shoot.progress}%` }}></i></div>                    <span>{Math.round(shoot.progress)}%</span></div>
                      <em className={`status ${shoot.status}`}>{shoot.status.toUpperCase()}</em>
                    </div>
                    </a>
                  )
                })}
              </div>
            )}
          </article>

          <article className="panel attention-panel">
            <div className="panel-title">
              <div><span className="kicker">Needs attention</span><h3>Human approval gates</h3></div>
              <span className="count-badge">{attentionCount}</span>
            </div>
            {data.attention_items.length === 0 ? (
              <div className="attention-item" style={{ opacity: 0.5, cursor: 'default' }}>
                <span className="attention-icon" style={{ color: 'var(--green)' }}>{Icons.check}</span>
                <p><b>No pending approvals</b><small>All workflows are running smoothly</small></p>
              </div>
            ) : data.attention_items.map(item => (
              <a key={item.id} href={`/personas/${item.id}`} className="attention-item" style={{ textDecoration: 'none', color: 'inherit' }}>
                <span className="attention-icon amber">{Icons.shield}</span>
                <p><b>{item.name}</b><small>{item.type.replace(/_/g, ' ')}</small></p>
                <span><b>Review</b>{Icons.arrowRight}</span>
              </a>
            ))}
          </article>

          <article className="panel capacity-panel">
            <a href="/settings" className="panel-title-link" style={{ textDecoration: 'none', color: 'inherit' }}>
            <div className="panel-title">
              <div><span className="kicker">Infrastructure</span><h3>System health</h3></div>
              {Icons.cpu}
            </div>
            </a>
            {data.health.checks.map(check => (
              <div key={check.service} className="capacity">
                <div><p>{check.service}</p><span>{check.status === 'green' ? 'Operational' : check.status === 'yellow' ? 'Degraded' : 'Offline'}</span></div>
                <div className="capacity-bar"><i className={check.status === 'red' ? 'alert' : ''} style={{ width: check.status === 'green' ? '100%' : check.status === 'yellow' ? '60%' : '10%' }}></i></div>
                <b style={{ color: check.status === 'green' ? 'var(--green)' : check.status === 'yellow' ? '#fbbf24' : '#ef4444' }}>
                  {check.status === 'green' ? '✓' : check.status === 'yellow' ? '⚠' : '✗'}
                </b>
              </div>
            ))}
            <div className="storage-row">
              {Icons.database}
              <p><b>{data.total_packs} packs</b><small>Content in production</small></p>
            </div>
          </article>
        </section>

        {/* Persona grid */}
        <section className="section-row">
          <div><span className="kicker">Identity portfolio</span><h2>Active models</h2></div>
          <a href="/models/create"><button className="text-button">Create model {Icons.arrowRight}</button></a>
        </section>

        <section className="persona-grid">
          {data.personas.length === 0 ? (
            <div style={{ gridColumn: '1 / -1', textAlign: 'center', padding: '48px 0', color: 'var(--muted)' }}>
              <p style={{ fontSize: 15, marginBottom: 8 }}>No personas yet</p>
              <a href="/models/create" style={{ color: 'var(--green)', textDecoration: 'none' }}>Create your first model →</a>
            </div>
          ) : data.personas.map((p, i) => (
            <a key={p.id} href={`/personas/${p.id}`} style={{ textDecoration: 'none' }}>
              <article className="persona-card">
                <div className="persona-art" style={{ background: GRADIENTS[i % GRADIENTS.length] }}>
                  {p.avatar_url ? (
                    <img src={p.avatar_url} alt={p.name} style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: 'inherit' }} />
                  ) : (
                    <span>{p.name[0]}</span>
                  )}
                  <em className={`persona-state ${p.identity_status === 'training' || p.status === 'building' ? 'training' : 'active'}`}>
                    {(p.identity_status || p.status || 'active').toUpperCase()}
                  </em>
                  <div className="art-grain"></div>
                </div>
                <div className="persona-info">
                  <div>
                    <span className="kicker">{p.name.toUpperCase()}_{String(i + 1).padStart(4, '0')}</span>
                    <h3>{p.name}</h3>
                    <p>{p.brand || 'Creator'}</p>
                  </div>
                  <button aria-label={`Open ${p.name}`}>{Icons.arrowRight}</button>
                </div>
                <div className="persona-stats">
                  <span><small>Identity</small><b>{p.identity_score ? `${(p.identity_score * 100).toFixed(1)}%` : '—'}</b></span>
                  <span><small>Packs</small><b>{p.packs_count}</b></span>
                  <span><small>Age</small><b>{p.age}</b></span>
                </div>
              </article>
            </a>
          ))}
        </section>
      </div>
    </main>
  )
}
