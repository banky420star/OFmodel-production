'use client'

import { useEffect, useState, useRef, use } from 'react'
import { getMailbox, listFanMessages, autoReply, sendAsPersona } from '@/lib/api'

interface Thread {
  fan_id: string
  username: string
  display_name: string
  platform: string
  subscription_tier: string
  total_spent: number
  fan_score: number
  tags: string[]
  last_message: {
    content: string
    direction: string
    is_ai_generated: boolean
    created_at: string | null
  } | null
  unread_count: number
}

interface ChatMsg {
  id: string
  direction: string
  content: string
  message_type: string
  is_ai_generated: boolean
  sentiment: number | null
  intent: string | null
  created_at: string | null
}

interface MailboxData {
  persona_id: string
  persona_name: string
  avatar_url: string
  brand: string
  total_fans: number
  total_revenue: number
  threads: Thread[]
}

export default function MailboxDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const [mailbox, setMailbox] = useState<MailboxData | null>(null)
  const [selectedThread, setSelectedThread] = useState<Thread | null>(null)
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [inputText, setInputText] = useState('')
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [autoMode, setAutoMode] = useState(true)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    getMailbox(id)
      .then((data: any) => { setMailbox(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [id])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const selectThread = async (thread: Thread) => {
    setSelectedThread(thread)
    try {
      const msgs = await listFanMessages(thread.fan_id)
      setMessages(msgs)
    } catch {
      setMessages([])
    }
  }

  const handleSend = async () => {
    if (!selectedThread || !inputText.trim() || sending) return
    const text = inputText.trim()
    setInputText('')
    setSending(true)

    // Add user message optimistically
    const tempMsg: ChatMsg = {
      id: 'temp', direction: 'outbound', content: text,
      message_type: 'text', is_ai_generated: false,
      sentiment: null, intent: null, created_at: new Date().toISOString(),
    }
    setMessages(prev => [...prev, tempMsg])

    try {
      if (autoMode) {
        // AI auto-reply mode: send as fan message, get persona's AI reply
        const reply = await autoReply(selectedThread.fan_id, text)
        setMessages(prev => [
          ...prev.filter(m => m.id !== 'temp'),
          { ...tempMsg, id: 'inbound-' + Date.now(), direction: 'inbound' },
          {
            id: 'outbound-' + Date.now(), direction: 'outbound',
            content: reply.reply, message_type: 'text',
            is_ai_generated: true, sentiment: reply.sentiment,
            intent: reply.intent, created_at: new Date().toISOString(),
          },
        ])
      } else {
        // Manual mode: send as the persona directly
        await sendAsPersona(id, selectedThread.fan_id, text)
        setMessages(prev => [
          ...prev.filter(m => m.id !== 'temp'),
          { ...tempMsg, id: 'manual-' + Date.now() },
        ])
      }
    } catch {
      setMessages(prev => prev.filter(m => m.id !== 'temp'))
    }
    setSending(false)

    // Refresh thread list to update last message
    try {
      const updated = await getMailbox(id)
      setMailbox(updated)
    } catch {}
  }

  if (loading) {
    return (
      <main className="workspace">
        <header className="topbar">
          <div className="crumb">
            <a href="/"><span>Persona Studio</span></a><b>/</b>
            <a href="/mailbox"><span>Mailboxes</span></a><b>/</b><strong>Loading...</strong>
          </div>
        </header>
        <div className="content"><p className="muted-md" style={{ padding: 16 }}>Loading mailbox...</p></div>
      </main>
    )
  }

  if (!mailbox) {
    return (
      <main className="workspace">
        <header className="topbar">
          <div className="crumb">
            <a href="/"><span>Persona Studio</span></a><b>/</b>
            <a href="/mailbox"><span>Mailboxes</span></a><b>/</b><strong>Not Found</strong>
          </div>
        </header>
        <div className="content">
          <div className="panel" style={{ textAlign: 'center', padding: 48 }}>
            <p style={{ fontSize: 14, color: 'var(--text-muted)' }}>Mailbox not found.</p>
            <a href="/mailbox" className="primary-button" style={{ marginTop: 12, display: 'inline-block', padding: '8px 16px', borderRadius: 8, fontSize: 13 }}>
              Back to Mailboxes
            </a>
          </div>
        </div>
      </main>
    )
  }

  return (
    <main className="workspace">
      <header className="topbar">
        <div className="crumb">
          <a href="/"><span>Persona Studio</span></a><b>/</b>
          <a href="/mailbox"><span>Mailboxes</span></a><b>/</b>
          <strong>{mailbox.persona_name}</strong>
        </div>
      </header>
      <div className="content">
        {/* Persona header */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 16, marginBottom: 20,
          padding: '16px 20px', background: 'var(--bg-card)', borderRadius: 12,
          border: '1px solid var(--border)',
        }}>
          <div style={{
            width: 56, height: 56, borderRadius: 14, overflow: 'hidden',
            background: 'rgba(217,251,113,0.08)', display: 'flex', alignItems: 'center',
            justifyContent: 'center', fontSize: 22, fontWeight: 700, color: 'var(--green)', flexShrink: 0,
          }}>
            {mailbox.avatar_url ? (
              <img src={mailbox.avatar_url} alt={mailbox.persona_name} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
            ) : (
              mailbox.persona_name.charAt(0)
            )}
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 700, fontSize: 18 }}>{mailbox.persona_name}&apos;s Mailbox</div>
            <div className="muted-sm">{mailbox.brand || 'Content Creator'} — {mailbox.total_fans} fans — R {mailbox.total_revenue.toLocaleString()} revenue</div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <span style={{
              padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600,
              background: 'rgba(217,251,113,0.12)', color: 'var(--green)',
            }}>
              AI Auto-Reply: {autoMode ? 'ON' : 'OFF'}
            </span>
            <button
              onClick={() => setAutoMode(!autoMode)}
              style={{
                padding: '6px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
                border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)',
              }}
            >
              {autoMode ? 'Switch to Manual' : 'Switch to AI'}
            </button>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '340px 1fr', gap: 16, height: 'calc(100vh - 240px)' }}>
          {/* Thread list */}
          <div className="panel" style={{ overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)' }}>
              <div style={{ fontWeight: 600, fontSize: 14 }}>
                Inbox
                <span className="muted-sm" style={{ marginLeft: 8 }}>{mailbox.threads.length} conversations</span>
              </div>
            </div>
            <div style={{ flex: 1, overflow: 'auto', padding: 8 }}>
              {mailbox.threads.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '32px 16px', color: 'var(--text-muted)' }}>
                  <p style={{ fontSize: 24, marginBottom: 8 }}>📭</p>
                  <p style={{ fontSize: 13 }}>No fans messaging yet.</p>
                  <p style={{ fontSize: 12, marginTop: 4 }}>Fans will appear here when they send messages.</p>
                </div>
              ) : (
                mailbox.threads.map(thread => (
                  <div
                    key={thread.fan_id}
                    onClick={() => selectThread(thread)}
                    style={{
                      padding: '12px 14px', borderRadius: 8, cursor: 'pointer', marginBottom: 4,
                      background: selectedThread?.fan_id === thread.fan_id ? 'rgba(217,251,113,0.08)' : 'transparent',
                      border: `1px solid ${selectedThread?.fan_id === thread.fan_id ? 'var(--green)' : 'transparent'}`,
                      transition: 'all 0.15s',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                          <span style={{ fontWeight: 600, fontSize: 13 }}>
                            {thread.display_name || thread.username}
                          </span>
                          {thread.subscription_tier !== 'free' && (
                            <span style={{
                              fontSize: 9, padding: '1px 5px', borderRadius: 4,
                              background: 'rgba(217,251,113,0.12)', color: 'var(--green)',
                              fontWeight: 600, textTransform: 'uppercase',
                            }}>
                              {thread.subscription_tier}
                            </span>
                          )}
                        </div>
                        {thread.last_message && (
                          <div style={{
                            fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'nowrap',
                            overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 220,
                          }}>
                            {thread.last_message.direction === 'outbound' ? '← ' : ''}
                            {thread.last_message.content}
                          </div>
                        )}
                      </div>
                      <div style={{ textAlign: 'right', flexShrink: 0, marginLeft: 8 }}>
                        {thread.unread_count > 0 && (
                          <div style={{
                            background: 'var(--green)', color: '#000', fontWeight: 700,
                            fontSize: 10, padding: '2px 7px', borderRadius: 8, marginBottom: 4,
                            display: 'inline-block',
                          }}>
                            {thread.unread_count}
                          </div>
                        )}
                        <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                          {thread.last_message?.created_at
                            ? new Date(thread.last_message.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                            : ''}
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--green)', fontWeight: 600 }}>
                          R {thread.total_spent.toLocaleString()}
                        </div>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Chat area */}
          <div className="panel" style={{ overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            {selectedThread ? (
              <>
                {/* Chat header */}
                <div style={{
                  padding: '12px 18px', borderBottom: '1px solid var(--border)',
                  display: 'flex', alignItems: 'center', gap: 12,
                }}>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 14 }}>
                      {selectedThread.display_name || selectedThread.username}
                    </div>
                    <div className="muted-sm">
                      @{selectedThread.username} · {selectedThread.platform}
                    </div>
                  </div>
                  <div style={{ flex: 1 }} />
                  <span style={{
                    padding: '3px 10px', borderRadius: 10, fontSize: 11, fontWeight: 600,
                    background: selectedThread.fan_score > 50 ? 'rgba(217,251,113,0.15)' : 'var(--bg-card)',
                    color: selectedThread.fan_score > 50 ? 'var(--green)' : 'var(--text-muted)',
                  }}>
                    Score: {selectedThread.fan_score}
                  </span>
                  <span style={{
                    padding: '3px 10px', borderRadius: 10, fontSize: 11,
                    background: 'var(--bg-card)', color: 'var(--text-muted)',
                  }}>
                    R {selectedThread.total_spent.toLocaleString()} spent
                  </span>
                </div>

                {/* Messages */}
                <div style={{ flex: 1, overflow: 'auto', padding: 16 }}>
                  {messages.length === 0 && (
                    <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-muted)' }}>
                      <p style={{ fontSize: 24, marginBottom: 8 }}>💬</p>
                      <p>Start chatting as {mailbox.persona_name}</p>
                      <p style={{ fontSize: 12, marginTop: 4 }}>
                        {autoMode ? 'AI will reply in character' : 'You reply manually as the persona'}
                      </p>
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
                            <span style={{
                              fontSize: 9, background: 'rgba(0,0,0,0.1)', padding: '1px 4px', borderRadius: 4,
                            }}>
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
                        <span className="pulse-dot" style={{ width: 6, height: 6 }} /> {autoMode ? `${mailbox.persona_name} is typing...` : 'Sending...'}
                      </div>
                    </div>
                  )}
                  <div ref={messagesEndRef} />
                </div>

                {/* Input */}
                <div style={{
                  padding: '12px 18px', borderTop: '1px solid var(--border)',
                  display: 'flex', gap: 8,
                }}>
                  <input
                    value={inputText}
                    onChange={e => setInputText(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && handleSend()}
                    placeholder={autoMode ? `Message as fan (AI replies as ${mailbox.persona_name})...` : `Type as ${mailbox.persona_name}...`}
                    style={{
                      flex: 1, padding: '10px 14px', borderRadius: 8,
                      border: '1px solid var(--border)', background: 'var(--bg-card)',
                      color: 'var(--text)', fontSize: 13,
                    }}
                  />
                  <button
                    className="primary-button"
                    onClick={handleSend}
                    disabled={!inputText.trim() || sending}
                    style={{ padding: '10px 18px', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer' }}
                  >
                    {autoMode ? 'Send' : `Send as ${mailbox.persona_name}`}
                  </button>
                </div>
              </>
            ) : (
              <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>
                <div style={{ textAlign: 'center' }}>
                  <p style={{ fontSize: 32, marginBottom: 12 }}>💌</p>
                  <p style={{ fontSize: 14 }}>Select a conversation from {mailbox.persona_name}&apos;s inbox</p>
                  <p style={{ fontSize: 12, marginTop: 4 }}>Each fan gets their own thread with AI-powered replies</p>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </main>
  )
}
