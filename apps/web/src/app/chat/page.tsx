'use client'

import { useEffect, useState, useRef } from 'react'
import { listFans, listFanMessages, autoReply, listPersonas } from '@/lib/api'
import { Icons } from '@/lib/icons'

interface Fan {
  id: string; persona_id: string; username: string; display_name: string
  platform: string; status: string; subscription_tier: string
  total_spent: number; ppv_purchases: number; tips_given: number
  messages_sent: number; messages_received: number
  fan_score: number; tags: string[]; last_active: string | null
  last_message_at: string | null
}

interface ChatMsg {
  id: string; direction: string; content: string; message_type: string
  is_ai_generated: boolean; is_ppv: boolean; ppv_price: number
  sentiment: number | null; intent: string | null
  created_at: string | null
}

export default function ChatPage() {
  const [fans, setFans] = useState<Fan[]>([])
  const [selectedFan, setSelectedFan] = useState<Fan | null>(null)
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [inputText, setInputText] = useState('')
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [filter, setFilter] = useState<'all' | 'whale' | 'at_risk' | 'new'>('all')
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    listFans().then((data: any[]) => {
      setFans(data)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const selectFan = async (fan: Fan) => {
    setSelectedFan(fan)
    try {
      const msgs = await listFanMessages(fan.id)
      setMessages(msgs)
    } catch { setMessages([]) }
  }

  const handleSend = async () => {
    if (!selectedFan || !inputText.trim() || sending) return
    const text = inputText.trim()
    setInputText('')
    setSending(true)

    // Add user message optimistically
    const tempMsg: ChatMsg = {
      id: 'temp', direction: 'inbound', content: text,
      message_type: 'text', is_ai_generated: false, is_ppv: false,
      ppv_price: 0, sentiment: null, intent: null, created_at: new Date().toISOString(),
    }
    setMessages(prev => [...prev, tempMsg])

    try {
      const reply = await autoReply(selectedFan.id, text)
      setMessages(prev => [
        ...prev.filter(m => m.id !== 'temp'),
        { ...tempMsg, id: 'inbound-' + Date.now() },
        {
          id: 'outbound-' + Date.now(), direction: 'outbound',
          content: reply.reply, message_type: 'text',
          is_ai_generated: true, is_ppv: false, ppv_price: 0,
          sentiment: reply.sentiment, intent: reply.intent,
          created_at: new Date().toISOString(),
        },
      ])
    } catch { setMessages(prev => prev.filter(m => m.id !== 'temp')) }
    setSending(false)
  }

  const filteredFans = fans.filter(f => {
    if (filter === 'whale') return (f.total_spent || 0) > 100
    if (filter === 'at_risk') return f.status === 'inactive'
    if (filter === 'new') {
      const week = new Date(Date.now() - 7 * 86400000)
      return f.created_at && new Date(f.created_at) > week
    }
    return true
  })

  const totalRevenue = fans.reduce((sum, f) => sum + (f.total_spent || 0), 0)
  const whaleCount = fans.filter(f => (f.total_spent || 0) > 100).length

  return (
    <main className="workspace">
      <header className="topbar">
        <div className="crumb">
          <a href="/"><span>Persona Studio</span></a><b>/</b><strong>Fan Chat</strong>
        </div>
      </header>
      <div className="content">
        <section className="page-heading">
          <div>
            <h1>Fan Chat & Revenue</h1>
            <p>AI-powered fan engagement. Auto-reply, PPV, and scoring.</p>
          </div>
        </section>

        {/* Stats */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 24 }}>
          {[
            { label: 'Total Fans', value: fans.length, color: 'var(--text)' },
            { label: 'Revenue', value: `R ${totalRevenue.toLocaleString()}`, color: 'var(--green)' },
            { label: 'Whales', value: whaleCount, color: 'var(--blue)' },
            { label: 'AI Score Avg', value: fans.length ? (fans.reduce((s, f) => s + (f.fan_score || 0), 0) / fans.length).toFixed(0) : '0', color: 'var(--text)' },
          ].map((stat, i) => (
            <div key={i} className="panel" style={{ padding: '14px 18px' }}>
              <div className="muted-sm" style={{ marginBottom: 4 }}>{stat.label}</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: stat.color }}>{stat.value}</div>
            </div>
          ))}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 16, height: 'calc(100vh - 260px)' }}>
          {/* Fan List */}
          <div className="panel" style={{ overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)' }}>
              <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 10 }}>Fans</div>
              <div style={{ display: 'flex', gap: 4 }}>
                {(['all', 'whale', 'at_risk', 'new'] as const).map(f => (
                  <button key={f} onClick={() => setFilter(f)} style={{
                    padding: '4px 10px', borderRadius: 6, fontSize: 11, cursor: 'pointer',
                    border: `1px solid ${filter === f ? 'var(--green)' : 'var(--border)'}`,
                    background: filter === f ? 'rgba(217,251,113,0.1)' : 'transparent',
                    color: filter === f ? 'var(--green)' : 'var(--text-muted)',
                  }}>
                    {f === 'all' ? 'All' : f === 'whale' ? '🐋 Whales' : f === 'at_risk' ? '⚠️ At Risk' : '🆕 New'}
                  </button>
                ))}
              </div>
            </div>
            <div style={{ flex: 1, overflow: 'auto', padding: 8 }}>
              {loading ? (
                <p className="muted-md" style={{ padding: 16 }}>Loading fans...</p>
              ) : filteredFans.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '32px 16px', color: 'var(--text-muted)' }}>
                  <p style={{ fontSize: 14, marginBottom: 8 }}>No fans yet</p>
                  <p style={{ fontSize: 12 }}>Fans appear here when they subscribe.</p>
                </div>
              ) : (
                filteredFans.map(fan => (
                  <div key={fan.id} onClick={() => selectFan(fan)} style={{
                    padding: '10px 14px', borderRadius: 8, cursor: 'pointer', marginBottom: 4,
                    background: selectedFan?.id === fan.id ? 'rgba(217,251,113,0.08)' : 'transparent',
                    border: `1px solid ${selectedFan?.id === fan.id ? 'var(--green)' : 'transparent'}`,
                    transition: 'all 0.15s',
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <div>
                        <div style={{ fontWeight: 600, fontSize: 13 }}>{fan.display_name || fan.username}</div>
                        <div className="muted-sm">@{fan.username}</div>
                      </div>
                      <div style={{ textAlign: 'right' }}>
                        <div style={{ fontSize: 12, color: 'var(--green)', fontWeight: 600 }}>R {(fan.total_spent || 0).toLocaleString()}</div>
                        <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>Score: {fan.fan_score || 0}</div>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Chat Area */}
          <div className="panel" style={{ overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            {selectedFan ? (
              <>
                {/* Chat Header */}
                <div style={{ padding: '12px 18px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 12 }}>
                  <div style={{ fontWeight: 600 }}>{selectedFan.display_name || selectedFan.username}</div>
                  <span className="muted-sm">@{selectedFan.username}</span>
                  <span style={{
                    padding: '2px 8px', borderRadius: 10, fontSize: 10, fontWeight: 600,
                    background: selectedFan.fan_score > 50 ? 'rgba(217,251,113,0.15)' : 'var(--bg-card)',
                    color: selectedFan.fan_score > 50 ? 'var(--green)' : 'var(--text-muted)',
                  }}>
                    Score: {selectedFan.fan_score || 0}
                  </span>
                  <span style={{
                    padding: '2px 8px', borderRadius: 10, fontSize: 10,
                    background: 'var(--bg-card)', color: 'var(--text-muted)',
                  }}>
                    R {(selectedFan.total_spent || 0).toLocaleString()} spent
                  </span>
                </div>

                {/* Messages */}
                <div style={{ flex: 1, overflow: 'auto', padding: 16 }}>
                  {messages.length === 0 && (
                    <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-muted)' }}>
                      <p style={{ fontSize: 24, marginBottom: 8 }}>💬</p>
                      <p>Start chatting with {selectedFan.display_name || selectedFan.username}</p>
                    </div>
                  )}
                  {messages.map(msg => (
                    <div key={msg.id} style={{
                      display: 'flex', justifyContent: msg.direction === 'outbound' ? 'flex-end' : 'flex-start',
                      marginBottom: 8,
                    }}>
                      <div style={{
                        maxWidth: '70%', padding: '10px 14px', borderRadius: 12,
                        background: msg.direction === 'outbound' ? 'var(--green)' : 'var(--bg-card)',
                        color: msg.direction === 'outbound' ? '#000' : 'var(--text)',
                        fontSize: 13, lineHeight: 1.5,
                      }}>
                        {msg.content}
                        <div style={{
                          fontSize: 10, marginTop: 4, opacity: 0.6,
                          display: 'flex', gap: 6, alignItems: 'center',
                        }}>
                          {msg.created_at && new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                          {msg.is_ai_generated && <span style={{ fontSize: 9 }}>🤖 AI</span>}
                          {msg.intent && msg.intent !== 'conversation' && (
                            <span style={{ fontSize: 9, background: 'rgba(0,0,0,0.1)', padding: '1px 4px', borderRadius: 4 }}>
                              {msg.intent}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                  {sending && (
                    <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
                      <div className="panel" style={{ padding: '10px 14px', borderRadius: 12 }}>
                        <span className="pulse-dot" style={{ width: 6, height: 6 }} /> thinking...
                      </div>
                    </div>
                  )}
                  <div ref={messagesEndRef} />
                </div>

                {/* Input */}
                <div style={{ padding: '12px 18px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8 }}>
                  <input
                    value={inputText}
                    onChange={e => setInputText(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && handleSend()}
                    placeholder="Type a message..."
                    style={{
                      flex: 1, padding: '10px 14px', borderRadius: 8, border: '1px solid var(--border)',
                      background: 'var(--bg-card)', color: 'var(--text)', fontSize: 13,
                    }}
                  />
                  <button className="primary-button" onClick={handleSend} disabled={!inputText.trim() || sending}>
                    Send
                  </button>
                </div>
              </>
            ) : (
              <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>
                <div style={{ textAlign: 'center' }}>
                  <p style={{ fontSize: 32, marginBottom: 12 }}>💬</p>
                  <p style={{ fontSize: 14 }}>Select a fan to start chatting</p>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </main>
  )
}
