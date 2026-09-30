'use client'

import { useEffect, useRef, useState } from 'react'
import {
  FanApiError, getPersona, getThread, money, sendMessage, getWallet,
  type FanPersona, type ThreadMessage,
} from '@/lib/fanApi'

// The chat.
//
// Three things this screen has to get right, and they are all about not
// pretending:
//
//   1. The AI badge and the disclosure line are visible at rest, not tucked
//      into a menu.
//   2. When no model can answer, the fan is told — in the transcript, in the
//      same place the reply would have been. It is not a toast that scrolls
//      away, and it is certainly not a placeholder line.
//   3. A pending reply is a real pending state. The typing indicator means a
//      request is in flight, and it comes down when the request finishes —
//      success or failure.

export default function FanChatPage() {
  const [persona, setPersona] = useState<FanPersona | null>(null)
  const [messages, setMessages] = useState<ThreadMessage[]>([])
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [balance, setBalance] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    Promise.all([getPersona(), getThread(), getWallet()])
      .then(([who, thread, wallet]) => {
        setPersona(who)
        setMessages(thread.messages)
        setBalance(wallet.balance_minor)
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load the thread.'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, sending])

  async function send(event: React.FormEvent) {
    event.preventDefault()
    const text = draft.trim()
    if (!text || sending) return

    setDraft('')
    setError('')
    setSending(true)

    // Optimistic, but only for the fan's own message — her reply is never
    // rendered before it exists.
    const optimistic: ThreadMessage = {
      id: `local-${Date.now()}`,
      direction: 'inbound',
      content: text,
      intent: '',
      served_by: '',
      created_at: new Date().toISOString(),
    }
    setMessages((prev) => [...prev, optimistic])

    try {
      const reply = await sendMessage(text)
      setMessages((prev) => [
        ...prev,
        {
          id: `reply-${reply.created_at}`,
          direction: 'outbound',
          content: reply.reply,
          intent: reply.intent,
          served_by: reply.served_by,
          created_at: reply.created_at,
        },
      ])
    } catch (err) {
      if (err instanceof FanApiError && err.status === 503) {
        // The honest failure. Her message bubble is not filled in, and the fan
        // is told why rather than left reading a blank reply.
        setError(
          "She can't reply right now — no language model is available. " +
          'Your message was not sent. Try again in a moment.',
        )
        // The optimistic bubble is withdrawn: nothing was recorded server-side,
        // so leaving it on screen would show a message that does not exist.
        setMessages((prev) => prev.filter((m) => m.id !== optimistic.id))
        setDraft(text)
      } else {
        setError(err instanceof Error ? err.message : 'Could not send.')
        setMessages((prev) => prev.filter((m) => m.id !== optimistic.id))
        setDraft(text)
      }
    } finally {
      setSending(false)
    }
  }

  if (loading) {
    return <div className="fan-center"><div className="fan-spinner" /></div>
  }

  return (
    <div className="fan-chat">
      <div className="fan-chat-head">
        <div>
          <b>{persona?.name}</b>
          <small>
            {persona?.brand}
            {balance !== null && <> · {money(balance)} left</>}
          </small>
        </div>
        <span className="fan-ai-tag">AI</span>
      </div>

      {/* Always visible, never dismissible. This is the disclosure. */}
      <p className="fan-disclosure">{persona?.disclosure}</p>

      <div className="fan-thread">
        {messages.length === 0 && !sending && (
          <p className="fan-thread-empty">
            Say hello. She is an AI and she knows it — ask her anything about
            that, too.
          </p>
        )}

        {messages.map((message) => (
          <div
            key={message.id}
            className={`fan-bubble ${message.direction === 'outbound' ? 'her' : 'you'}`}
          >
            {message.content}
          </div>
        ))}

        {sending && (
          <div className="fan-bubble her fan-typing" aria-label="She is replying">
            <span /><span /><span />
          </div>
        )}

        {error && <p className="fan-chat-error">{error}</p>}
        <div ref={bottom} />
      </div>

      <form className="fan-composer" onSubmit={send}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Message her…"
          maxLength={4000}
          disabled={sending}
        />
        <button type="submit" className="fan-btn-primary" disabled={sending || !draft.trim()}>
          Send
        </button>
      </form>
    </div>
  )
}
