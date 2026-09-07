'use client'

import { useEffect, useState } from 'react'
import { getDashboardSummary } from '@/lib/api'
import type { DashboardSummary } from '@/lib/types'
import { GRADIENTS } from '@/lib/constants'
import { Icons } from '@/lib/icons'
import { formatCurrency, getGreeting, getDayString } from '@/lib/utils'

/* ── Sparkline (dashboard-only) ──────────────────────── */
function Sparkline({ revenue }: { revenue: number }) {
  const base = 56
  const peak = revenue > 0 ? 8 : 30
  const coords = ['0,' + base, '9.1,' + (base - 3), '18.2,' + (base - 6), '27.3,' + (base - 10),
    '36.4,' + (base - 14), '45.5,' + (base - 18), '54.5,' + (base - 22), '63.6,' + (base - 27),
    '72.7,' + (base - 32), '81.8,' + (base - 37), '90.9,' + (base - 42), '100,' + peak]
  const linePath = 'M' + coords.join(' L')
  const fillPath = linePath + ' L100,60 L0,60 Z'
  return (
    <svg className="sparkline" viewBox="0 0 100 60" preserveAspectRatio="none">
      <defs>
        <linearGradient id="sparkFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#d9fb71" stopOpacity=".28"/>
          <stop offset="1" stopColor="#d9fb71" stopOpacity="0"/>
        </linearGradient>
      </defs>
      <path d={fillPath} fill="url(#sparkFill)"/>
      <path d={linePath} fill="none" stroke="#d9fb71" strokeWidth="2" vectorEffect="non-scaling-stroke"/>
    </svg>
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
            <p style={{ color: 'var(--text-muted)', fontSize: 13, marginBottom: 16, lineHeight: 1.5 }}>{error}. Make sure the API server is running on port 8001.</p>
            <button className="primary-button" onClick={() => { setLoading(true); setError(null); window.location.reload() }}>{Icons.activity} Retry</button>
          </div>
        </div>
      </main>
    )
  }

  const data = summary!
  const attentionCount = data.attention_items.length

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

        {/* Metrics */}
        <section className="metrics-grid">
          <article className="metric-card">
            <div className="metric-top"><span>Active models</span><span className="metric-icon">{Icons.users}</span></div>
            <strong>{data.active_models}</strong>
            <div className="metric-foot">
              <span className="positive">{data.training_models > 0 ? `${data.training_models} training` : 'All active'}</span>
              <small>{data.total_models} total identities</small>
            </div>
          </article>
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
          <article className="metric-card">
            <div className="metric-top"><span>Revenue this month</span><span className="metric-icon">{Icons.dollar}</span></div>
            <strong>{formatCurrency(data.revenue)}</strong>
            <div className="metric-foot">
              <span className="positive">{data.active_models > 0 ? 'From analytics' : 'No data yet'}</span>
              <small>Across all personas</small>
            </div>
          </article>
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
        </section>

        {/* Dashboard grid */}
        <section className="dashboard-grid">
          <article className="panel revenue-panel">
            <div className="panel-head">
              <div>
                <span className="kicker">Revenue trajectory</span>
                <h2>{formatCurrency(data.revenue)}</h2>
                <p><b style={{ color: 'var(--green)' }}>{data.active_models > 0 ? `From ${data.active_models} active model${data.active_models > 1 ? 's' : ''}` : 'No revenue data'}</b></p>
              </div>
              <div className="scenario-tabs">
                <button onClick={() => {}} >Conservative</button><button className="selected">Base</button><button onClick={() => {}} >Aggressive</button>
              </div>
            </div>
            <Sparkline revenue={data.revenue} />
            <div className="chart-labels">
              <span>SEP</span><span>NOV</span><span>JAN</span><span>MAR</span><span>MAY</span><span>JUL</span><span>AUG</span>
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
                    <div key={shoot.id} className="job-row">
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
              <button key={item.id} className="attention-item">
                <span className="attention-icon amber">{Icons.shield}</span>
                <p><b>{item.name}</b><small>{item.type.replace(/_/g, ' ')}</small></p>
                <span><b>Review</b>{Icons.arrowRight}</span>
              </button>
            ))}
          </article>

          <article className="panel capacity-panel">
            <div className="panel-title">
              <div><span className="kicker">Infrastructure</span><h3>System health</h3></div>
              {Icons.cpu}
            </div>
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
