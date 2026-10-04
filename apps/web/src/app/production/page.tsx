'use client'

import { useEffect, useMemo, useState } from 'react'
import {
  autoProduce,
  getDashboardSummary,
  listJobs,
  listPersonas,
  listVideos,
  managerDashboard,
} from '@/lib/api'
import type { DashboardSummary, PersonaDetail, ShootDetail } from '@/lib/types'
import { Icons } from '@/lib/icons'
import { Toast, type ToastState } from '@/components/ui/Toast'

interface VideoItem {
  id: string
  prompt: string
  video_url: string
  video_key: string
  duration: number
  width: number
  height: number
  generation_time_ms: number
  created_at: string
}

interface JobItem {
  id: string
  persona_id: string
  job_type: string
  status: string
  progress: number
  message: string | null
  created_at: string
  updated_at: string
}

interface ManagerPipelineShot {
  shot_id: string
  number: number
  type: string
  status: string
  qa: string | null
  asset_key: string | null
  error: string | null
  retries: number
}

interface ManagerPipeline {
  shoot_id: string
  stages: Record<string, number>
  shots: ManagerPipelineShot[]
}

interface ManagerDash {
  status: string
  paused: boolean
  current_objective: string | null
  identity_lock: { status: string; generation_allowed: boolean }
  inventory: Record<string, number>
  pipeline: ManagerPipeline[]
  active_jobs: any[]
  pending_jobs: any[]
  failed_jobs: any[]
  blockers: { error: string | null; kind: string | null; task: string }[]
}

const THEMES = [
  { key: 'lifestyle', label: 'Lifestyle', icon: '🏠' },
  { key: 'fashion', label: 'Fashion', icon: '👗' },
  { key: 'swimwear', label: 'Swimwear', icon: '👙' },
  { key: 'fitness', label: 'Fitness', icon: '💪' },
  { key: 'editorial', label: 'Editorial', icon: '📸' },
  { key: 'artistic', label: 'Artistic', icon: '🎨' },
  { key: 'travel', label: 'Travel', icon: '✈️' },
  { key: 'loungewear', label: 'Loungewear', icon: '🩱' },
  { key: 'grwm', label: 'GRWM', icon: '💄' },
  { key: 'casual', label: 'Casual', icon: '🛋️' },
  { key: 'nude', label: 'Artistic Nude', icon: '🖼️' },
]

const PRESETS = [
  { key: 'quick', label: 'Quick', images: 5, videos: true, description: '5 photos + video / theme' },
  { key: 'standard', label: 'Standard', images: 12, videos: true, description: '12 photos + video / theme' },
  { key: 'premium', label: 'Premium', images: 20, videos: true, description: '20 photos + video / theme' },
  { key: 'photos', label: 'Photos only', images: 15, videos: false, description: '15 photos / theme' },
] as const

const TIERS = ['PUBLIC', 'SUBSCRIBER', 'PREMIUM'] as const

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="panel" style={{ padding: 14, minHeight: 86 }}>
      <div className="muted-sm">{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, marginTop: 4 }}>{value}</div>
      {sub && <div className="muted-sm" style={{ marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

function PipelineStage({
  label,
  value,
  active,
}: {
  label: string
  value: string
  active?: boolean
}) {
  return (
    <div style={{
      flex: 1,
      minWidth: 110,
      padding: '12px 14px',
      borderRadius: 10,
      border: `1px solid ${active ? 'var(--green)' : 'var(--border)'}`,
      background: active ? 'var(--green-dim)' : 'var(--bg-card)',
    }}>
      <div className="muted-sm" style={{ textTransform: 'uppercase', letterSpacing: '.05em' }}>{label}</div>
      <div style={{ fontWeight: 700, marginTop: 3, color: active ? 'var(--green)' : 'var(--text)' }}>{value}</div>
    </div>
  )
}

export default function ProductionPage() {
  const [shoots, setShoots] = useState<ShootDetail[]>([])
  const [videos, setVideos] = useState<VideoItem[]>([])
  const [jobs, setJobs] = useState<JobItem[]>([])
  const [personas, setPersonas] = useState<PersonaDetail[]>([])
  const [selectedPersonaId, setSelectedPersonaId] = useState('')
  const [manager, setManager] = useState<ManagerDash | null>(null)
  const [loading, setLoading] = useState(true)
  const [autoProducing, setAutoProducing] = useState(false)
  const [selectedThemes, setSelectedThemes] = useState<string[]>(['lifestyle', 'fashion', 'swimwear'])
  const [presetKey, setPresetKey] = useState<(typeof PRESETS)[number]['key']>('standard')
  const [tier, setTier] = useState<(typeof TIERS)[number]>('SUBSCRIBER')
  const [toast, setToast] = useState<ToastState>(null)
  const [statusFilter, setStatusFilter] = useState<'all' | 'completed' | 'failed' | 'draft'>('all')
  const [lightbox, setLightbox] = useState<string | null>(null)
  const [expandedShoot, setExpandedShoot] = useState<string | null>(null)

  const refreshCore = async () => {
    const [summary, personaData, runningJobs] = await Promise.all([
      getDashboardSummary(),
      listPersonas(),
      listJobs({ status: 'running' }).catch(() => []),
    ])
    setShoots((summary as DashboardSummary).shoots || [])
    const nextPersonas = personaData as PersonaDetail[]
    setPersonas(nextPersonas)
    setJobs(runningJobs as JobItem[])
    setSelectedPersonaId(prev => prev || nextPersonas.find(p => p.status === 'active')?.id || nextPersonas[0]?.id || '')
  }

  useEffect(() => {
    refreshCore().finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    const interval = setInterval(() => {
      refreshCore().catch(() => {})
    }, 5000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    if (!selectedPersonaId) return
    Promise.all([
      managerDashboard(selectedPersonaId).then((data: ManagerDash) => setManager(data)).catch(() => setManager(null)),
      listVideos(selectedPersonaId).then((data: VideoItem[]) => setVideos(data || [])).catch(() => setVideos([])),
    ])
  }, [selectedPersonaId, jobs])

  const selectedPersona = personas.find(p => p.id === selectedPersonaId) || null
  const preset = PRESETS.find(p => p.key === presetKey) || PRESETS[1]
  const personaShoots = selectedPersona
    ? shoots.filter(s => s.persona_name === selectedPersona.name)
    : shoots
  const filteredShoots = statusFilter === 'all'
    ? personaShoots
    : personaShoots.filter(s => (s.status || '').toLowerCase() === statusFilter)

  const runningForPersona = jobs.filter(j => !selectedPersonaId || j.persona_id === selectedPersonaId)
  const activePipeline = manager?.pipeline?.find(p => {
    const st = p.stages || {}
    const total = Number(st.images_total || 0)
    const approved = Number(st.images_approved || 0)
    return total > 0 && approved < total
  }) || manager?.pipeline?.[0] || null

  const pipelineSummary = useMemo(() => {
    if (!activePipeline) return null
    const st = activePipeline.stages || {}
    const total = Number(st.images_total || activePipeline.shots.length || 0)
    const approved = Number(st.images_approved || 0)
    const failed = Number(st.images_failed || 0)
    const generating = activePipeline.shots.filter(s => ['GENERATING', 'PENDING'].includes(s.status)).length
    const qa = activePipeline.shots.filter(s => s.qa && s.status !== 'APPROVED').length
    const progress = total > 0 ? Math.round((approved / total) * 100) : 0
    return { total, approved, failed, generating, qa, progress }
  }, [activePipeline])

  const handleProduce = async () => {
    if (!selectedPersona) return
    if (selectedThemes.length === 0) {
      setToast({ msg: 'Select at least one shoot theme.', type: 'error' })
      return
    }
    setAutoProducing(true)
    try {
      await autoProduce(selectedPersona.id, {
        shoot_count: selectedThemes.length,
        images_per_shoot: preset.images,
        generate_videos: preset.videos,
        adult_content: false,
        themes: selectedThemes.join(','),
      })
      setToast({
        msg: `Production started for ${selectedPersona.name}: ${selectedThemes.length} themes × ${preset.images} images${preset.videos ? ' + video' : ''}. Target tier: ${tier}.`,
        type: 'success',
      })
      setTimeout(() => refreshCore().catch(() => {}), 1500)
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Unknown error'
      setToast({
        msg: msg.includes('409') ? `${selectedPersona.name} is already producing.` : `Production failed: ${msg}`,
        type: 'error',
      })
    } finally {
      setAutoProducing(false)
      setTimeout(() => setToast(null), 5000)
    }
  }

  return (
    <main className="workspace">
      <header className="topbar">
        <button className="mobile-menu" aria-label="Open menu">{Icons.menu}</button>
        <div className="crumb">
          <a href="/"><span>Persona Studio</span></a><b>/</b><strong>Production</strong>
        </div>
        <div className="top-actions">
          <span className="environment"><i />LIVE WORKSPACE</span>
        </div>
      </header>

      <div className="content">
        <section className="page-heading" style={{ marginBottom: 20 }}>
          <div>
            <div className="eyebrow">Content operations</div>
            <h1>Production Command Centre</h1>
            <p>Plan shoots, run generation, inspect QA, and move finished media into inventory.</p>
          </div>
        </section>

        <section className="panel" style={{ marginBottom: 18, padding: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1.3fr 1fr 1fr auto', gap: 10, alignItems: 'end' }}>
            <label>
              <div className="muted-sm" style={{ marginBottom: 6 }}>Persona</div>
              <select
                value={selectedPersonaId}
                onChange={e => setSelectedPersonaId(e.target.value)}
                style={{ width: '100%', padding: '10px 12px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--bg-card)' }}
              >
                {personas.map(p => <option key={p.id} value={p.id}>{p.name} · {p.status}</option>)}
              </select>
            </label>

            <div>
              <div className="muted-sm" style={{ marginBottom: 6 }}>Inventory target</div>
              <div style={{ display: 'flex', gap: 6 }}>
                {TIERS.map(t => (
                  <button
                    key={t}
                    onClick={() => setTier(t)}
                    style={{
                      flex: 1,
                      padding: '9px 8px',
                      borderRadius: 8,
                      border: `1px solid ${tier === t ? 'var(--green)' : 'var(--border)'}`,
                      background: tier === t ? 'var(--green-dim)' : 'var(--bg-card)',
                      color: tier === t ? 'var(--green)' : 'var(--text-secondary)',
                      fontSize: 11,
                      fontWeight: 700,
                    }}
                  >{t}</button>
                ))}
              </div>
            </div>

            <label>
              <div className="muted-sm" style={{ marginBottom: 6 }}>Production preset</div>
              <select
                value={presetKey}
                onChange={e => setPresetKey(e.target.value as (typeof PRESETS)[number]['key'])}
                style={{ width: '100%', padding: '10px 12px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--bg-card)' }}
              >
                {PRESETS.map(p => <option key={p.key} value={p.key}>{p.label} — {p.description}</option>)}
              </select>
            </label>

            <button
              className="primary-button"
              onClick={handleProduce}
              disabled={!selectedPersona || autoProducing || selectedPersona?.status !== 'active'}
              style={{ height: 40, opacity: (!selectedPersona || autoProducing || selectedPersona?.status !== 'active') ? .5 : 1 }}
            >
              {autoProducing ? <span className="pulse-dot" /> : Icons.plus}
              {autoProducing ? 'Starting…' : 'Start production'}
            </button>
          </div>

          <div style={{ marginTop: 14 }}>
            <div className="muted-sm" style={{ marginBottom: 8 }}>Shoot themes</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
              {THEMES.map(t => {
                const selected = selectedThemes.includes(t.key)
                return (
                  <button
                    key={t.key}
                    onClick={() => setSelectedThemes(prev => selected ? prev.filter(x => x !== t.key) : [...prev, t.key])}
                    style={{
                      padding: '7px 11px',
                      borderRadius: 999,
                      border: `1px solid ${selected ? 'var(--green)' : 'var(--border)'}`,
                      background: selected ? 'var(--green-dim)' : 'transparent',
                      color: selected ? 'var(--green)' : 'var(--text-secondary)',
                      fontSize: 12,
                    }}
                  >{t.icon} {t.label}</button>
                )
              })}
            </div>
          </div>
        </section>

        <section style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 10, marginBottom: 18 }}>
          <StatCard label="Manager" value={manager ? (manager.paused ? 'PAUSED' : manager.status) : '—'} sub={manager?.current_objective || 'No manager state'} />
          <StatCard label="Identity lock" value={manager?.identity_lock?.status || '—'} sub={manager?.identity_lock?.generation_allowed ? 'Generation allowed' : 'Check identity readiness'} />
          <StatCard label="Approved media" value={(manager?.inventory?.approved_images || 0) + (manager?.inventory?.approved_videos || 0)} sub={`${manager?.inventory?.approved_images || 0} images · ${manager?.inventory?.approved_videos || 0} videos`} />
          <StatCard label="Content runway" value={manager?.inventory?.days_of_content != null ? `${Number(manager.inventory.days_of_content).toFixed(1)}d` : '—'} sub={`${manager?.inventory?.packs || 0} packs ready`} />
        </section>

        {(activePipeline || runningForPersona.length > 0) && (
          <section className="panel" style={{ marginBottom: 18 }}>
            <div className="section-header">
              <div>
                <div className="eyebrow">Active production</div>
                <h3 className="section-title" style={{ marginBottom: 3 }}>{selectedPersona?.name || 'Persona'} production run</h3>
                <div className="muted-sm">{runningForPersona[0]?.message || manager?.current_objective || 'Production pipeline active'}</div>
              </div>
              <div style={{ fontSize: 26, fontWeight: 700, color: 'var(--green)' }}>{pipelineSummary?.progress ?? runningForPersona[0]?.progress ?? 0}%</div>
            </div>

            <div className="progress-bar" style={{ margin: '14px 0' }}>
              <div className="progress-bar-fill" style={{ width: `${pipelineSummary?.progress ?? runningForPersona[0]?.progress ?? 0}%` }} />
            </div>

            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <PipelineStage label="Brief" value={selectedThemes.length ? `${selectedThemes.length} themes` : 'Ready'} active />
              <PipelineStage label="Generate" value={pipelineSummary ? `${pipelineSummary.approved + pipelineSummary.generating}/${pipelineSummary.total}` : runningForPersona.length ? 'Running' : '—'} active={Boolean(runningForPersona.length || pipelineSummary?.generating)} />
              <PipelineStage label="Identity QA" value={pipelineSummary ? `${pipelineSummary.approved} approved` : '—'} active={Boolean(pipelineSummary?.qa)} />
              <PipelineStage label="Video" value={`${videos.length} available`} />
              <PipelineStage label="Pack" value={`${manager?.inventory?.packs || 0} ready`} />
            </div>

            {manager?.blockers?.length ? (
              <div style={{ marginTop: 12, padding: 10, borderRadius: 8, background: 'var(--red-dim)', color: 'var(--red)', fontSize: 12 }}>
                {manager.blockers[0].task}: {manager.blockers[0].error || manager.blockers[0].kind || 'Blocked'}
              </div>
            ) : null}
          </section>
        )}

        <section className="panel" style={{ marginBottom: 18 }}>
          <div className="section-header">
            <div>
              <div className="eyebrow">Pipeline board</div>
              <h3 className="section-title">Per-shoot QA state</h3>
            </div>
            <span className="muted-sm">{manager?.pipeline?.length || 0} tracked shoots</span>
          </div>

          {!manager?.pipeline?.length ? (
            <div className="muted-md" style={{ padding: '18px 0' }}>No manager pipeline data for this persona yet.</div>
          ) : (
            <div style={{ display: 'grid', gap: 8 }}>
              {manager.pipeline.slice(0, 8).map(p => {
                const st = p.stages || {}
                const total = Number(st.images_total || p.shots.length || 0)
                const approved = Number(st.images_approved || p.shots.filter(s => s.status === 'APPROVED').length)
                const failed = Number(st.images_failed || p.shots.filter(s => s.status === 'FAILED').length)
                const open = expandedShoot === p.shoot_id
                return (
                  <div key={p.shoot_id} style={{ border: '1px solid var(--border)', borderRadius: 9, overflow: 'hidden' }}>
                    <button
                      onClick={() => setExpandedShoot(open ? null : p.shoot_id)}
                      style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 12, padding: '11px 12px', textAlign: 'left', background: 'var(--bg-card)' }}
                    >
                      <span style={{ fontWeight: 700 }}>Shoot {p.shoot_id.slice(0, 8)}</span>
                      <span className="muted-sm">{approved}/{total} approved</span>
                      {failed > 0 && <span style={{ color: 'var(--red)', fontSize: 12 }}>{failed} failed</span>}
                      <span style={{ marginLeft: 'auto', color: 'var(--text-muted)' }}>{open ? '▴' : '▾'}</span>
                    </button>
                    {open && (
                      <div style={{ padding: '4px 12px 10px' }}>
                        {p.shots.map(s => (
                          <div key={s.shot_id} style={{ display: 'grid', gridTemplateColumns: '70px 1fr auto', gap: 8, padding: '7px 0', borderBottom: '1px solid var(--border)', fontSize: 12 }}>
                            <span className="muted-sm">#{s.number}</span>
                            <span>{s.type}</span>
                            <span style={{ color: s.status === 'APPROVED' ? 'var(--green)' : s.status === 'FAILED' ? 'var(--red)' : 'var(--amber)' }}>
                              {s.status}{s.qa ? ` · QA ${s.qa}` : ''}{s.retries ? ` · retry ${s.retries}` : ''}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </section>

        <section style={{ marginBottom: 24 }}>
          <div className="section-header">
            <div>
              <div className="eyebrow">Media library</div>
              <h3 className="section-title">Shoots</h3>
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              {(['all', 'completed', 'failed', 'draft'] as const).map(f => (
                <button
                  key={f}
                  onClick={() => setStatusFilter(f)}
                  style={{
                    padding: '5px 11px',
                    borderRadius: 6,
                    fontSize: 12,
                    border: `1px solid ${statusFilter === f ? 'var(--green)' : 'var(--border)'}`,
                    background: statusFilter === f ? 'var(--green-dim)' : 'transparent',
                    color: statusFilter === f ? 'var(--green)' : 'var(--text-muted)',
                    textTransform: 'capitalize',
                  }}
                >{f}</button>
              ))}
            </div>
          </div>

          {loading ? (
            <p className="muted-md">Loading production data…</p>
          ) : filteredShoots.length === 0 ? (
            <div className="panel" style={{ textAlign: 'center', padding: 36 }}>
              <div className="muted-md">No {statusFilter !== 'all' ? statusFilter + ' ' : ''}shoots for {selectedPersona?.name || 'this persona'}.</div>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(175px, 1fr))', gap: 10 }}>
              {filteredShoots.map(shoot => {
                const imgs = (shoot.generated_images || []).filter(k => /^storage\/shoots\/[0-9a-f]{8}\//.test(k))
                const cover = imgs[0]
                const coverUrl = cover ? `/api/v1/shoots/${cover.split('/')[2]}/images/${cover.split('/')[3]}` : null
                return (
                  <article
                    key={shoot.id}
                    className="panel"
                    style={{ padding: 0, overflow: 'hidden', cursor: coverUrl ? 'pointer' : 'default' }}
                    onClick={() => coverUrl && setLightbox(coverUrl)}
                  >
                    <div style={{ aspectRatio: '3/4', background: 'var(--bg-card)', position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
                      {coverUrl ? <img src={coverUrl} alt="" loading="lazy" style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : <span>{Icons.image}</span>}
                      <span style={{ position: 'absolute', left: 6, top: 6, padding: '2px 7px', borderRadius: 999, background: 'rgba(0,0,0,.72)', fontSize: 10, color: shoot.status?.toLowerCase() === 'failed' ? '#f87171' : '#fff' }}>
                        {shoot.status}
                      </span>
                      {imgs.length > 0 && <span style={{ position: 'absolute', right: 6, bottom: 6, padding: '2px 7px', borderRadius: 999, background: 'rgba(0,0,0,.72)', fontSize: 10 }}>{imgs.length} imgs</span>}
                    </div>
                    <div style={{ padding: 9 }}>
                      <div style={{ fontWeight: 700, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{shoot.name}</div>
                      <div className="muted-sm">{shoot.theme || selectedPersona?.name}</div>
                    </div>
                  </article>
                )
              })}
            </div>
          )}
        </section>

        {videos.length > 0 && (
          <section style={{ marginBottom: 24 }}>
            <div className="section-header">
              <div>
                <div className="eyebrow">Video inventory</div>
                <h3 className="section-title">Videos ({videos.length})</h3>
              </div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 10 }}>
              {videos.map(v => (
                <article key={v.id} className="panel" style={{ padding: 0, overflow: 'hidden' }}>
                  {v.video_url ? (
                    <video src={v.video_url} controls muted preload="metadata" style={{ width: '100%', aspectRatio: '9/16', objectFit: 'cover', background: '#000' }} />
                  ) : (
                    <div style={{ width: '100%', aspectRatio: '9/16', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg-card)' }}>{Icons.activity}</div>
                  )}
                </article>
              ))}
            </div>
          </section>
        )}
      </div>

      {lightbox && (
        <div className="gallery-lightbox" onClick={() => setLightbox(null)}>
          <img src={lightbox} alt="Full size" style={{ maxWidth: '90vw', maxHeight: '85vh', borderRadius: 8 }} />
        </div>
      )}

      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </main>
  )
}
