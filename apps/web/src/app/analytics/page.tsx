'use client'

import { useEffect, useState } from 'react'
import { listPersonas, getAnalytics, syncInstagramAnalytics, submitManualAnalytics } from '@/lib/api'
import { Icons } from '@/lib/icons'

interface PersonaAnalytics {
  id: string
  name: string
  followers: number
  engagement: number
  revenue: number
  costs: number
  views: number
  source: string  // 'instagram', 'demo', 'manual', etc.
}

export default function AnalyticsPage() {
  const [data, setData] = useState<PersonaAnalytics[]>([])
  const [loading, setLoading] = useState(true)
  const [syncingId, setSyncingId] = useState<string | null>(null)
  const [showManualForm, setShowManualForm] = useState<string | null>(null)
  const [manualForm, setManualForm] = useState({ followers: '', engagement_rate: '', revenue: '' })

  const loadData = async () => {
    try {
      const personas = await listPersonas() as any[]
      const results: PersonaAnalytics[] = []
      for (const p of personas) {
        try {
          const analytics = await getAnalytics(p.id) as any[]
          if (analytics.length > 0) {
            const latest = analytics[0]
            // Detect source from platform field
            const source = latest.platform === 'instagram' ? 'instagram'
              : latest.platform === 'manual' ? 'manual'
              : 'demo'
            results.push({
              id: p.id,
              name: p.name,
              followers: latest.followers || 0,
              engagement: latest.engagement_rate || 0,
              revenue: latest.revenue || 0,
              costs: latest.costs || 0,
              views: latest.views || 0,
              source,
            })
          }
        } catch {}
      }
      setData(results.sort((a, b) => b.revenue - a.revenue))
    } catch {}
    setLoading(false)
  }

  useEffect(() => { loadData() }, [])

  const handleSyncInstagram = async (personaId: string) => {
    setSyncingId(personaId)
    try {
      await syncInstagramAnalytics(personaId)
      await loadData()  // Reload with real data
    } catch (e: any) {
      alert(`Sync failed: ${e.message}`)
    }
    setSyncingId(null)
  }

  const handleManualEntry = async (personaId: string) => {
    try {
      await submitManualAnalytics(personaId, {
        followers: parseInt(manualForm.followers) || 0,
        engagement_rate: parseFloat(manualForm.engagement_rate) || 0,
        revenue: parseFloat(manualForm.revenue) || 0,
      })
      setShowManualForm(null)
      setManualForm({ followers: '', engagement_rate: '', revenue: '' })
      await loadData()
    } catch (e: any) {
      alert(`Save failed: ${e.message}`)
    }
  }

  const totalFollowers = data.reduce((s, d) => s + d.followers, 0)
  const totalRevenue = data.reduce((s, d) => s + d.revenue, 0)
  const totalViews = data.reduce((s, d) => s + d.views, 0)
  const avgEngagement = data.length > 0 ? data.reduce((s, d) => s + d.engagement, 0) / data.length : 0

  const hasRealData = data.some(d => d.source === 'instagram' || d.source === 'manual')
  const hasDemoData = data.some(d => d.source === 'demo')

  return (
    <main className="workspace">
      <header className="topbar">
        <button className="mobile-menu" aria-label="Open menu">{Icons.menu}</button>
        <div className="crumb">
          <a href="/"><span>Persona Studio</span></a><b>/</b><strong>Analytics</strong>
        </div>
      </header>
      <div className="content">
        <section className="page-heading">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <h1>Analytics</h1>
            {hasDemoData && (
              <span style={{
                fontSize: 11, fontWeight: 600, padding: '3px 8px',
                borderRadius: 4, background: 'rgba(251, 191, 36, 0.15)',
                color: '#fbbf24', textTransform: 'uppercase', letterSpacing: '.04em'
              }}>
                Demo Data
              </span>
            )}
            {hasRealData && (
              <span style={{
                fontSize: 11, fontWeight: 600, padding: '3px 8px',
                borderRadius: 4, background: 'rgba(34, 197, 94, 0.15)',
                color: '#22c55e', textTransform: 'uppercase', letterSpacing: '.04em'
              }}>
                Real Data
              </span>
            )}
          </div>
          <p>Performance metrics across all personas.</p>
        </section>

        {loading ? (
          <p className="muted-md">Loading analytics…</p>
        ) : data.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '48px 0', color: 'var(--muted)' }}>
            <p style={{ fontSize: 15, marginBottom: 8 }}>No analytics data</p>
            <p style={{ fontSize: 13 }}>Generate analytics from a persona's Analytics tab, or sync from Instagram.</p>
          </div>
        ) : (
          <>
            <div className="metrics-grid" style={{ marginBottom: 24 }}>
              <article className="metric-card">
                <div className="metric-top"><span>Total Followers</span><span className="metric-icon">{Icons.users}</span></div>
                <strong>{totalFollowers.toLocaleString()}</strong>
                <div className="metric-foot"><span className="positive">Across {data.length} personas</span></div>
              </article>
              <article className="metric-card">
                <div className="metric-top"><span>Total Revenue</span><span className="metric-icon">{Icons.dollar}</span></div>
                <strong>R {totalRevenue.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}</strong>
                <div className="metric-foot"><span className="positive">This month</span></div>
              </article>
              <article className="metric-card">
                <div className="metric-top"><span>Total Views</span><span className="metric-icon">{Icons.trending}</span></div>
                <strong>{totalViews.toLocaleString()}</strong>
                <div className="metric-foot"><span className="positive">Across all content</span></div>
              </article>
              <article className="metric-card">
                <div className="metric-top"><span>Avg Engagement</span><span className="metric-icon">{Icons.activity}</span></div>
                <strong>{(avgEngagement * 100).toFixed(1)}%</strong>
                <div className="metric-foot"><span className="positive">Engagement rate</span></div>
              </article>
            </div>

            <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Per-Persona Breakdown</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {data.map(d => (
                <div key={d.name} className="panel" style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
                  <span style={{ fontWeight: 600, fontSize: 13, minWidth: 100 }}>{d.name}</span>
                  <span style={{
                    fontSize: 10, fontWeight: 600, padding: '2px 6px', borderRadius: 3,
                    background: d.source === 'instagram' ? 'rgba(34, 197, 94, 0.15)' :
                      d.source === 'manual' ? 'rgba(59, 130, 246, 0.15)' :
                      'rgba(251, 191, 36, 0.15)',
                    color: d.source === 'instagram' ? '#22c55e' :
                      d.source === 'manual' ? '#3b82f6' : '#fbbf24',
                    textTransform: 'uppercase', letterSpacing: '.03em'
                  }}>
                    {d.source}
                  </span>
                  <span style={{ fontSize: 12, color: 'var(--muted)', flex: 1 }}>
                    {d.followers.toLocaleString()} followers · {(d.engagement * 100).toFixed(1)}% engagement
                  </span>
                  <span style={{ fontSize: 13, fontWeight: 600 }}>R {d.revenue.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button
                      className="btn-sm"
                      disabled={syncingId === d.id}
                      onClick={() => handleSyncInstagram(d.id)}
                      title="Sync real Instagram data"
                    >
                      {syncingId === d.id ? 'Syncing…' : '📱 Sync IG'}
                    </button>
                    <button
                      className="btn-sm"
                      onClick={() => setShowManualForm(showManualForm === d.id ? null : d.id)}
                      title="Enter data manually"
                    >
                      ✏️ Manual
                    </button>
                  </div>

                  {showManualForm === d.id && (
                    <div style={{
                      width: '100%', padding: '12px', marginTop: 8,
                      background: 'var(--bg-secondary)', borderRadius: 8,
                      display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap'
                    }}>
                      <div>
                        <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Followers</label>
                        <input
                          type="number"
                          value={manualForm.followers}
                          onChange={e => setManualForm({ ...manualForm, followers: e.target.value })}
                          style={{ width: 120, padding: '6px 10px', fontSize: 13, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)' }}
                          placeholder="12500"
                        />
                      </div>
                      <div>
                        <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Engagement %</label>
                        <input
                          type="number"
                          step="0.1"
                          value={manualForm.engagement_rate}
                          onChange={e => setManualForm({ ...manualForm, engagement_rate: e.target.value })}
                          style={{ width: 100, padding: '6px 10px', fontSize: 13, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)' }}
                          placeholder="3.5"
                        />
                      </div>
                      <div>
                        <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Revenue (R)</label>
                        <input
                          type="number"
                          value={manualForm.revenue}
                          onChange={e => setManualForm({ ...manualForm, revenue: e.target.value })}
                          style={{ width: 120, padding: '6px 10px', fontSize: 13, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)' }}
                          placeholder="4200"
                        />
                      </div>
                      <button
                        className="btn-sm"
                        onClick={() => handleManualEntry(d.id)}
                        style={{ background: 'var(--accent)', color: '#000', fontWeight: 600 }}
                      >
                        Save
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>

            <div style={{
              marginTop: 24, padding: 16, borderRadius: 8,
              background: 'var(--bg-secondary)', border: '1px solid var(--border)'
            }}>
              <h4 style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>📊 Data Sources</h4>
              <ul style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.8 }}>
                <li><strong style={{ color: '#22c55e' }}>Instagram</strong> — Real data synced via Instagram Graph API. Requires INSTAGRAM_ACCESS_TOKEN in .env</li>
                <li><strong style={{ color: '#3b82f6' }}>Manual</strong> — Data you entered by hand</li>
                <li><strong style={{ color: '#fbbf24' }}>Demo</strong> — Randomly generated placeholder data. Replace with real data above.</li>
              </ul>
            </div>
          </>
        )}
      </div>
    </main>
  )
}
