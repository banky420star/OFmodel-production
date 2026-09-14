'use client'

import { useEffect, useState } from 'react'
import { getDashboardSummary, listVideos, autoProduce, listJobs, listPersonas } from '@/lib/api'
import type { DashboardSummary, ShootDetail, PersonaDetail } from '@/lib/types'
import { Icons } from '@/lib/icons'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Toast, type ToastState } from '@/components/ui/Toast'

interface VideoItem {
  id: string; prompt: string; video_url: string; video_key: string;
  duration: number; width: number; height: number;
  generation_time_ms: number; created_at: string;
}

interface JobItem {
  id: string; persona_id: string; job_type: string; status: string;
  progress: number; message: string | null;
  created_at: string; updated_at: string;
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

export default function ProductionPage() {
  const [shoots, setShoots] = useState<ShootDetail[]>([])
  const [videos, setVideos] = useState<VideoItem[]>([])
  const [jobs, setJobs] = useState<JobItem[]>([])
  const [personas, setPersonas] = useState<PersonaDetail[]>([])
  const [loading, setLoading] = useState(true)
  const [autoProducing, setAutoProducing] = useState<string | null>(null)
  const [selectedThemes, setSelectedThemes] = useState<string[]>(['lifestyle', 'fashion', 'swimwear'])
  const [toast, setToast] = useState<ToastState>(null)
  const [statusFilter, setStatusFilter] = useState<'all' | 'completed' | 'failed' | 'draft'>('all')
  const [lightbox, setLightbox] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([
      getDashboardSummary().then((data: DashboardSummary) => setShoots(data.shoots || [])),
      listPersonas().then((data: any[]) => setPersonas(data)),
      listJobs({ status: 'running' }).then((data: JobItem[]) => setJobs(data)).catch(() => {}),
    ]).then(() => setLoading(false)).catch(() => setLoading(false))
  }, [])

  // Poll for running jobs — always, so jobs started elsewhere still appear
  useEffect(() => {
    const interval = setInterval(() => {
      listJobs({ status: 'running' }).then((data: JobItem[]) => setJobs(data)).catch(() => {})
    }, 3000)
    return () => clearInterval(interval)
  }, [])

  // Load videos for each persona
  useEffect(() => {
    if (personas.length === 0) return
    Promise.all(
      personas.map(p => listVideos(p.id).catch(() => []))
    ).then(results => {
      const all = results.flat().filter(Boolean)
      setVideos(all as VideoItem[])
    })
  }, [personas])

  const filteredShoots = statusFilter === 'all'
    ? shoots
    : shoots.filter(s => (s.status || '').toLowerCase() === statusFilter)

  const handleAutoProduce = async (personaId: string) => {
    const personaName = personas.find(p => p.id === personaId)?.name || 'persona'
    setAutoProducing(personaId)
    try {
      await autoProduce(personaId, {
        shoot_count: selectedThemes.length,
        images_per_shoot: 5,
        generate_videos: true,
        adult_content: false,
        themes: selectedThemes.join(','),
      })
      // Refresh data after a moment
      setTimeout(() => {
        getDashboardSummary().then((data: DashboardSummary) => setShoots(data.shoots || []))
        listJobs({ status: 'running' }).then((data: JobItem[]) => setJobs(data)).catch(() => {})
      }, 2000)
      setToast({ msg: `Auto-produce started for ${personaName} — ${selectedThemes.length} themes`, type: 'success' })
      setTimeout(() => setToast(null), 4000)
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Unknown error'
      setToast({
        msg: msg.includes('409')
          ? `${personaName} is already producing — wait for the current run to finish`
          : `Auto-produce failed: ${msg}`,
        type: 'error',
      })
      setTimeout(() => setToast(null), 5000)
    } finally {
      setAutoProducing(null)
    }
  }

  return (
    <main className="workspace">
      <header className="topbar">
        <button className="mobile-menu" aria-label="Open menu">{Icons.menu}</button>
        <div className="crumb">
          <a href="/"><span>Persona Studio</span></a><b>/</b><strong>Production</strong>
        </div>
      </header>
      <div className="content">
        <section className="page-heading">
          <div>
            <h1>Production Queue</h1>
            <p>Shoots, videos, and generation jobs across your personas.</p>
          </div>
        </section>

        {/* Theme Selector */}
        <section style={{ marginBottom: 20 }}>
          <div className="section-header">
            <h3 className="section-title">Shoot Themes</h3>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {THEMES.map(t => (
              <button
                key={t.key}
                onClick={() => {
                  setSelectedThemes(prev =>
                    prev.includes(t.key)
                      ? prev.filter(x => x !== t.key)
                      : [...prev, t.key]
                  )
                }}
                style={{
                  padding: '8px 14px', borderRadius: 8,
                  border: `1px solid ${selectedThemes.includes(t.key) ? 'var(--green)' : 'var(--border)'}`,
                  background: selectedThemes.includes(t.key) ? 'rgba(217,251,113,0.1)' : 'var(--bg-card)',
                  color: selectedThemes.includes(t.key) ? 'var(--green)' : 'var(--text-secondary)',
                  fontSize: 13, cursor: 'pointer',
                  fontWeight: selectedThemes.includes(t.key) ? 600 : 400,
                  transition: 'all 0.15s',
                }}
              >
                {t.icon} {t.label}
              </button>
            ))}
          </div>
          <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text-muted)' }}>
            {selectedThemes.length} theme{selectedThemes.length !== 1 ? 's' : ''} selected — each gets 5 images + video
          </div>
        </section>

        {/* Quick Produce — compact single row */}
        {personas.length > 0 && (
          <section style={{ marginBottom: 20 }}>
            <div className="section-header">
              <h3 className="section-title">Quick Produce</h3>
              <span className="muted-sm">{selectedThemes.length} themes × 5 images + video each</span>
            </div>
            <div className="panel" style={{ padding: '10px 12px' }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {personas.map(p => {
                  const busy = jobs.some(j => j.persona_id === p.id)
                  const producing = autoProducing === p.id
                  const disabled = producing || p.status !== 'active' || busy
                  return (
                    <button
                      key={p.id}
                      disabled={disabled}
                      onClick={() => handleAutoProduce(p.id)}
                      title={
                        p.status !== 'active'
                          ? `Cannot produce — status: ${p.status}`
                          : busy
                            ? 'Already producing'
                            : `Produce ${selectedThemes.length} shoots for ${p.name}`
                      }
                      style={{
                        display: 'flex', alignItems: 'center', gap: 8,
                        padding: '6px 12px 6px 6px', borderRadius: 20,
                        border: `1px solid ${disabled ? 'var(--border)' : 'var(--green)'}`,
                        background: producing ? 'rgba(217,251,113,0.15)' : 'transparent',
                        color: disabled ? 'var(--text-muted)' : 'var(--text)',
                        fontSize: 13, cursor: disabled ? 'not-allowed' : 'pointer',
                        opacity: disabled ? 0.55 : 1,
                      }}
                    >
                      <span style={{
                        width: 26, height: 26, borderRadius: '50%', overflow: 'hidden',
                        background: 'var(--bg-card)', display: 'flex', alignItems: 'center',
                        justifyContent: 'center', flexShrink: 0, fontSize: 11, fontWeight: 600,
                        color: 'var(--text-muted)',
                      }}>
                        {p.avatar_url ? (
                          <img src={p.avatar_url} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                        ) : (
                          p.name.charAt(0)
                        )}
                      </span>
                      {p.name}
                      {producing || busy ? <span className="pulse-dot" style={{ width: 5, height: 5 }} /> : Icons.plus}
                    </button>
                  )
                })}
              </div>
            </div>
          </section>
        )}

        {/* Running Jobs */}
        {jobs.length > 0 && (
          <section style={{ marginBottom: 24 }}>
            <div className="section-header">
              <h3 className="section-title">Running Jobs</h3>
              <span className="count-badge">{jobs.length}</span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {jobs.map(job => (
                <div key={job.id} className="panel" style={{ padding: '14px 18px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
                    <span className="pulse-dot" />
                    <span style={{ fontWeight: 600, fontSize: 13 }}>{job.job_type.replace(/_/g, ' ')}</span>
                    <span className="muted-sm">{personas.find(p => p.id === job.persona_id)?.name || `${job.persona_id.slice(0, 8)}…`}</span>
                    <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--green)' }}>{job.progress}%</span>
                  </div>
                  <div className="progress-bar">
                    <div className="progress-bar-fill" style={{ width: `${job.progress}%` }} />
                  </div>
                  {job.message && <p className="muted-sm" style={{ marginTop: 6 }}>{job.message}</p>}
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Shoots — dense grid, filterable */}
        <section style={{ marginBottom: 24 }}>
          <div className="section-header">
            <h3 className="section-title">
              Shoots
              <span className="count-badge" style={{ marginLeft: 8 }}>{filteredShoots.length}</span>
            </h3>
            <div style={{ display: 'flex', gap: 6 }}>
              {(['all', 'completed', 'failed', 'draft'] as const).map(f => (
                <button
                  key={f}
                  onClick={() => setStatusFilter(f)}
                  style={{
                    padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
                    border: `1px solid ${statusFilter === f ? 'var(--green)' : 'var(--border)'}`,
                    background: statusFilter === f ? 'rgba(217,251,113,0.1)' : 'transparent',
                    color: statusFilter === f ? 'var(--green)' : 'var(--text-muted)',
                    textTransform: 'capitalize',
                  }}
                >
                  {f}
                </button>
              ))}
            </div>
          </div>
          {loading ? (
            <p className="muted-md">Loading production data…</p>
          ) : filteredShoots.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '48px 0', color: 'var(--text-muted)' }}>
              <p style={{ fontSize: 15, marginBottom: 8 }}>No {statusFilter !== 'all' ? statusFilter + ' ' : ''}shoots</p>
              <p style={{ fontSize: 13 }}>Use Auto-Produce above or create a shoot from a persona.</p>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 10 }}>
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
                    title={coverUrl ? `${shoot.name} — click to view` : shoot.name}
                  >
                    <div style={{
                      width: '100%', aspectRatio: '3/4', background: 'var(--bg-card)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      color: 'var(--text-muted)', position: 'relative', overflow: 'hidden',
                    }}>
                      {coverUrl ? (
                        <img src={coverUrl} alt="" loading="lazy" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                      ) : (
                        <span>{Icons.image}</span>
                      )}
                      {imgs.length > 1 && (
                        <span style={{
                          position: 'absolute', bottom: 6, right: 6, background: 'rgba(0,0,0,0.7)',
                          color: '#fff', fontSize: 11, padding: '2px 7px', borderRadius: 10,
                        }}>{imgs.length}</span>
                      )}
                      {shoot.status?.toUpperCase() !== 'COMPLETED' && (
                        <span style={{
                          position: 'absolute', top: 6, left: 6, background: 'rgba(0,0,0,0.7)',
                          color: shoot.status?.toUpperCase() === 'FAILED' ? '#f87171' : '#fbbf24', fontSize: 10,
                          padding: '2px 7px', borderRadius: 10, textTransform: 'uppercase',
                        }}>{shoot.status}</span>
                      )}
                    </div>
                    <div style={{ padding: '8px 10px' }}>
                      <div style={{ fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {shoot.name}
                      </div>
                      <div className="muted-sm" style={{ fontSize: 11 }}>{shoot.persona_name}</div>
                    </div>
                  </article>
                )
              })}
            </div>
          )}
        </section>

        {/* Videos */}
        {videos.length > 0 && (
          <section style={{ marginBottom: 24 }}>
            <div className="section-header">
              <h3 className="section-title">Videos ({videos.length})</h3>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 10 }}>
              {videos.map(v => (
                <article key={v.id} className="panel" style={{ padding: 0, overflow: 'hidden' }}>
                  {v.video_url ? (
                    <video
                      src={v.video_url}
                      controls
                      muted
                      preload="metadata"
                      style={{ width: '100%', aspectRatio: '9/16', objectFit: 'cover', background: '#000' }}
                    />
                  ) : (
                    <div style={{
                      width: '100%', aspectRatio: '9/16', background: 'var(--bg-card)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      color: 'var(--text-muted)',
                    }}>
                      <span>{Icons.activity}</span>
                    </div>
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
