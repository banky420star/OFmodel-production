'use client'

import { useEffect, useState } from 'react'
import { getDashboardSummary, getSystemProviders, listInvites, createInvite, revokeInvite } from '@/lib/api'
import type { DashboardSummary } from '@/lib/types'
import { Icons } from '@/lib/icons'

export default function SettingsPage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [providerRows, setProviderRows] = useState<any[]>([])
  const [requiredCaps, setRequiredCaps] = useState<string[]>([])
  // The adult-content gate is not a capability and does not fail a self-check,
  // but its two layers can disagree and one of them (the provider's own
  // SUPPORTS_ADULT declaration) is a claim nothing verifies. Show it, so an
  // operator reads its state instead of inferring it from output later.
  const [adultGate, setAdultGate] = useState<any | null>(null)
  const [invites, setInvites] = useState<any[]>([])
  const [inviteForm, setInviteForm] = useState({ email: '', role: 'viewer', note: '' })
  const [inviting, setInviting] = useState(false)
  const [inviteNotice, setInviteNotice] = useState('')

  useEffect(() => {
    Promise.all([getDashboardSummary(), getSystemProviders()])
      .then(([data, providerData]) => {
        setSummary(data)
        setProviderRows(providerData.capabilities || [])
        setRequiredCaps(providerData.required || [])
        setAdultGate(providerData.gates?.adult || null)
        setLoading(false)
      })
      .catch(() => setLoading(false))
    listInvites().then(setInvites).catch(() => {})
  }, [])

  const handleCreateInvite = async () => {
    setInviting(true)
    setInviteNotice('')
    try {
      const created: any = await createInvite(inviteForm)
      setInviteNotice(created.accept_url || '')
      setInviteForm({ email: '', role: 'viewer', note: '' })
      setInvites(await listInvites())
    } catch (e: any) {
      setInviteNotice(`Error: ${e.message}`)
    }
    setInviting(false)
  }

  const handleRevoke = async (id: string) => {
    try {
      await revokeInvite(id)
      setInvites(await listInvites())
    } catch (e: any) {
      setInviteNotice(`Error: ${e.message}`)
    }
  }

  const providers = [
    { name: 'LLM', key: 'llm', description: 'Ollama — local language model for identity generation, shoot planning, QA' },
    { name: 'Image', key: 'image', description: 'ComfyUI — local identity-locked image generation on Apple Silicon' },
    { name: 'Video', key: 'video', description: 'Wan-compatible server — local video generation when configured' },
    { name: 'Voice', key: 'voice', description: 'macOS say + ffmpeg — local synthesis without voice cloning' },
    { name: 'Trainer', key: 'trainer', description: 'HuggingFace LoRA trainer — local MPS/CUDA training' },
    { name: 'Storage', key: 'storage', description: 'Local filesystem — files saved to hard drive' },
    { name: 'Instagram', key: 'instagram', description: 'Instagram Graph API — real follower + engagement analytics (optional)' },
    { name: 'TikTok', key: 'tiktok', description: 'TikTok Login Kit + Display API — read-only stats for a connected account (optional)' },
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
                // Instagram/TikTok are optional capabilities — an unconfigured one
                // is not "Offline", it's simply not set up. Say which it is.
                const optional = requiredCaps.length > 0 && !requiredCaps.includes(p.key)
                const label = status === 'green'
                  ? 'Online'
                  : optional ? 'Optional' : status === 'yellow' ? 'Degraded' : 'Offline'
                const dotColor = status === 'green'
                  ? 'var(--green)'
                  : optional ? 'var(--text-muted)' : status === 'yellow' ? '#fbbf24' : '#ef4444'
                return (
                  <div key={p.key} className="panel" style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 16 }}>
                    <span style={{
                      width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                      background: dotColor,
                    }} />
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 600, fontSize: 13 }}>{p.name}</div>
                      <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{p.description}</div>
                    </div>
                    <span style={{
                      fontSize: 11, fontWeight: 600, textTransform: 'uppercase',
                      color: optional && status !== 'green' ? 'var(--text-muted)' : dotColor,
                    }}>
                      {label}
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

            <div className="panel" style={{ marginBottom: 32 }}>
              <div className="panel-title">
                <div>
                  <span className="kicker">Content gates</span>
                  <h3>Adult content</h3>
                </div>
                <span
                  className="count-badge"
                  style={adultGate?.allowed
                    ? { background: 'var(--blue-dim)', color: 'var(--blue)' }
                    : { background: 'var(--bg-card)', color: 'var(--text-muted)' }}
                >
                  {adultGate ? (adultGate.allowed ? 'ALLOWED' : 'BLOCKED') : '—'}
                </span>
              </div>
              {/* Two layers have to agree, and the second one is a declaration
                  rather than a detection: ComfyUI loads any weights, so
                  COMFYUI_ADULT_CHECKPOINT=true means "an adult-tuned checkpoint
                  is what is loaded" and nothing here checks that. Listing the
                  blockers — instead of a single on/off light — is what makes a
                  false declaration visible before it shows up as bad output. */}
              {adultGate ? (
                <>
                  <p className="muted-sm" style={{ maxWidth: 720, marginBottom: 12 }}>
                    Both layers must agree before adult content is produced: the global kill
                    switch, and the configured image provider declaring adult support. Turning
                    the checkpoint flag on is an assertion about what is loaded, not a detection
                    — the app cannot verify it for you.
                  </p>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
                    <span className={`status ${adultGate.adult_content_enabled ? 'completed' : 'pending'}`} style={{ textTransform: 'none' }}>
                      ADULT_CONTENT_ENABLED = {String(adultGate.adult_content_enabled)}
                    </span>
                    <span className={`status ${adultGate.image_provider_supports_adult ? 'completed' : 'pending'}`} style={{ textTransform: 'none' }}>
                      {adultGate.image_provider} SUPPORTS_ADULT = {String(adultGate.image_provider_supports_adult)}
                    </span>
                    {adultGate.checkpoint && (
                      <span className="status" style={{ textTransform: 'none' }}>
                        checkpoint: {adultGate.checkpoint}
                      </span>
                    )}
                  </div>
                  {adultGate.blockers?.length > 0 ? (
                    <ul className="muted-sm" style={{ margin: 0, paddingLeft: 18 }}>
                      {adultGate.blockers.map((b: string, i: number) => <li key={i}>{b}</li>)}
                    </ul>
                  ) : (
                    <p className="muted-sm" style={{ margin: 0 }}>No blockers — both layers are on.</p>
                  )}
                </>
              ) : (
                <p className="muted-sm" style={{ margin: 0 }}>Gate state unavailable — the API did not report it.</p>
              )}
            </div>

            <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Team</h3>
            <div className="panel" style={{ padding: 16, marginBottom: 32 }}>
              <div className="panel-title" style={{ marginBottom: 10 }}>
                <div>
                  <span className="kicker">Invites</span>
                  <h3>Invite someone to this instance</h3>
                </div>
              </div>
              {/* This app has no user table, no login and no sessions — so an
                  invite records who was invited, it does not gate anything.
                  Saying so here beats implying access control that isn't there. */}
              <p className="muted-sm" style={{ marginBottom: 14, maxWidth: 720 }}>
                This build has no login layer, so an invite is a <strong>roster record, not access control</strong> —
                the link records who was invited and who accepted, and grants no permissions. Anyone who can reach
                this instance already has access. Roles are stored ready for a future auth layer.
              </p>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                <input
                  placeholder="teammate@example.com"
                  value={inviteForm.email}
                  onChange={e => setInviteForm({ ...inviteForm, email: e.target.value })}
                  style={{ padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)', fontSize: 13, minWidth: 220 }}
                />
                <select
                  value={inviteForm.role}
                  onChange={e => setInviteForm({ ...inviteForm, role: e.target.value })}
                  style={{ padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)', fontSize: 13 }}
                >
                  <option value="viewer">Viewer</option>
                  <option value="operator">Operator</option>
                </select>
                <button className="primary-button" onClick={handleCreateInvite} disabled={inviting}>
                  {inviting ? '…' : '+ Create invite'}
                </button>
              </div>

              {inviteNotice && (
                <div style={{ marginTop: 12, padding: 10, borderRadius: 6, background: 'var(--bg-card)', fontFamily: 'monospace', fontSize: 12, color: 'var(--text-secondary)', wordBreak: 'break-all' }}>
                  {inviteNotice}
                </div>
              )}

              {invites.length > 0 && (
                <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {invites.map((inv: any) => (
                    <div key={inv.id} style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 12, padding: '8px 10px', borderRadius: 6, background: 'var(--bg-card)' }}>
                      <span style={{ flex: 1 }}>{inv.email || <em style={{ color: 'var(--text-muted)' }}>no email</em>}</span>
                      <span className="count-badge" style={{ textTransform: 'uppercase' }}>{inv.role}</span>
                      <span style={{
                        textTransform: 'uppercase', fontWeight: 600, fontSize: 11,
                        color: inv.status === 'accepted' ? 'var(--green)' : inv.status === 'pending' ? '#fbbf24' : 'var(--text-muted)',
                      }}>
                        {inv.status}
                      </span>
                      {inv.status === 'pending' && (
                        <button
                          onClick={() => handleRevoke(inv.id)}
                          style={{ padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600, cursor: 'pointer', background: 'rgba(239,68,68,0.12)', color: '#EF4444', border: 'none' }}
                        >
                          Revoke
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
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
                  <span style={{ color: 'var(--text-muted)' }}>{item.label}</span>
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
