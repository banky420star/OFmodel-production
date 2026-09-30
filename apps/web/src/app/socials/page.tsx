'use client'

import { useEffect, useState } from 'react'
import { Toast, type ToastState } from '@/components/ui/Toast'
import {
  listSocialAccounts, requestSocialAccount, approveSocialAccount,
  rejectSocialAccount, activateSocialAccount, listPersonas,
  generateAccountEmail, checkAccountEmails,
  syncProfile, bulkSyncProfiles, storeCredentials,
  connectTikTok, tiktokStatus, tiktokSync, tiktokDisconnect,
  getSignupPacket,
} from '@/lib/api'

interface SocialAccount {
  id: string
  persona_id: string
  persona_name: string
  platform: string
  username: string
  display_name: string
  email: string
  profile_url: string
  bio: string
  status: string
  approval_notes: string
  approved_by: string
  approved_at: string | null
  rejected_at: string | null
  rejection_reason: string
  followers: number
  following: number
  posts_count: number
  api_connected: boolean
  created_at: string | null
}

interface Persona {
  id: string
  name: string
  status: string
  brand: string
}

const PLATFORMS = [
  { id: 'instagram', label: 'Instagram', icon: '📸', color: '#E1306C' },
  { id: 'facebook', label: 'Facebook', icon: '👤', color: '#1877F2' },
  { id: 'onlyfans', label: 'OnlyFans', icon: '🔐', color: '#00AFF0' },
  { id: 'tiktok', label: 'TikTok', icon: '🎵', color: '#000000' },
  { id: 'twitter', label: 'Twitter/X', icon: '🐦', color: '#1DA1F2' },
  { id: 'fanvue', label: 'Fanvue', icon: '💜', color: '#8B5CF6' },
  { id: 'fansly', label: 'Fansly', icon: '⭐', color: '#FF4D8D' },
]

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  draft: { bg: 'rgba(255,255,255,0.05)', color: 'var(--text-muted)' },
  pending_approval: { bg: 'rgba(251,191,36,0.12)', color: 'var(--amber)' },
  approved: { bg: 'rgba(217,251,113,0.12)', color: 'var(--green)' },
  active: { bg: 'rgba(217,251,113,0.15)', color: 'var(--green)' },
  rejected: { bg: 'rgba(239,68,68,0.12)', color: '#EF4444' },
  suspended: { bg: 'rgba(239,68,68,0.08)', color: '#EF4444' },
}

export default function SocialsPage() {
  const [accounts, setAccounts] = useState<SocialAccount[]>([])
  const [personas, setPersonas] = useState<Persona[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<'all' | 'pending' | 'active' | 'rejected'>('all')
  const [showRequestForm, setShowRequestForm] = useState(false)
  const [requesting, setRequesting] = useState(false)
  const [form, setForm] = useState({ persona_id: '', platform: 'instagram', username: '', display_name: '', email: '', bio: '' })
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [rejectingId, setRejectingId] = useState<string | null>(null)
  const [rejectReason, setRejectReason] = useState<Record<string, string>>({})
  const [toast, setToast] = useState<ToastState>(null)
  // Manual signup packet: everything a person needs to complete one platform
  // signup by hand. Read-only — see handleSignupPacket.
  const [packet, setPacket] = useState<any>(null)
  const [packetLoading, setPacketLoading] = useState<string | null>(null)
  const [copied, setCopied] = useState<string | null>(null)
  const [credUsername, setCredUsername] = useState('')
  const [savingCreds, setSavingCreds] = useState(false)
  const notify = (msg: string, type: 'success' | 'error' = 'success') => setToast({ msg, type })

  const refresh = () => {
    Promise.all([
      listSocialAccounts().catch(() => []),
      listPersonas().catch(() => []),
    ]).then(([accts, pers]: [any[], any[]]) => {
      setAccounts(accts)
      setPersonas(pers)
      setLoading(false)
    })
  }

  useEffect(() => { refresh() }, [])

  const filtered = accounts.filter(a => {
    if (filter === 'pending') return a.status === 'pending_approval'
    if (filter === 'active') return a.status === 'active' || a.status === 'approved'
    if (filter === 'rejected') return a.status === 'rejected'
    return true
  })

  const handleRequest = async () => {
    if (!form.persona_id || !form.username.trim()) return
    setRequesting(true)
    try {
      await requestSocialAccount(form)
      setForm({ persona_id: '', platform: 'instagram', username: '', display_name: '', email: '', bio: '' })
      setShowRequestForm(false)
      refresh()
    } catch (e: any) {
      notify(e.message || 'Failed to submit request', 'error')
    }
    setRequesting(false)
  }

  const handleApprove = async (id: string) => {
    setActionLoading(id)
    try {
      await approveSocialAccount(id, 'Approved — ready to activate')
      notify('Account approved')
      refresh()
    } catch (e: any) {
      notify(e.message, 'error')
    }
    setActionLoading(null)
  }

  const handleReject = async (id: string) => {
    const reason = rejectReason[id]?.trim()
    if (!reason) return
    setActionLoading(id)
    try {
      await rejectSocialAccount(id, reason)
      notify('Account rejected')
      setRejectReason(prev => ({ ...prev, [id]: '' }))
      setRejectingId(null)
      refresh()
    } catch (e: any) {
      notify(e.message, 'error')
    }
    setActionLoading(null)
  }

  const handleActivate = async (id: string) => {
    setActionLoading(id)
    try {
      await activateSocialAccount(id)
      notify('Account activated')
      refresh()
    } catch (e: any) {
      notify(e.message, 'error')
    }
    setActionLoading(null)
  }

  // ── Manual signup ────────────────────────────────────────────────
  // Fetches the packet (username, password, bio, signup URL, inbox state) so a
  // person can complete the platform's own signup form themselves. This calls
  // the local API only — nothing here creates an account on the platform, and
  // the account's status does not change: approve/activate stay separate,
  // deliberate clicks for after the account really exists.
  const handleSignupPacket = async (id: string) => {
    setPacketLoading(id)
    try {
      const p = await getSignupPacket(id)
      setPacket(p)
      setCredUsername((p as any).username || '')
    } catch (e: any) {
      notify(e.message || 'Could not build signup packet', 'error')
    }
    setPacketLoading(null)
  }

  // Records what the operator actually registered, which is what clears the
  // roster's missing-username/password blockers. The packet's own last step has
  // always told the operator to save the credentials here; until now there was
  // no control on this page that did it, and `storeCredentials` sat imported
  // and uncalled.
  const handleSaveCredentials = async () => {
    if (!packet) return
    setSavingCreds(true)
    try {
      await storeCredentials(packet.account_id, packet.password, credUsername)
      notify(`Credentials saved for ${packet.platform}`)
      setPacket(null)
      refresh()
    } catch (e: any) {
      notify(e.message || 'Could not save credentials', 'error')
    } finally {
      setSavingCreds(false)
    }
  }

  const copyField = async (label: string, value: string) => {
    if (!value) return
    try {
      await navigator.clipboard.writeText(value)
      setCopied(label)
      setTimeout(() => setCopied(null), 1500)
    } catch {
      // Clipboard needs a secure context; every field is selectable anyway.
      notify('Clipboard unavailable — select the text and copy manually', 'error')
    }
  }

  // Automated account creation is not wired here, on purpose. There used to be a
  // "Sign Up" button on this page that POSTed /social-accounts/{id}/auto-signup
  // with headless=true — a Playwright run that filled and submitted the real
  // platform's signup form with an anti-automation-detection profile and a
  // disposable-inbox email-code bypass. That is a different act from the packet
  // above: the packet hands a human the details and the human submits the form,
  // while auto-signup has software create the account. It is not something this
  // UI should offer, and it also made the app's own copy untrue — both the packet
  // note and the manager roster note state that nothing here creates a platform
  // account. A "Save credentials" control for the packet is the supported path.

  // ── TikTok — Login Kit OAuth, read-only Display API ───────────────
  // Only reads this account's own profile + videos. Nothing here posts,
  // follows, or likes on the account's behalf.
  const handleTikTokConnect = async (id: string) => {
    setActionLoading(id)
    try {
      const { authorize_url } = await connectTikTok(id)
      // Full-page navigation: TikTok has to render its own consent screen.
      // If this throws 503 it's the honest "set TIKTOK_CLIENT_KEY" error.
      window.location.href = authorize_url
    } catch (e: any) {
      notify(e.message || 'Could not start TikTok authorization', 'error')
      setActionLoading(null)
    }
  }

  const handleTikTokSync = async (id: string) => {
    setActionLoading(id)
    try {
      const res: any = await tiktokSync(id)
      notify(
        `Synced @${res.profile.display_name || 'account'} — ` +
        `${res.profile.followers.toLocaleString()} followers, ` +
        `${res.metrics.total_views.toLocaleString()} views across ${res.recent_videos.length} videos`
      )
      refresh()
    } catch (e: any) {
      notify(e.message, 'error')
    }
    setActionLoading(null)
  }

  const handleTikTokCheck = async (id: string) => {
    setActionLoading(id)
    try {
      const s: any = await tiktokStatus(id)
      notify(
        s.ok
          ? `Token live — @${s.display_name} (${(s.followers || 0).toLocaleString()} followers)`
          : `Token not usable: ${s.detail || 'unknown error'}`,
        s.ok ? 'success' : 'error'
      )
    } catch (e: any) {
      notify(e.message, 'error')
    }
    setActionLoading(null)
  }

  const handleTikTokDisconnect = async (id: string) => {
    setActionLoading(id)
    try {
      await tiktokDisconnect(id)
      notify('TikTok authorization removed — reconnect to sync again')
      refresh()
    } catch (e: any) {
      notify(e.message, 'error')
    }
    setActionLoading(null)
  }

  // Stats
  const pending = accounts.filter(a => a.status === 'pending_approval').length
  const active = accounts.filter(a => a.status === 'active' || a.status === 'approved').length
  const totalFollowers = accounts.reduce((s, a) => s + (a.followers || 0), 0)

  // Platform breakdown
  const platformCounts = PLATFORMS.map(p => ({
    ...p,
    count: accounts.filter(a => a.platform === p.id).length,
    active: accounts.filter(a => a.platform === p.id && (a.status === 'active' || a.status === 'approved')).length,
  }))

  return (
    <main className="workspace">
      <header className="topbar">
        <div className="crumb">
          <a href="/"><span>Persona Studio</span></a><b>/</b><strong>Social Accounts</strong>
        </div>
      </header>
      <div className="content">
        <section className="page-heading">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div>
              <h1>Social Accounts</h1>
              <p>Sign up models to social platforms. All accounts require approval before going live.</p>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                onClick={async () => {
                  try {
                    const r: any = await bulkSyncProfiles()
                    notify(`Synced ${r.synced} of ${r.total} active accounts`)
                  } catch (e: any) { notify(e.message, 'error') }
                }}
                style={{
                  padding: '10px 18px', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer',
                  border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)',
                }}
              >
                Sync All Profiles
              </button>
              <button
                className="primary-button"
                onClick={() => setShowRequestForm(!showRequestForm)}
                style={{ padding: '10px 18px', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer', flexShrink: 0 }}
              >
                + Request Account
              </button>
            </div>
          </div>
        </section>

        {/* Stats */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
          {[
            { label: 'Total Accounts', value: accounts.length, color: 'var(--text)' },
            { label: 'Pending Approval', value: pending, color: pending > 0 ? 'var(--amber)' : 'var(--text-muted)' },
            { label: 'Active', value: active, color: 'var(--green)' },
            { label: 'Total Followers', value: totalFollowers.toLocaleString(), color: 'var(--text)' },
          ].map((stat, i) => (
            <div key={i} className="panel" style={{ padding: '14px 18px' }}>
              <div className="muted-sm" style={{ marginBottom: 4 }}>{stat.label}</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: stat.color }}>{stat.value}</div>
            </div>
          ))}
        </div>

        {/* Platform overview */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 20, flexWrap: 'wrap' }}>
          {platformCounts.map(p => (
            <div key={p.id} className="panel" style={{
              padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 8,
              minWidth: 120, border: `1px solid ${p.active > 0 ? p.color : 'var(--border)'}`,
            }}>
              <span style={{ fontSize: 16 }}>{p.icon}</span>
              <div>
                <div style={{ fontSize: 12, fontWeight: 600 }}>{p.label}</div>
                <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  {p.active}/{p.count} live
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Request form */}
        {showRequestForm && (
          <div className="panel" style={{ padding: 20, marginBottom: 20, border: '1px solid var(--green)' }}>
            <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 16 }}>Request New Social Account</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div>
                <label className="muted-sm" style={{ display: 'block', marginBottom: 4 }}>Model</label>
                <select
                  value={form.persona_id}
                  onChange={e => setForm(f => ({ ...f, persona_id: e.target.value }))}
                  style={{
                    width: '100%', padding: '8px 12px', borderRadius: 6,
                    border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)', fontSize: 13,
                  }}
                >
                  <option value="">Select a model...</option>
                  {personas.filter(p => p.status === 'active' || p.status === 'ready').map(p => (
                    <option key={p.id} value={p.id}>{p.name} — {p.brand || 'content creator'}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="muted-sm" style={{ display: 'block', marginBottom: 4 }}>Platform</label>
                <select
                  value={form.platform}
                  onChange={e => setForm(f => ({ ...f, platform: e.target.value }))}
                  style={{
                    width: '100%', padding: '8px 12px', borderRadius: 6,
                    border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)', fontSize: 13,
                  }}
                >
                  {PLATFORMS.map(p => (
                    <option key={p.id} value={p.id}>{p.icon} {p.label}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="muted-sm" style={{ display: 'block', marginBottom: 4 }}>Username</label>
                <input
                  value={form.username}
                  onChange={e => setForm(f => ({ ...f, username: e.target.value }))}
                  placeholder="@username"
                  style={{
                    width: '100%', padding: '8px 12px', borderRadius: 6,
                    border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)', fontSize: 13,
                  }}
                />
              </div>
              <div>
                <label className="muted-sm" style={{ display: 'block', marginBottom: 4 }}>Display Name</label>
                <input
                  value={form.display_name}
                  onChange={e => setForm(f => ({ ...f, display_name: e.target.value }))}
                  placeholder="Display name"
                  style={{
                    width: '100%', padding: '8px 12px', borderRadius: 6,
                    border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)', fontSize: 13,
                  }}
                />
              </div>
              <div>
                <label className="muted-sm" style={{ display: 'block', marginBottom: 4 }}>Signup Email</label>
                <input
                  type="email"
                  value={form.email}
                  onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
                  placeholder="email@example.com"
                  style={{
                    width: '100%', padding: '8px 12px', borderRadius: 6,
                    border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)', fontSize: 13,
                  }}
                />
              </div>
              <div>
                <label className="muted-sm" style={{ display: 'block', marginBottom: 4 }}>Bio</label>
                <input
                  value={form.bio}
                  onChange={e => setForm(f => ({ ...f, bio: e.target.value }))}
                  placeholder="Profile bio"
                  style={{
                    width: '100%', padding: '8px 12px', borderRadius: 6,
                    border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)', fontSize: 13,
                  }}
                />
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
              <button
                className="primary-button"
                onClick={handleRequest}
                disabled={!form.persona_id || !form.username.trim() || requesting}
                style={{ padding: '8px 16px', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer' }}
              >
                {requesting ? 'Submitting...' : 'Submit for Approval'}
              </button>
              <button
                onClick={() => setShowRequestForm(false)}
                style={{
                  padding: '8px 16px', borderRadius: 6, fontSize: 13, cursor: 'pointer',
                  border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-muted)',
                }}
              >
                Cancel
              </button>
            </div>
            <div style={{ marginTop: 12, padding: '10px 14px', borderRadius: 6, background: 'rgba(251,191,36,0.08)', border: '1px solid rgba(251,191,36,0.2)' }}>
              <p style={{ fontSize: 12, color: 'var(--amber)' }}>
                ⚠️ All account requests require operator approval before the signup process begins. You&apos;ll be notified when the account is ready to activate.
              </p>
            </div>
          </div>
        )}

        {/* Filter tabs */}
        <div style={{ display: 'flex', gap: 4, marginBottom: 16 }}>
          {(['all', 'pending', 'active', 'rejected'] as const).map(f => (
            <button key={f} onClick={() => setFilter(f)} style={{
              padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
              border: `1px solid ${filter === f ? 'var(--green)' : 'var(--border)'}`,
              background: filter === f ? 'rgba(217,251,113,0.1)' : 'transparent',
              color: filter === f ? 'var(--green)' : 'var(--text-muted)',
            }}>
              {f === 'all' ? 'All' : f === 'pending' ? `Pending (${pending})` : f === 'active' ? `Active (${active})` : 'Rejected'}
            </button>
          ))}
        </div>

        {/* Accounts list */}
        {loading ? (
          <p className="muted-md" style={{ padding: 16 }}>Loading accounts...</p>
        ) : filtered.length === 0 ? (
          <div className="panel" style={{ textAlign: 'center', padding: 48 }}>
            <p style={{ fontSize: 32, marginBottom: 12 }}>📱</p>
            <p style={{ fontSize: 14, color: 'var(--text-muted)' }}>
              {filter === 'all' ? 'No social accounts yet. Request one to get started.' : `No ${filter} accounts.`}
            </p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {filtered.map(account => {
              const sc = STATUS_COLORS[account.status] || STATUS_COLORS.draft
              const platform = PLATFORMS.find(p => p.id === account.platform)
              return (
                <div key={account.id} className="panel" style={{
                  padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 16,
                  border: `1px solid ${account.status === 'pending_approval' ? 'rgba(251,191,36,0.3)' : 'var(--border)'}`,
                }}>
                  {/* Platform icon */}
                  <div style={{
                    width: 40, height: 40, borderRadius: 10, display: 'flex', alignItems: 'center',
                    justifyContent: 'center', fontSize: 20, flexShrink: 0,
                    background: `${platform?.color || '#666'}15`,
                  }}>
                    {platform?.icon || '📱'}
                  </div>

                  {/* Account info */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
                      <span style={{ fontWeight: 600, fontSize: 14 }}>@{account.username}</span>
                      <span className="muted-sm" style={{ textTransform: 'capitalize' }}>{account.platform}</span>
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                      {account.persona_name} · {account.display_name || account.username}
                    </div>
                    {account.bio && (
                      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 400 }}>
                        {account.bio}
                      </div>
                    )}
                    {account.rejection_reason && (
                      <div style={{ fontSize: 11, color: '#EF4444', marginTop: 2 }}>
                        Rejected: {account.rejection_reason}
                      </div>
                    )}
                  </div>

                  {/* Stats */}
                  <div style={{ display: 'flex', gap: 16, flexShrink: 0 }}>
                    {account.followers > 0 && (
                      <div style={{ textAlign: 'center' }}>
                        <div style={{ fontSize: 14, fontWeight: 700 }}>{account.followers.toLocaleString()}</div>
                        <div className="muted-sm">followers</div>
                      </div>
                    )}
                    {account.posts_count > 0 && (
                      <div style={{ textAlign: 'center' }}>
                        <div style={{ fontSize: 14, fontWeight: 700 }}>{account.posts_count}</div>
                        <div className="muted-sm">posts</div>
                      </div>
                    )}
                  </div>

                  {/* Status badge */}
                  <span style={{
                    padding: '3px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600,
                    background: sc.bg, color: sc.color, textTransform: 'uppercase', letterSpacing: '.03em',
                    flexShrink: 0,
                  }}>
                    {account.status.replace('_', ' ')}
                  </span>

                  {/* Email badge */}
                  {account.email && (
                    <div style={{
                      padding: '3px 8px', borderRadius: 4, fontSize: 11,
                      background: 'rgba(99,102,241,0.1)', color: '#818CF8',
                      fontFamily: 'monospace', flexShrink: 0,
                    }}>
                      ✉ {account.email}
                    </div>
                  )}

                  {/* Sync badge */}
                  {(account.status === 'active' || account.status === 'approved') && (
                    <div style={{
                      padding: '3px 8px', borderRadius: 4, fontSize: 11,
                      background: account.api_connected ? 'rgba(217,251,113,0.12)' : 'rgba(255,255,255,0.05)',
                      color: account.api_connected ? 'var(--green)' : 'var(--text-muted)',
                    }}>
                      {account.api_connected ? '🔗 API Connected' : '🔗 No API'}
                    </div>
                  )}

                  {/* Actions */}
                  <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                    {/* TikTok — real OAuth (Login Kit / Display API), read-only */}
                    {account.platform === 'tiktok' && (account.status === 'active' || account.status === 'approved') && (
                      account.api_connected ? (
                        <>
                          <button
                            onClick={() => handleTikTokSync(account.id)}
                            disabled={actionLoading === account.id}
                            title="Pull real followers + video stats from the TikTok Display API"
                            style={{
                              padding: '6px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                              background: 'rgba(99,102,241,0.12)', color: '#818CF8', border: 'none',
                            }}
                          >
                            {actionLoading === account.id ? '...' : 'Sync TikTok'}
                          </button>
                          <button
                            onClick={() => handleTikTokCheck(account.id)}
                            disabled={actionLoading === account.id}
                            title="Verify the stored token still reads this account"
                            style={{
                              padding: '6px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                              background: 'rgba(255,255,255,0.05)', color: 'var(--text-muted)', border: 'none',
                            }}
                          >
                            Check
                          </button>
                          <button
                            onClick={() => handleTikTokDisconnect(account.id)}
                            disabled={actionLoading === account.id}
                            title="Drop the stored TikTok authorization"
                            style={{
                              padding: '6px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                              background: 'rgba(239,68,68,0.12)', color: '#EF4444', border: 'none',
                            }}
                          >
                            Disconnect
                          </button>
                        </>
                      ) : (
                        <button
                          onClick={() => handleTikTokConnect(account.id)}
                          disabled={actionLoading === account.id}
                          title="Authorize Persona Studio to read this account's TikTok stats"
                          style={{
                            padding: '6px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                            background: 'var(--green)', color: '#000', border: 'none',
                          }}
                        >
                          {actionLoading === account.id ? '...' : '🎵 Connect TikTok'}
                        </button>
                      )
                    )}
                    {!account.email && account.status !== 'rejected' && (
                      <button
                        onClick={async () => {
                          setActionLoading(account.id)
                          try {
                            await generateAccountEmail(account.id)
                            notify('New inbox created — email address assigned')
                            refresh()
                          } catch (e: any) { notify(e.message, 'error') }
                          setActionLoading(null)
                        }}
                        disabled={actionLoading === account.id}
                        style={{
                          padding: '6px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                          background: 'rgba(99,102,241,0.12)', color: '#818CF8', border: 'none',
                        }}
                      >
                        {actionLoading === account.id ? '...' : 'Get Email'}
                      </button>
                    )}
                    {account.email && (
                      <button
                        onClick={async () => {
                          setActionLoading(account.id)
                          try {
                            const result: any = await checkAccountEmails(account.id)
                            if (result.count > 0) {
                              const subjects = result.emails.map((e: any) => e.subject).join(', ')
                              notify(`${result.count} email(s): ${subjects}`)
                            } else {
                              notify('Inbox empty — check again after signing up on the platform', 'error')
                            }
                          } catch (e: any) { notify(e.message, 'error') }
                          setActionLoading(null)
                        }}
                        disabled={actionLoading === account.id}
                        style={{
                          padding: '6px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                          background: 'rgba(99,102,241,0.12)', color: '#818CF8', border: 'none',
                        }}
                      >
                        Check Inbox
                      </button>
                    )}
                    {/* Manual signup path — hands a person everything needed
                        to complete the platform's own form. The app never
                        contacts the platform for this. */}
                    {['pending_approval', 'approved'].includes(account.status) && (
                      <button
                        onClick={() => handleSignupPacket(account.id)}
                        disabled={packetLoading === account.id}
                        style={{
                          padding: '6px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                          background: 'rgba(129,140,248,0.12)', color: '#A5B4FC', border: 'none',
                        }}
                      >
                        {packetLoading === account.id ? '...' : '📋 Signup packet'}
                      </button>
                    )}
                    {account.status === 'pending_approval' && (
                      <>
                        <button
                          onClick={() => handleApprove(account.id)}
                          disabled={actionLoading === account.id}
                          style={{
                            padding: '6px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                            background: 'var(--green)', color: '#000', border: 'none',
                          }}
                        >
                          {actionLoading === account.id ? '...' : 'Approve'}
                        </button>
                        {rejectingId === account.id && (
                          <input
                            autoFocus
                            placeholder="Rejection reason…"
                            value={rejectReason[account.id] || ''}
                            onChange={e => setRejectReason(prev => ({ ...prev, [account.id]: e.target.value }))}
                            onKeyDown={e => { if (e.key === 'Enter') handleReject(account.id); if (e.key === 'Escape') setRejectingId(null) }}
                            style={{
                              padding: '6px 10px', borderRadius: 6, fontSize: 12, width: 170,
                              background: 'var(--bg-card)', color: 'var(--text)', border: '1px solid var(--border)', outline: 'none',
                            }}
                          />
                        )}
                        <button
                          onClick={() => rejectingId === account.id ? handleReject(account.id) : setRejectingId(account.id)}
                          disabled={actionLoading === account.id}
                          style={{
                            padding: '6px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                            background: 'rgba(239,68,68,0.12)', color: '#EF4444', border: 'none',
                          }}
                        >
                          {rejectingId === account.id ? 'Confirm' : 'Reject'}
                        </button>
                      </>
                    )}
                    {account.status === 'approved' && (
                      <button
                        onClick={() => handleActivate(account.id)}
                        disabled={actionLoading === account.id}
                        style={{
                          padding: '6px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                          background: 'var(--green)', color: '#000', border: 'none',
                        }}
                      >
                        {actionLoading === account.id ? '...' : 'Activate'}
                      </button>
                    )}
                    {account.status === 'active' && (
                      <button
                        onClick={async () => {
                          setActionLoading(account.id)
                          try {
                            await syncProfile(account.id)
                            notify('Profile synced from platform')
                            refresh()
                          } catch (e: any) { notify(e.message, 'error') }
                          setActionLoading(null)
                        }}
                        disabled={actionLoading === account.id}
                        style={{
                          padding: '6px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                          background: 'rgba(99,102,241,0.12)', color: '#818CF8', border: 'none',
                        }}
                      >
                        {actionLoading === account.id ? '...' : 'Sync Profile'}
                      </button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
      {/* Manual signup packet. A person opens the platform's own signup page
          and fills it in; nothing in this dialog contacts the platform, and
          the account is not marked signed-up here. */}
      {packet && (
        <div
          onClick={() => setPacket(null)}
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 50,
            display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
          }}
        >
          <div
            onClick={e => e.stopPropagation()}
            style={{
              background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 12,
              padding: 22, width: '100%', maxWidth: 580, maxHeight: '85vh', overflowY: 'auto',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 4 }}>
              <div>
                <div style={{ fontSize: 16, fontWeight: 700 }}>
                  Signup packet — {packet.platform}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
                  for {packet.persona_name} · account status stays {packet.status}
                </div>
              </div>
              <button
                onClick={() => setPacket(null)}
                style={{ background: 'none', border: 'none', color: 'var(--text-muted)', fontSize: 18, cursor: 'pointer', lineHeight: 1 }}
              >
                ×
              </button>
            </div>

            <div style={{ fontSize: 12, color: 'var(--text-muted)', margin: '14px 0 10px' }}>
              Someone opens the signup page below and fills the form in by hand.
            </div>

            {([
              ['Username', packet.username],
              ['Display name', packet.display_name],
              ['Bio', packet.bio],
              ['Email', packet.email],
              ['Email password', packet.email_password],
              ['Platform password', packet.password],
            ] as [string, string][]).map(([label, value]) => (
              <div key={label} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
                <span style={{ width: 124, fontSize: 12, color: 'var(--text-muted)', flexShrink: 0 }}>{label}</span>
                <code style={{
                  flex: 1, fontSize: 12, background: 'rgba(255,255,255,0.04)', padding: '6px 8px',
                  borderRadius: 6, overflowWrap: 'anywhere',
                }}>
                  {value ? String(value) : '— none yet'}
                </code>
                <button
                  onClick={() => copyField(label, String(value || ''))}
                  disabled={!value}
                  style={{
                    padding: '5px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600, flexShrink: 0,
                    cursor: value ? 'pointer' : 'not-allowed', opacity: value ? 1 : 0.4,
                    background: 'rgba(99,102,241,0.12)', color: '#818CF8', border: 'none',
                  }}
                >
                  {copied === label ? '✓' : 'Copy'}
                </button>
              </div>
            ))}

            <a
              href={packet.signup_url}
              target="_blank"
              rel="noreferrer"
              style={{
                display: 'block', textAlign: 'center', marginTop: 16, padding: '10px 0',
                borderRadius: 8, background: 'var(--green)', color: '#000',
                fontSize: 13, fontWeight: 700, textDecoration: 'none',
              }}
            >
              Open {packet.platform} signup page ↗
            </a>

            <ol style={{ margin: '16px 0 0', paddingLeft: 20, fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.7 }}>
              {(packet.steps || []).map((s: string, i: number) => <li key={i}>{s}</li>)}
            </ol>

            <div style={{
              display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap',
              padding: '10px 12px', marginTop: 14, borderRadius: 8,
              background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)',
            }}>
              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                Account created? Record the handle you registered:
              </span>
              <input
                value={credUsername}
                onChange={e => setCredUsername(e.target.value)}
                placeholder={packet.username || 'username'}
                style={{ flex: 1, minWidth: 140, fontSize: 12, padding: '5px 9px', borderRadius: 6, background: 'var(--bg-card)', color: 'var(--text)', border: '1px solid var(--border)', outline: 'none' }}
              />
              <button
                onClick={handleSaveCredentials}
                disabled={savingCreds || !packet.password}
                style={{
                  padding: '5px 12px', borderRadius: 6, fontSize: 11, fontWeight: 600,
                  cursor: savingCreds ? 'wait' : 'pointer', border: 'none',
                  background: 'var(--green)', color: '#000', opacity: savingCreds || !packet.password ? .5 : 1,
                }}
              >
                {savingCreds ? 'Saving…' : 'Save credentials'}
              </button>
            </div>

            {packet.note && (
              <div style={{
                marginTop: 14, padding: 10, borderRadius: 8, fontSize: 11, lineHeight: 1.6,
                background: 'rgba(251,191,36,0.08)', color: 'var(--amber)',
              }}>
                {packet.note}
              </div>
            )}
          </div>
        </div>
      )}
      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </main>
  )
}
