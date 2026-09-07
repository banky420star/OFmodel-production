'use client'

import { useEffect, useState } from 'react'
import { getDashboardSummary } from '@/lib/api'
import type { DashboardSummary } from '@/lib/types'
import { Icons } from '@/lib/icons'

export default function SettingsPage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getDashboardSummary()
      .then(data => { setSummary(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const providers = [
    { name: 'LLM', key: 'llm', description: 'Ollama — local language model for identity generation, shoot planning, QA' },
    { name: 'Image', key: 'image', description: 'HuggingFace Inference API — FLUX.1-schnell for image generation' },
    { name: 'Video', key: 'video', description: 'DashScope Wan — Alibaba Cloud video generation' },
    { name: 'Voice', key: 'voice', description: 'ElevenLabs — voice synthesis and voice profile creation' },
    { name: 'Trainer', key: 'trainer', description: 'LoRA model training (mock — requires GPU worker)' },
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
