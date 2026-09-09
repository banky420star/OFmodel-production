'use client'

import { useEffect, useState } from 'react'
import { getDashboardSummary, mediaUrl } from '@/lib/api'
import type { DashboardSummary } from '@/lib/types'
import { GRADIENTS } from '@/lib/constants'
import { Icons } from '@/lib/icons'
import { StatusBadge } from '@/components/ui/StatusBadge'

export default function ModelsPage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getDashboardSummary()
      .then(data => { setSummary(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  return (
    <main className="workspace">
      <header className="topbar">
        <button className="mobile-menu" aria-label="Open menu">{Icons.menu}</button>
        <div className="crumb">
          <a href="/"><span>Persona Studio</span></a><b>/</b>
          <strong>Models</strong>
        </div>
        <div className="top-actions">
          <a href="/models/create">
            <button className="primary-button">{Icons.plus} Create model</button>
          </a>
        </div>
      </header>

      <div className="content">
        <section className="page-heading">
          <h1>Models</h1>
          <p>All synthetic creator identities in your workspace.</p>
        </section>

        {loading ? (
          <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>Loading models…</p>
        ) : !summary || summary.personas.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '48px 0', color: 'var(--text-muted)' }}>
            <p style={{ fontSize: 15, marginBottom: 8 }}>No models yet</p>
            <a href="/models/create" style={{ color: 'var(--green)', textDecoration: 'none' }}>Create your first model →</a>
          </div>
        ) : (
          <section className="persona-grid">
            {summary.personas.map((p, i) => (
              <a key={p.id} href={`/personas/${p.id}`} style={{ textDecoration: 'none' }}>
                <article className="persona-card">
                  <div className="persona-art" style={{ background: GRADIENTS[i % GRADIENTS.length] }}>
                    {p.avatar_url ? (
                      <img src={mediaUrl(p.avatar_url)} alt={p.name} style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: 'inherit' }} />
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
        )}
      </div>
    </main>
  )
}
