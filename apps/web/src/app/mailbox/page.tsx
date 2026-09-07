'use client'

import { useEffect, useState } from 'react'
import { listMailboxes } from '@/lib/api'
import { Icons } from '@/lib/icons'

interface Mailbox {
  persona_id: string
  persona_name: string
  avatar_url: string
  brand: string
  status: string
  fan_count: number
  message_count: number
  unread_count: number
  revenue: number
  last_message_at: string | null
}

export default function MailboxPage() {
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    listMailboxes()
      .then((data: any[]) => { setMailboxes(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const totalFans = mailboxes.reduce((s, m) => s + m.fan_count, 0)
  const totalUnread = mailboxes.reduce((s, m) => s + m.unread_count, 0)
  const totalRevenue = mailboxes.reduce((s, m) => s + m.revenue, 0)
  const totalMessages = mailboxes.reduce((s, m) => s + m.message_count, 0)

  return (
    <main className="workspace">
      <header className="topbar">
        <div className="crumb">
          <a href="/"><span>Persona Studio</span></a><b>/</b><strong>Mailboxes</strong>
        </div>
      </header>
      <div className="content">
        <section className="page-heading">
          <div>
            <h1>AI Mailboxes</h1>
            <p>Each model has her own inbox. Fans message her, she replies with her personality.</p>
          </div>
        </section>

        {/* Summary stats */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 24 }}>
          {[
            { label: 'Active Mailboxes', value: mailboxes.filter(m => m.status === 'active').length, color: 'var(--green)' },
            { label: 'Total Fans', value: totalFans, color: 'var(--text)' },
            { label: 'Unread Messages', value: totalUnread, color: totalUnread > 0 ? 'var(--amber)' : 'var(--text-muted)' },
            { label: 'Total Revenue', value: `R ${totalRevenue.toLocaleString()}`, color: 'var(--green)' },
          ].map((stat, i) => (
            <div key={i} className="panel" style={{ padding: '14px 18px' }}>
              <div className="muted-sm" style={{ marginBottom: 4 }}>{stat.label}</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: stat.color }}>{stat.value}</div>
            </div>
          ))}
        </div>

        {/* Mailbox cards */}
        {loading ? (
          <p className="muted-md" style={{ padding: 16 }}>Loading mailboxes...</p>
        ) : mailboxes.length === 0 ? (
          <div className="panel" style={{ textAlign: 'center', padding: 48 }}>
            <p style={{ fontSize: 32, marginBottom: 12 }}>📭</p>
            <p style={{ fontSize: 14, color: 'var(--text-muted)' }}>No mailboxes yet. Create a model to get started.</p>
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 16 }}>
            {mailboxes.map(mb => (
              <a
                key={mb.persona_id}
                href={`/mailbox/${mb.persona_id}`}
                style={{ textDecoration: 'none', color: 'inherit' }}
              >
                <div className="panel" style={{
                  padding: 0, overflow: 'hidden', cursor: 'pointer',
                  transition: 'border-color 0.15s, transform 0.15s',
                  border: '1px solid var(--border)',
                }}
                  onMouseEnter={e => {
                    e.currentTarget.style.borderColor = 'var(--green)'
                    e.currentTarget.style.transform = 'translateY(-2px)'
                  }}
                  onMouseLeave={e => {
                    e.currentTarget.style.borderColor = 'var(--border)'
                    e.currentTarget.style.transform = 'translateY(0)'
                  }}
                >
                  {/* Header with avatar + name */}
                  <div style={{
                    padding: '18px 20px 14px', display: 'flex', alignItems: 'center', gap: 14,
                    borderBottom: '1px solid var(--border)',
                  }}>
                    <div style={{
                      width: 48, height: 48, borderRadius: 12, overflow: 'hidden',
                      background: 'var(--bg-card)', display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: 20, fontWeight: 700, color: 'var(--green)', flexShrink: 0,
                    }}>
                      {mb.avatar_url ? (
                        <img src={mb.avatar_url} alt={mb.persona_name} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                      ) : (
                        mb.persona_name.charAt(0).toUpperCase()
                      )}
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 600, fontSize: 15 }}>{mb.persona_name}</div>
                      <div className="muted-sm">{mb.brand || 'Content Creator'}</div>
                    </div>
                    {mb.unread_count > 0 && (
                      <div style={{
                        background: 'var(--green)', color: '#000', fontWeight: 700,
                        fontSize: 12, padding: '3px 10px', borderRadius: 10, minWidth: 24, textAlign: 'center',
                      }}>
                        {mb.unread_count}
                      </div>
                    )}
                  </div>

                  {/* Stats grid */}
                  <div style={{ padding: '14px 20px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <div>
                      <div className="muted-sm" style={{ marginBottom: 2 }}>Fans</div>
                      <div style={{ fontSize: 18, fontWeight: 700 }}>{mb.fan_count}</div>
                    </div>
                    <div>
                      <div className="muted-sm" style={{ marginBottom: 2 }}>Revenue</div>
                      <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--green)' }}>
                        R {mb.revenue.toLocaleString()}
                      </div>
                    </div>
                    <div>
                      <div className="muted-sm" style={{ marginBottom: 2 }}>Messages</div>
                      <div style={{ fontSize: 18, fontWeight: 700 }}>{mb.message_count}</div>
                    </div>
                    <div>
                      <div className="muted-sm" style={{ marginBottom: 2 }}>Last Active</div>
                      <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>
                        {mb.last_message_at
                          ? new Date(mb.last_message_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
                          : 'Never'}
                      </div>
                    </div>
                  </div>

                  {/* Footer */}
                  <div style={{
                    padding: '10px 20px', borderTop: '1px solid var(--border)',
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  }}>
                    <span style={{
                      padding: '3px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600,
                      background: mb.status === 'active' ? 'rgba(217,251,113,0.12)' : 'rgba(255,255,255,0.05)',
                      color: mb.status === 'active' ? 'var(--green)' : 'var(--text-muted)',
                      textTransform: 'uppercase', letterSpacing: '.04em',
                    }}>
                      {mb.status}
                    </span>
                    <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                      Open inbox →
                    </span>
                  </div>
                </div>
              </a>
            ))}
          </div>
        )}
      </div>
    </main>
  )
}
