'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import {
  getPersona, listIdentities, listShoots, listPacks, listWorkflows,
  getAnalytics, getForecasts, getSchedule, getGallery,
  createShoot, createPack,
  generateAnalytics, generateForecast, generateSchedule,
  toggleAutopilot,
} from '@/lib/api'
import { GRADIENTS } from '@/lib/constants'
import { Icons } from '@/lib/icons'
import { StatusBadge } from '@/components/ui/StatusBadge'

type Tab = 'overview' | 'identity' | 'shoots' | 'content' | 'analytics' | 'revenue' | 'schedule' | 'workflows' | 'gallery'

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'gallery', label: 'Gallery' },
  { key: 'identity', label: 'Identity' },
  { key: 'shoots', label: 'Shoots' },
  { key: 'content', label: 'Content' },
  { key: 'analytics', label: 'Analytics' },
  { key: 'revenue', label: 'Revenue' },
  { key: 'schedule', label: 'Calendar' },
  { key: 'workflows', label: 'Workflows' },
]

/* ── Lazy tab wrapper ─────────────────────────────────── */
function useTabData<T>(id: string, fetcher: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let cancelled = false
    fetcher().then(d => { if (!cancelled) { setData(d); setLoading(false) } })
      .catch(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, deps) // eslint-disable-line react-hooks/exhaustive-deps
  return { data, loading, setData }
}

/* ── Tab panels ───────────────────────────────────────── */

function OverviewTab({ persona }: { persona: any }) {
  const items = [
    { label: 'Status', value: persona.status },
    { label: 'Identity', value: persona.identity_score ? `${(persona.identity_score * 100).toFixed(1)}%` : 'N/A' },
    { label: 'Content Packs', value: persona.packs_count },
    { label: 'Voice', value: persona.identity_status === 'ready' ? 'READY' : 'PENDING' },
  ]
  return (
    <div className="grid-4">
      {items.map(item => (
        <div key={item.label} className="panel panel-sm">
          <div className="field-label">{item.label}</div>
          <div className="stat-value">{item.value}</div>
        </div>
      ))}
    </div>
  )
}

function GalleryTab({ id }: { id: string }) {
  const { data: gallery, loading } = useTabData(id, () => getGallery(id), [id])
  const [selected, setSelected] = useState<string | null>(null)

  if (loading) return <p className="muted-md">Loading gallery…</p>
  if (!gallery?.images?.length) return <p className="muted-md">No images yet.</p>

  return (
    <div>
      <h3 className="section-title">Gallery ({gallery.count} images)</h3>
      {selected && (
        <div className="gallery-lightbox" onClick={() => setSelected(null)}>
          <img src={selected} alt="Full size" style={{ maxWidth: '90vw', maxHeight: '85vh', borderRadius: 8 }} />
        </div>
      )}
      <div className="gallery-grid">
        {gallery.images.map((img: any, i: number) => (
          <button
            key={i}
            className="gallery-thumb"
            onClick={() => setSelected(img.url)}
          >
            <img src={img.url} alt={img.label} />
            <span className="gallery-label">{img.label}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

function IdentityTab({ id }: { id: string }) {
  const { data: identities, loading } = useTabData(id, () => listIdentities(id), [id])
  if (loading) return <p className="muted-md">Loading…</p>
  return (
    <div>
      <h3 className="section-title">Identities ({identities?.length || 0})</h3>
      {identities?.map((i: any) => (
        <div key={i.id} className="panel list-row">
          <span className="field-value">{i.name}</span>
          <StatusBadge status={i.status} />
        </div>
      ))}
      {identities?.length === 0 && <p className="muted-md">No identities yet.</p>}
    </div>
  )
}

function ShootsTab({ id }: { id: string }) {
  const { data: shoots, loading, setData } = useTabData(id, () => listShoots(id), [id])
  const [busy, setBusy] = useState(false)
  const handleCreate = async () => {
    setBusy(true)
    try {
      const shoot = await createShoot(id, { name: 'New Shoot', theme: 'lifestyle', image_count: 8 })
      setData(prev => prev ? [shoot, ...prev] : [shoot])
    } finally { setBusy(false) }
  }
  if (loading) return <p className="muted-md">Loading…</p>
  return (
    <div>
      <div className="section-header">
        <h3 className="section-title">Shoots ({shoots?.length || 0})</h3>
        <button onClick={handleCreate} disabled={busy} className="primary-button btn-sm">
          {busy ? 'Creating…' : '+ Create Shoot'}
        </button>
      </div>
      {shoots?.map((s: any) => (
        <div key={s.id} className="panel list-row">
          <span className="field-value">{s.name || s.theme}</span>
          <StatusBadge status={s.status} />
        </div>
      ))}
    </div>
  )
}

function ContentTab({ id, personaName }: { id: string; personaName: string }) {
  const { data: packs, loading, setData } = useTabData(id, () => listPacks(id), [id])
  const [busy, setBusy] = useState(false)
  const handleCreate = async () => {
    setBusy(true)
    try {
      const pack = await createPack(id, { name: `${personaName} Pack`, platform: 'instagram' })
      setData(prev => prev ? [pack, ...prev] : [pack])
    } finally { setBusy(false) }
  }
  if (loading) return <p className="muted-md">Loading…</p>
  return (
    <div>
      <div className="section-header">
        <h3 className="section-title">Content Packs ({packs?.length || 0})</h3>
        <button onClick={handleCreate} disabled={busy} className="primary-button btn-sm">
          {busy ? 'Generating…' : '+ Generate Pack'}
        </button>
      </div>
      {packs?.map((p: any) => (
        <div key={p.id} className="panel list-row">
          <div>
            <div className="field-value">{p.name}</div>
            <div className="muted-sm">Platform: {p.platform}</div>
          </div>
          <StatusBadge status={p.status} />
        </div>
      ))}
    </div>
  )
}

function AnalyticsTab({ id }: { id: string }) {
  const { data: analytics, loading, setData } = useTabData(id, () => getAnalytics(id), [id])
  const [busy, setBusy] = useState(false)
  const handleGenerate = async () => {
    setBusy(true)
    try { await generateAnalytics(id); const a = await getAnalytics(id); setData(a) }
    finally { setBusy(false) }
  }
  if (loading) return <p className="muted-md">Loading…</p>
  return (
    <div>
      <div className="section-header">
        <h3 className="section-title">Analytics ({analytics?.length || 0} days)</h3>
        <button onClick={handleGenerate} disabled={busy} className="primary-button btn-sm">
          {busy ? 'Generating…' : 'Generate'}
        </button>
      </div>
      {analytics && analytics.length > 0 && (
        <div className="grid-4" style={{ marginBottom: 16 }}>
          {[
            { label: 'Followers', value: analytics[0]?.followers?.toLocaleString() || '—' },
            { label: 'Engagement', value: analytics[0] ? `${(analytics[0].engagement_rate * 100).toFixed(1)}%` : '—' },
            { label: 'Revenue', value: `$${analytics[0]?.revenue?.toLocaleString() || 0}` },
            { label: 'Costs', value: `$${analytics[0]?.costs?.toLocaleString() || 0}` },
          ].map(item => (
            <div key={item.label} className="panel">
              <div className="muted-sm">{item.label}</div>
              <div className="stat-value">{item.value}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function RevenueTab({ id }: { id: string }) {
  const { data: forecasts, loading, setData } = useTabData(id, () => getForecasts(id), [id])
  const [busy, setBusy] = useState(false)
  const handleGenerate = async () => {
    setBusy(true)
    try { await generateForecast(id); const f = await getForecasts(id); setData(f) }
    finally { setBusy(false) }
  }
  if (loading) return <p className="muted-md">Loading…</p>
  return (
    <div>
      <div className="section-header">
        <h3 className="section-title">24-Month Forecast</h3>
        <button onClick={handleGenerate} disabled={busy} className="primary-button btn-sm">
          {busy ? 'Generating…' : 'Generate'}
        </button>
      </div>
      {forecasts && forecasts.length > 0 && forecasts[0]?.scenarios?.map((s: any) => (
        <div key={s.scenario} className="panel" style={{ padding: 12, marginBottom: 8 }}>
          <div className="forecast-row">
            <span className="forecast-scenario">{s.scenario}</span>
            <span className="forecast-total">
              Total: ${s.monthly_revenue?.reduce((a: number, b: number) => a + b, 0).toLocaleString() || 0}
            </span>
          </div>
          <div className="progress-bar">
            <div className="progress-bar-fill" style={{ width: `${Math.min(100, (s.monthly_revenue?.[23] || 0) / 500)}%` }} />
          </div>
        </div>
      ))}
      {forecasts?.length === 0 && <p className="muted-md">Generate a forecast to see projections.</p>}
    </div>
  )
}

function ScheduleTab({ id }: { id: string }) {
  const { data: schedule, loading, setData } = useTabData(id, () => getSchedule(id), [id])
  const [busy, setBusy] = useState(false)
  const handleGenerate = async () => {
    setBusy(true)
    try { await generateSchedule(id); const sc = await getSchedule(id); setData(sc) }
    finally { setBusy(false) }
  }
  if (loading) return <p className="muted-md">Loading…</p>
  return (
    <div>
      <div className="section-header">
        <h3 className="section-title">Calendar ({schedule?.length || 0} posts)</h3>
        <button onClick={handleGenerate} disabled={busy} className="primary-button btn-sm">
          {busy ? 'Scheduling…' : 'Auto-Schedule'}
        </button>
      </div>
      {schedule?.map((s: any) => (
        <div key={s.id} className="panel list-row-compact">
          <span>{s.platform}</span>
          <span className="muted-sm">{new Date(s.scheduled_at).toLocaleDateString()}</span>
          <StatusBadge status={s.status} />
        </div>
      ))}
    </div>
  )
}

function WorkflowsTab({ id }: { id: string }) {
  const { data: workflows, loading } = useTabData(id, () => listWorkflows({ persona_id: id }), [id])
  if (loading) return <p className="muted-md">Loading…</p>
  return (
    <div>
      <h3 className="section-title">Workflows ({workflows?.length || 0})</h3>
      {workflows?.map((w: any) => (
        <div key={w.id} className="panel list-row">
          <div>
            <div className="field-value">{w.name}</div>
            <div className="muted-sm">{w.workflow_type}</div>
          </div>
          <StatusBadge status={w.status} />
        </div>
      ))}
      {workflows?.length === 0 && <p className="muted-md">No workflows yet.</p>}
    </div>
  )
}

/* ── Main Persona Page ────────────────────────────────── */
export default function PersonaPage() {
  const params = useParams()
  const id = params.id as string
  const [tab, setTab] = useState<Tab>('overview')
  const [persona, setPersona] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!id) return
    getPersona(id).then(p => { setPersona(p); setLoading(false) })
      .catch(() => setLoading(false))
  }, [id])

  if (loading) return <main className="workspace"><div className="content muted-md">Loading…</div></main>
  if (!persona) return <main className="workspace"><div className="content muted-md">Persona not found</div></main>

  const tabContent = {
    overview: <OverviewTab persona={persona} />,
    gallery: <GalleryTab id={id} />,
    identity: <IdentityTab id={id} />,
    shoots: <ShootsTab id={id} />,
    content: <ContentTab id={id} personaName={persona.name} />,
    analytics: <AnalyticsTab id={id} />,
    revenue: <RevenueTab id={id} />,
    schedule: <ScheduleTab id={id} />,
    workflows: <WorkflowsTab id={id} />,
  }

  return (
    <main className="workspace">
      <header className="topbar">
        <div className="crumb">
          <a href="/"><span>Persona Studio</span></a><b>/</b>
          <span>{persona.name}</span>
        </div>
      </header>
      <div className="content">
        <div className="persona-header">
          <div className="persona-avatar" style={{ background: GRADIENTS[0] }}>
            {persona.avatar_url ? (
              <img src={persona.avatar_url} alt={persona.name} style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: 'inherit' }} />
            ) : (
              persona.name[0]
            )}
          </div>
          <div>
            <h1 style={{ fontSize: 22, fontWeight: 600 }}>{persona.name}</h1>
            <p className="persona-meta">Age {persona.age} · {persona.brand} · {persona.status}</p>
          </div>
          <div className="persona-actions">
            <button onClick={() => toggleAutopilot(id, 'on')} className="secondary-button">Autopilot ON</button>
          </div>
        </div>

        <div className="tab-bar">
          {TABS.map(t => (
            <button key={t.key} onClick={() => setTab(t.key)} className={`tab-btn${tab === t.key ? ' active' : ''}`}>
              {t.label}
            </button>
          ))}
        </div>

        <div className="panel">
          {tabContent[tab]}
        </div>
      </div>
    </main>
  )
}
