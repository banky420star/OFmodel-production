'use client'

import { useEffect, useState } from 'react'
import { getDashboardSummary, getSystemProviders } from '@/lib/api'
import type { DashboardSummary } from '@/lib/types'
import { Icons } from '@/lib/icons'

export default function SettingsPage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [providerRows, setProviderRows] = useState<any[]>([])

  useEffect(() => {
    Promise.all([getDashboardSummary(), getSystemProviders()])
      .then(([data, providerData]) => { setSummary(data); setProviderRows(providerData.capabilities || []); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const providers = [
    { name: 'LLM', key: 'llm', description: 'Ollama — local language model for identity generation, shoot planning, QA' },
    { name: 'Image', key: 'image', description: 'ComfyUI — local identity-locked image generation on Apple Silicon' },
    { name: 'Video', key: 'video', description: 'Wan-compatible server — local video generation when configured' },
    { name: 'Voice', key: 'voice', description: 'macOS say + ffmpeg — local synthesis without voice cloning' },
    { name: 'Trainer', key: 'trainer', description: 'HuggingFace LoRA trainer — local MPS/CUDA training' },
    { name: 'Storage', key: 'storage', description: 'Local filesystem — files saved to hard drive' },
  ]

  const checks = summary?.health.checks || []

  return (
    <main className="workspace">
      <header className="topbar">
        <button className="mobile-menu" aria-label="Open menu">{Icons.menu}</button>
        <div className="crumb">
          <a href="/"><span>Persona Studio</span></a><b>/</b><strong>Settings</strong>
        </div>
      </header>
      <div className="content">
        <section className="page-heading">
          <h1>Settings</h1>
          <p>Provider configuration and system status.</p>
        </section>

        {loading ? (
          <p className="muted-md">Loading settings…</p>
        ) : (
          <>
            <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Provider Status</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 32 }}>
              {providers.map(p => {
                const check = checks.find(c => c.service === p.key)
                const status = check?.status || 'unknown'
                return (
                  <div key={p.key} className="panel" style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 16 }}>
                    <span style={{
                      width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                      background: status === 'green' ? 'var(--green)' : status === 'yellow' ? '#fbbf24' : '#ef4444',
                    }} />
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 600, fontSize: 13 }}>{p.name}</div>
                      <div style={{ fontSize: 12, color: 'var(--muted)' }}>{p.description}</div>
                    </div>
                    <span style={{
                      fontSize: 11, fontWeight: 600, textTransform: 'uppercase',
                      color: status === 'green' ? 'var(--green)' : status === 'yellow' ? '#fbbf24' : '#ef4444',
                    }}>
                      {status === 'green' ? 'Online' : status === 'yellow' ? 'Degraded' : 'Offline'}
                    </span>
                  </div>
                )
              })}
            </div>

            <div className="panel" style={{ marginBottom: 32, borderColor: providerRows.find(p => p.capability === 'image')?.provider === 'ComfyUIImageProvider' ? 'var(--green)' : 'var(--border)' }}>
              <div className="panel-title">
                <div><span className="kicker">Image workflow</span><h3>ComfyUI identity-locked generation</h3></div>
                <span className="count-badge" style={{ background: 'var(--blue-dim)', color: 'var(--blue)' }}>LOCAL GPU</span>
              </div>
              <p className="muted-sm" style={{ maxWidth: 720, marginBottom: 14 }}>
                The app uploads the approved avatar to ComfyUI, runs an img2img workflow with moderate denoise, downloads the rendered PNG, and sends it through technical QA. It will not generate without an active identity lock and reference image.
              </p>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
                {['CheckpointLoaderSimple', 'KSampler', 'LoadImage', 'VAEEncode', 'SaveImage'].map(node => <span key={node} className="status completed" style={{ textTransform: 'none' }}>{node}</span>)}
              </div>
              <code style={{ display: 'block', padding: 10, background: 'var(--bg-card)', borderRadius: 6, color: 'var(--text-secondary)', fontSize: 12 }}>IMAGE_PROVIDER=comfyui · COMFYUI_URL=http://127.0.0.1:8188</code>
              <p className="muted-sm" style={{ marginTop: 10 }}>Run <code>python3 scripts/check_comfyui.py</code> from the project root to verify the server and checkpoint nodes.</p>
            </div>

            <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>System Info</h3>
            <div className="panel" style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
              {[
                { label: 'Active Models', value: summary?.active_models || 0 },
                { label: 'Total Shoots', value: summary?.total_shoots || 0 },
                { label: 'Content Packs', value: summary?.total_packs || 0 },
                { label: 'Services Online', value: `${summary?.health.online || 0}/${summary?.health.total || 0}` },
              ].map(item => (
                <div key={item.label} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                  <span style={{ color: 'var(--muted)' }}>{item.label}</span>
                  <span style={{ fontWeight: 600 }}>{item.value}</span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </main>
  )
}
