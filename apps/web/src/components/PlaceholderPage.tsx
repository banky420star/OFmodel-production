'use client'

import { Icons } from '@/lib/icons'

interface Props {
  title: string
  description: string
  icon: React.ReactNode
}

export default function PlaceholderPage({ title, description, icon }: Props) {
  return (
    <main className="workspace">
      <header className="topbar">
        <button className="mobile-menu" aria-label="Open menu">{Icons.menu}</button>
        <div className="crumb">
          <a href="/"><span>Persona Studio</span></a><b>/</b>
          <strong>{title}</strong>
        </div>
      </header>
      <div className="content" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: '60vh', gap: 16 }}>
        <div style={{ color: 'var(--green)', opacity: 0.6 }}>{icon}</div>
        <h2 style={{ fontSize: 20, fontWeight: 600 }}>{title}</h2>
        <p style={{ color: 'var(--text-muted)', fontSize: 14, maxWidth: 400, textAlign: 'center' }}>{description}</p>
        <a href="/" style={{ marginTop: 8 }}>
          <button className="secondary-button">← Back to Dashboard</button>
        </a>
      </div>
    </main>
  )
}
