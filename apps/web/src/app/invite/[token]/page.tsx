'use client'

import { useEffect, useState, use } from 'react'
import { getInvite, acceptInvite } from '@/lib/api'

interface Invite {
  id: string
  token: string
  email: string
  role: string
  invited_by: string
  status: string
  note: string
  expires_at: string | null
  accepted_at: string | null
  expired: boolean
  enforced: boolean
}

export default function InvitePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params)
  const [invite, setInvite] = useState<Invite | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [accepting, setAccepting] = useState(false)
  const [accepted, setAccepted] = useState(false)

  useEffect(() => {
    getInvite(token)
      .then((data: Invite) => {
        setInvite(data)
        if (data.status === 'accepted') setAccepted(true)
        if (data.expired) setError('This invite has expired — ask for a new link.')
        setLoading(false)
      })
      .catch((e: any) => {
        setError(e.message || 'This invite link is not valid.')
        setLoading(false)
      })
  }, [token])

  const handleAccept = async () => {
    setAccepting(true)
    try {
      await acceptInvite(token)
      setAccepted(true)
      setError('')
    } catch (e: any) {
      setError(e.message || 'Could not accept this invite.')
    }
    setAccepting(false)
  }

  return (
    <main className="workspace">
      <header className="topbar">
        <div className="crumb">
          <a href="/"><span>Persona Studio</span></a><b>/</b><strong>Invite</strong>
        </div>
      </header>
      <div className="content">
        {loading ? (
          <p className="muted-md">Checking invite…</p>
        ) : (
          <div className="panel" style={{ maxWidth: 560, margin: '40px auto', padding: 24 }}>
            <span className="kicker">Persona Studio</span>
            <h1 style={{ fontSize: 20, fontWeight: 600, margin: '6px 0 14px' }}>
              {error && !invite ? 'Invite unavailable' : accepted ? 'Invite accepted' : 'You have been invited'}
            </h1>

            {error && (
              <p style={{ color: '#EF4444', fontSize: 13, marginBottom: 16 }}>{error}</p>
            )}

            {invite && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 13, marginBottom: 18 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--text-muted)' }}>Invited</span>
                  <span style={{ fontWeight: 600 }}>{invite.email || '—'}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--text-muted)' }}>Role</span>
                  <span style={{ fontWeight: 600, textTransform: 'capitalize' }}>{invite.role}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--text-muted)' }}>From</span>
                  <span style={{ fontWeight: 600 }}>{invite.invited_by}</span>
                </div>
                {invite.expires_at && (
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color: 'var(--text-muted)' }}>Expires</span>
                    <span style={{ fontWeight: 600 }}>{new Date(invite.expires_at).toLocaleDateString()}</span>
                  </div>
                )}
              </div>
            )}

            {invite && !accepted && (
              <button className="primary-button" onClick={handleAccept} disabled={accepting || !!error}>
                {accepting ? '…' : 'Accept invite'}
              </button>
            )}

            {accepted && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <p className="muted-sm" style={{ margin: 0 }}>
                  Recorded. Now the honest part: this instance has <strong>no login yet</strong>, so
                  accepting did not unlock anything — you already had access to everything. This
                  records your acceptance for when an auth layer exists.
                </p>
                <a className="primary-button" href="/" style={{ alignSelf: 'flex-start', textDecoration: 'none' }}>
                  Open Persona Studio
                </a>
              </div>
            )}
          </div>
        )}
      </div>
    </main>
  )
}
