'use client'

import { useEffect, useMemo, useState } from 'react'
import { getDashboardSummary, listPersonas } from '@/lib/api'
import type { DashboardSummary, PersonaDetail } from '@/lib/types'

const offers = [
  { name: 'Discovery', price: 0, detail: 'Safe-for-work teaser content and profile discovery', color: 'var(--blue)' },
  { name: 'Core subscription', price: 14.99, detail: 'Consistent weekly drops, behind-the-scenes, and member updates', color: 'var(--green)' },
  { name: 'Premium tier', price: 29.99, detail: 'Higher-frequency drops, polls, and priority requests', color: 'var(--amber)' },
  { name: 'Custom work', price: 75, detail: 'Human-reviewed custom briefs with explicit rights and delivery terms', color: '#e695ff' },
]

export default function MonetizationPage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [personas, setPersonas] = useState<PersonaDetail[]>([])
  const [members, setMembers] = useState(100)
  const [conversion, setConversion] = useState(3)
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([getDashboardSummary(), listPersonas()])
      .then(([dashboard, models]) => { setSummary(dashboard); setPersonas(models) })
      .catch(err => setError(err instanceof Error ? err.message : 'Could not load monetization data'))
  }, [])

  const projection = useMemo(() => members * (conversion / 100) * 14.99, [members, conversion])
  const active = personas.filter(p => p.status === 'active').length
  const readiness = [
    { label: 'Synthetic identity declared', done: active > 0, note: 'Keep a visible AI/synthetic disclosure on every supported profile.' },
    { label: 'Rights and consent records', done: Boolean(summary?.health), note: 'Use only owned or licensed references, voices, and likenesses.' },
    { label: 'Human approval workflow', done: true, note: 'Review every custom request, scheduled post, and account action.' },
    { label: 'Provider health', done: summary?.health.online === summary?.health.total, note: 'Real generation is blocked when a required provider is unavailable.' },
  ]

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
          <article className="metric-card"><div className="metric-top"><span>Recorded revenue</span><span className="metric-icon">$</span></div><strong>${(summary?.revenue || 0).toLocaleString()}</strong><div className="metric-foot"><span className="positive">From verified analytics</span><small>Not a forecast</small></div></article>
          <article className="metric-card"><div className="metric-top"><span>Active personas</span><span className="metric-icon">◎</span></div><strong>{active}</strong><div className="metric-foot"><span className={active ? 'positive' : 'warning'}>{active ? 'Ready for offers' : 'Build an identity first'}</span><small>{personas.length} total</small></div></article>
          <article className="metric-card"><div className="metric-top"><span>Projected MRR</span><span className="metric-icon">↗</span></div><strong>${projection.toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong><div className="metric-foot"><span className="warning">Planning scenario</span><small>{members} visitors · {conversion}% convert</small></div></article>
          <article className="metric-card"><div className="metric-top"><span>Provider readiness</span><span className="metric-icon">◉</span></div><strong>{summary ? `${summary.health.online}/${summary.health.total}` : '—'}</strong><div className="metric-foot"><span className={summary?.health.online === summary?.health.total ? 'positive' : 'warning'}>{summary?.health.online === summary?.health.total ? 'Operational' : 'Needs attention'}</span><small>Required services</small></div></article>
        </section>

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
          </article>
        </section>

        <section className="panel" style={{ marginBottom: 18 }}>
          <div className="panel-title"><div><span className="kicker">Launch gates</span><h3>Publish only when ready</h3></div><a href="/settings" className="text-button">Open controls →</a></div>
          <div className="persona-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>{readiness.map(item => <div key={item.label} style={{ padding: 14, background: 'var(--bg-card)', borderRadius: 'var(--radius-sm)', border: `1px solid ${item.done ? 'var(--border)' : 'rgba(240,173,78,.5)'}` }}><div style={{ color: item.done ? 'var(--green)' : 'var(--amber)', fontWeight: 700, marginBottom: 8 }}>{item.done ? '✓ Ready' : '○ Review'}</div><b style={{ fontSize: 13 }}>{item.label}</b><p className="muted-sm" style={{ marginTop: 6 }}>{item.note}</p></div>)}</div>
        </section>

        <section className="panel" style={{ borderColor: 'rgba(91,156,246,.35)', background: 'linear-gradient(135deg, rgba(91,156,246,.08), var(--bg-panel))' }}>
          <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}><span style={{ fontSize: 22 }}>ⓘ</span><div><h3 style={{ marginBottom: 6 }}>Platform-safe operating model</h3><p className="muted-sm" style={{ maxWidth: 760 }}>Only use platforms and account actions that permit synthetic creators and provide an official integration or an explicit human-in-the-loop process. This workspace does not scrape creator sites, impersonate real people, bypass platform controls, or auto-publish to platforms without an approved API path. For any platform without an official API, keep publishing and account verification manual.</p></div></div>
        </section>
      </div>
    </main>
  )
}
