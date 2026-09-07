'use client'

import { useEffect, useState } from 'react'
import { getDashboardSummary, listVideos, autoProduce, listJobs, listPersonas } from '@/lib/api'
import type { DashboardSummary, ShootDetail, PersonaDetail } from '@/lib/types'
import { Icons } from '@/lib/icons'
import { StatusBadge } from '@/components/ui/StatusBadge'

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
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null)

  useEffect(() => {
    Promise.all([
      getDashboardSummary().then((data: DashboardSummary) => setShoots(data.shoots || [])),
      listPersonas().then((data: any[]) => setPersonas(data)),
      listJobs({ status: 'running' }).then((data: JobItem[]) => setJobs(data)).catch(() => {}),
    ]).then(() => setLoading(false)).catch(() => setLoading(false))
  }, [])

  // Poll for running jobs
  useEffect(() => {
    if (jobs.length === 0) return
    const interval = setInterval(() => {
      listJobs({ status: 'running' }).then((data: JobItem[]) => setJobs(data)).catch(() => {})
    }, 3000)
    return () => clearInterval(interval)
  }, [jobs.length])

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

  const handleAutoProduce = async (personaId: string) => {
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
    } catch (e) {
      setToast({ msg: `Auto-produce failed: ${e instanceof Error ? e.message : 'Unknown error'}`, type: 'error' })
      setTimeout(() => setToast(null), 5000)
    }
    const personaName = personas.find(p => p.id === personaId)?.name || 'persona'
    setAutoProducing(null)
    setToast({ msg: `Auto-produce started for ${personaName} — ${selectedThemes.length} themes`, type: 'success' })
    setTimeout(() => setToast(null), 4000)
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
        <section style={{ marginBottom: 24 }}>
          <div className="section-header">
            <h3 className="section-title">Shoot Themes</h3>
            <span className="muted-sm">Select themes for production</span>
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

        {/* Auto-Produce Cards */}
        {personas.length > 0 && (
          <section style={{ marginBottom: 24 }}>
            <div className="section-header">
              <h3 className="section-title">Quick Produce</h3>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 12 }}>
              {personas.map(p => (
                <div key={p.id} className="panel" style={{ padding: 16 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                    <span style={{
                      width: 36, height: 36, borderRadius: 8, overflow: 'hidden',
                      background: 'var(--bg-card)', display: 'flex', alignItems: 'center',
                      justifyContent: 'center', flexShrink: 0,
                    }}>
                      {p.avatar_url ? (
                        <img src={p.avatar_url} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                      ) : (
                        <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-muted)' }}>
                          {p.name.charAt(0)}
                        </span>
                      )}
                    </span>
                    <div>
                      <div style={{ fontWeight: 600, fontSize: 14 }}>{p.name}</div>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                        {p.status === 'active' ? 'Ready to produce' : 'Building...'}
                      </div>
                    </div>
                  </div>
                  <button
                    className="primary-button"
                    style={{ width: '100%', justifyContent: 'center', opacity: autoProducing === p.id ? 0.6 : 1 }}
                    disabled={autoProducing === p.id || p.status !== 'active'}
                    onClick={() => handleAutoProduce(p.id)}
                  >
                    {autoProducing === p.id ? (
                      <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span className="pulse-dot" style={{ width: 6, height: 6 }} />
                        Starting pipeline…
                      </span>
                    ) : (
                      <>
                        {Icons.plus}
                        Auto-Produce (3 shoots + videos)
                      </>
                    )}
                  </button>
                </div>
              ))}
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
                    <span className="muted-sm">{job.persona_id.slice(0, 8)}…</span>
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

        {/* Shoots */}
        <section style={{ marginBottom: 24 }}>
          <div className="section-header">
            <h3 className="section-title">Shoots ({shoots.length})</h3>
          </div>
          {loading ? (
            <p className="muted-md">Loading production data…</p>
          ) : shoots.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '48px 0', color: 'var(--text-muted)' }}>
              <p style={{ fontSize: 15, marginBottom: 8 }}>No shoots yet</p>
              <p style={{ fontSize: 13 }}>Use Auto-Produce above or create a shoot from a persona.</p>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {shoots.map(shoot => {
                const imgs = shoot.generated_images || []
                const firstImg = imgs.length > 0 ? imgs[0] : null
                const shootImgUrl = firstImg ? (() => {
                  const parts = firstImg.split('/')
                  if (parts.length >= 4) return `/api/v1/shoots/${parts[2]}/images/${parts[3]}`
                  return null
                })() : null

                return (
                  <article key={shoot.id} className="panel" style={{ padding: '16px 20px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                      <span style={{
                        width: 48, height: 48, borderRadius: 8, overflow: 'hidden',
                        background: 'var(--bg-card)', display: 'flex', alignItems: 'center',
                        justifyContent: 'center', flexShrink: 0,
                      }}>
                        {shootImgUrl ? (
                          <img src={shootImgUrl} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                        ) : (
                          <span style={{ color: 'var(--text-muted)' }}>{Icons.image}</span>
                        )}
                      </span>

                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontWeight: 600, fontSize: 14 }}>{shoot.name}</div>
                        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                          {shoot.theme || 'No theme'} · {shoot.persona_name}
                          {imgs.length > 0 && <span style={{ marginLeft: 8 }}>· {imgs.length} image{imgs.length !== 1 ? 's' : ''}</span>}
                        </div>
                      </div>

                      <div className="progress-wrap" style={{ minWidth: 120 }}>
                        <div><i style={{ width: `${Math.round(shoot.progress)}%` }}></i></div>
                        <span>{Math.round(shoot.progress)}%</span>
                      </div>
                      <StatusBadge status={shoot.status} />
                    </div>

                    {imgs.length > 1 && (
                      <div style={{ display: 'flex', gap: 6, marginTop: 12, paddingLeft: 64, overflowX: 'auto' }}>
                        {imgs.slice(0, 8).map((img, i) => {
                          const parts = img.split('/')
                          const url = parts.length >= 4 ? `/api/v1/shoots/${parts[2]}/images/${parts[3]}` : null
                          return url ? (
                            <div key={i} style={{
                              width: 72, height: 72, borderRadius: 6, overflow: 'hidden',
                              border: '1px solid var(--border)', flexShrink: 0,
                            }}>
                              <img src={url} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                            </div>
                          ) : null
                        })}
                        {imgs.length > 8 && (
                          <div style={{
                            width: 72, height: 72, borderRadius: 6, flexShrink: 0,
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            background: 'var(--bg-card)', fontSize: 12, color: 'var(--text-muted)',
                          }}>
                            +{imgs.length - 8}
                          </div>
                        )}
                      </div>
                    )}
                  </article>
                )
              })}
            </div>
          )}
        </section>

        {/* Videos */}
        {videos.length > 0 && (
          <section>
            <div className="section-header">
              <h3 className="section-title">Generated Videos ({videos.length})</h3>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
              {videos.map(v => (
                <article key={v.id} className="panel" style={{ padding: 0, overflow: 'hidden' }}>
                  {v.video_url ? (
                    <video
                      src={v.video_url}
                      controls
                      muted
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
                  <div style={{ padding: '10px 14px' }}>
                    <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 4 }}>
                      {v.duration}s · {v.width}×{v.height}
                    </div>
                    <div className="muted-sm" style={{ lineHeight: 1.4 }}>
                      {v.prompt.length > 80 ? v.prompt.slice(0, 80) + '…' : v.prompt}
                    </div>
                  </div>
                </article>
              ))}
            </div>
          </section>
        )}
      </div>

      {/* Toast notification */}
      {toast && (
        <div style={{
          position: 'fixed', bottom: 24, right: 24, zIndex: 999,
          padding: '12px 20px', borderRadius: 8, fontSize: 13, fontWeight: 500,
          background: toast.type === 'success' ? 'var(--green)' : '#ef4444',
          color: toast.type === 'success' ? '#0c0e12' : '#fff',
          boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
          animation: 'slideUp 0.3s ease',
        }}>
          {toast.type === 'success' ? '✓' : '✗'} {toast.msg}
        </div>
      )}
    </main>
  )
}
