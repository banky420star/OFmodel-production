'use client'

import { useEffect, useState } from 'react'
import { Toast, type ToastState } from '@/components/ui/Toast'
import {
  listPlatformAccounts, createPlatformAccount, patchPlatformAccount,
  getPlatformInventory, syncPlatformGallery, planPlatformInventory,
  listPlatformOrders, postPlatformInventoryItem, listPersonas,
} from '@/lib/api'

interface PlatformAccount {
  id: string
  persona_id: string
  platform: string
  handle: string
  status: string
  is_ai_disclosed: boolean
  ai_disclosure_text: string
  kyc_status: string
  consent_owner: string
  subscription_price: number
  subscriber_count: number
  targets: { public_posts_per_day: number; subscriber_posts_per_day: number; premium_items_per_week: number }
  compliance: { compliant: boolean; problems: string[] }
  restricted?: boolean
}

interface InventoryItem {
  id: string
  tier: string
  content_type: string
  asset_key: string
  is_mock: boolean
  caption: string
  status: string
}

interface ShootOrder {
  id: string
  tier: string
  content_type: string
  units_requested: number
  units_fulfilled: number
  reason: string
  status: string
}

interface Persona { id: string; name: string; status: string; brand: string }

const TIER_COLORS: Record<string, string> = {
  public: '#79c0ff',
  subscriber: '#7ee787',
  premium: '#d2a8ff',
}

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  pending_setup: { bg: 'rgba(255,255,255,0.05)', color: 'var(--text-muted)' },
  onboarding: { bg: 'rgba(251,191,36,0.12)', color: 'var(--amber)' },
  active: { bg: 'rgba(217,251,113,0.15)', color: 'var(--green)' },
  paused: { bg: 'rgba(255,255,255,0.05)', color: 'var(--text-muted)' },
  suspended: { bg: 'rgba(239,68,68,0.08)', color: '#EF4444' },
}

export default function MonetizationPage() {
  const [accounts, setAccounts] = useState<PlatformAccount[]>([])
  const [personas, setPersonas] = useState<Persona[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [inventory, setInventory] = useState<InventoryItem[]>([])
  const [orders, setOrders] = useState<ShootOrder[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [toast, setToast] = useState<ToastState>(null)
  const [form, setForm] = useState({
    persona_id: '', handle: '', is_ai_disclosed: true, kyc_status: 'verified',
    consent_owner: '', subscription_price: 9.99,
  })
  const notify = (msg: string, type: 'success' | 'error' = 'success') => setToast({ msg, type })

  const refresh = () => {
    Promise.all([
      listPlatformAccounts().catch(() => []),
      listPersonas().catch(() => []),
    ]).then(([accts, pers]: [any[], any[]]) => {
      setAccounts(accts)
      setPersonas(pers)
      setLoading(false)
      if (!selectedId && accts.length > 0) setSelectedId(accts[0].id)
    })
  }

  const loadDetail = async (id: string) => {
    try {
      const [inv, ords] = await Promise.all([
        getPlatformInventory(id),
        listPlatformOrders(id),
      ])
      setInventory(inv as InventoryItem[])
      setOrders(ords as ShootOrder[])
    } catch { /* transient */ }
  }

  useEffect(() => { refresh() }, [])
  useEffect(() => { if (selectedId) loadDetail(selectedId) }, [selectedId])

  const selected = accounts.find(a => a.id === selectedId) || null

  const handleCreate = async () => {
    if (!form.persona_id) return
    setBusy('create')
    try {
      const acct: any = await createPlatformAccount({
        persona_id: form.persona_id,
        handle: form.handle,
        is_ai_disclosed: form.is_ai_disclosed,
        kyc_status: form.kyc_status,
        consent_owner: form.consent_owner,
        subscription_price: form.subscription_price,
      })
      notify(acct.message || 'Account created')
      setShowCreate(false)
      setForm(f => ({ ...f, persona_id: '', handle: '', consent_owner: '' }))
      refresh()
      setSelectedId(acct.id)
    } catch (e: any) { notify(e.message, 'error') }
    setBusy(null)
  }

  const handleSync = async () => {
    if (!selected) return
    setBusy('sync')
    try {
      const r: any = await syncPlatformGallery(selected.id)
      notify(`Gallery sync: ${r.added} new, ${r.already_registered} already registered (${r.found} real assets found)`)
      loadDetail(selected.id)
    } catch (e: any) { notify(e.message, 'error') }
    setBusy(null)
  }

  const handlePlan = async () => {
    if (!selected) return
    setBusy('plan')
    try {
      const r: any = await planPlatformInventory(selected.id)
      if (!r.planning_allowed) {
        notify(r.reason, 'error')
      } else if (r.orders_created.length === 0) {
        notify('Plan complete — no shortages beyond already-open orders')
      } else {
        notify(`Plan opened ${r.orders_created.length} shoot order(s) for shortages`)
      }
      loadDetail(selected.id)
    } catch (e: any) { notify(e.message, 'error') }
    setBusy(null)
  }

  const handlePost = async (itemId: string) => {
    setBusy(itemId)
    try {
      await postPlatformInventoryItem(itemId)
      notify('Registered as posted')
      if (selected) loadDetail(selected.id)
    } catch (e: any) { notify(e.message, 'error') }
    setBusy(null)
  }

  const setCompliance = async (patch: Record<string, unknown>) => {
    if (!selected) return
    try {
      await patchPlatformAccount(selected.id, patch)
      refresh()
      notify('Compliance updated')
    } catch (e: any) { notify(e.message, 'error') }
  }

  // Ladder counts for the selected account
  const ladder = ['public', 'subscriber', 'premium'].map(tier => {
    const items = inventory.filter(i => i.tier === tier)
    const ready = items.filter(i => i.status === 'ready')
    const posted = items.filter(i => i.status === 'posted')
    return {
      tier,
      readyImages: ready.filter(i => i.content_type === 'image').length,
      readyVideos: ready.filter(i => i.content_type === 'video').length,
      posted: posted.length,
      items,
    }
  })

  return (
    <main className="workspace">
      <header className="topbar">
        <div className="crumb">
          <a href="/"><span>Persona Studio</span></a><b>/</b><strong>Monetization</strong>
        </div>
      </header>
      <div className="content">
        <section className="page-heading">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div>
              <h1>Monetization — Fanvue Manager</h1>
              <p>
                AI-disclosed platform accounts, the PUBLIC / SUBSCRIBER / PREMIUM inventory ladder,
                and shortage-driven shoot orders. Fanvue-first: AI creators permitted with disclosure + KYC.
              </p>
            </div>
            <button
              className="primary-button"
              onClick={() => setShowCreate(!showCreate)}
              style={{ padding: '10px 18px', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer' }}
            >
              + Platform Account
            </button>
          </div>
        </section>

        {showCreate && (
          <div className="panel" style={{ padding: 20, marginBottom: 20, border: '1px solid var(--green)' }}>
            <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 16 }}>New Platform Account (Fanvue)</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div>
                <label className="muted-sm" style={{ display: 'block', marginBottom: 4 }}>Model</label>
                <select value={form.persona_id} onChange={e => setForm(f => ({ ...f, persona_id: e.target.value }))}
                  style={{ width: '100%', padding: '8px 12px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)', fontSize: 13 }}>
                  <option value="">Select a model...</option>
                  {personas.filter(p => p.status === 'active' || p.status === 'ready').map(p => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="muted-sm" style={{ display: 'block', marginBottom: 4 }}>Handle</label>
                <input value={form.handle} onChange={e => setForm(f => ({ ...f, handle: e.target.value }))}
                  placeholder="ava.ai"
                  style={{ width: '100%', padding: '8px 12px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)', fontSize: 13 }} />
              </div>
              <div>
                <label className="muted-sm" style={{ display: 'block', marginBottom: 4 }}>KYC status</label>
                <select value={form.kyc_status} onChange={e => setForm(f => ({ ...f, kyc_status: e.target.value }))}
                  style={{ width: '100%', padding: '8px 12px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)', fontSize: 13 }}>
                  <option value="verified">verified</option>
                  <option value="submitted">submitted</option>
                  <option value="not_started">not started</option>
                </select>
              </div>
              <div>
                <label className="muted-sm" style={{ display: 'block', marginBottom: 4 }}>Consent owner (verified human)</label>
                <input value={form.consent_owner} onChange={e => setForm(f => ({ ...f, consent_owner: e.target.value }))}
                  placeholder="your name"
                  style={{ width: '100%', padding: '8px 12px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)', fontSize: 13 }} />
              </div>
              <div>
                <label className="muted-sm" style={{ display: 'block', marginBottom: 4 }}>Subscription price (USD/mo)</label>
                <input type="number" min="0" step="0.5" value={form.subscription_price}
                  onChange={e => setForm(f => ({ ...f, subscription_price: Number(e.target.value) }))}
                  style={{ width: '100%', padding: '8px 12px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)', fontSize: 13 }} />
              </div>
              <div style={{ display: 'flex', alignItems: 'flex-end' }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
                  <input type="checkbox" checked={form.is_ai_disclosed}
                    onChange={e => setForm(f => ({ ...f, is_ai_disclosed: e.target.checked }))} />
                  AI-creator disclosure ON (required by Fanvue)
                </label>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
              <button className="primary-button" onClick={handleCreate} disabled={!form.persona_id || busy === 'create'}
                style={{ padding: '8px 16px', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>
                {busy === 'create' ? 'Creating…' : 'Create Account'}
              </button>
              <button onClick={() => setShowCreate(false)}
                style={{ padding: '8px 16px', borderRadius: 6, fontSize: 13, cursor: 'pointer', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-muted)' }}>
                Cancel
              </button>
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 12 }}>
              Fanvue permits fully AI-generated creators when the AI nature is disclosed and the
              account operator passes KYC. Both are compliance fields here — planning stays locked until they are set.
            </p>
          </div>
        )}

        {loading ? (
          <p className="muted-md" style={{ padding: 16 }}>Loading accounts...</p>
        ) : accounts.length === 0 ? (
          <div className="panel" style={{ textAlign: 'center', padding: 48 }}>
            <p style={{ fontSize: 32, marginBottom: 12 }}>💰</p>
            <p style={{ fontSize: 14, color: 'var(--text-muted)' }}>
              No platform accounts yet. Create a Fanvue account for one of your models.
            </p>
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 16, alignItems: 'start' }}>
            {/* Account list */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {accounts.map(a => {
                const sc = STATUS_COLORS[a.status] || STATUS_COLORS.pending_setup
                return (
                  <button key={a.id} onClick={() => setSelectedId(a.id)} className="panel"
                    style={{
                      padding: '12px 14px', textAlign: 'left', cursor: 'pointer',
                      border: `1px solid ${selectedId === a.id ? 'var(--green)' : 'var(--border)'}`,
                      background: 'var(--bg-card)', color: 'var(--text)',
                    }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <div style={{ fontWeight: 600, fontSize: 13 }}>@{a.handle}</div>
                      <span style={{
                        padding: '2px 8px', borderRadius: 5, fontSize: 10, fontWeight: 600,
                        background: sc.bg, color: sc.color, textTransform: 'uppercase',
                      }}>{a.status.replace('_', ' ')}</span>
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                      {a.platform} · {a.subscription_price > 0 ? `$${a.subscription_price}/mo` : 'free'}
                    </div>
                    <div style={{ fontSize: 11, marginTop: 4 }}>
                      {a.compliance.compliant
                        ? <span style={{ color: 'var(--green)' }}>✓ compliant</span>
                        : <span style={{ color: 'var(--amber)' }}>⚠ {a.compliance.problems.length} compliance item(s)</span>}
                    </div>
                  </button>
                )
              })}
            </div>

            {/* Detail */}
            {selected && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                {/* Compliance card */}
                <div className="panel" style={{ padding: 16, border: selected.compliance.compliant ? '1px solid var(--border)' : '1px solid rgba(251,191,36,0.4)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                    <div style={{ fontWeight: 600, fontSize: 14 }}>Compliance</div>
                    {selected.compliance.compliant
                      ? <span style={{ color: 'var(--green)', fontSize: 12 }}>✓ Planning unlocked</span>
                      : <span style={{ color: 'var(--amber)', fontSize: 12 }}>⚠ Planning locked</span>}
                  </div>
                  {selected.restricted ? (
                    <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                      Restricted platform — no planning/automation (permitted workflows only).
                    </p>
                  ) : selected.compliance.compliant ? (
                    <div style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.6 }}>
                      <div>✓ AI-creator disclosure: <b style={{ color: 'var(--text)' }}>ON</b> — “{selected.ai_disclosure_text}”</div>
                      <div>✓ KYC: <b style={{ color: 'var(--text)' }}>{selected.kyc_status}</b> · operator: {selected.consent_owner}</div>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                      {selected.compliance.problems.map((p, i) => (
                        <div key={i} style={{ fontSize: 12, color: 'var(--amber)' }}>⚠ {p}</div>
                      ))}
                      <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                        {!selected.is_ai_disclosed && (
                          <button onClick={() => setCompliance({ is_ai_disclosed: true })}
                            style={{ padding: '6px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)' }}>
                            Turn disclosure ON
                          </button>
                        )}
                        {selected.kyc_status !== 'verified' && (
                          <button onClick={() => setCompliance({ kyc_status: 'verified' })}
                            style={{ padding: '6px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)' }}>
                            Mark KYC verified
                          </button>
                        )}
                        {!selected.consent_owner && (
                          <button onClick={() => setCompliance({ consent_owner: 'bank' })}
                            style={{ padding: '6px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)' }}>
                            Record operator
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </div>

                {/* Actions */}
                <div style={{ display: 'flex', gap: 8 }}>
                  <button onClick={handleSync} disabled={busy === 'sync'}
                    style={{ padding: '8px 14px', borderRadius: 8, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)' }}>
                    {busy === 'sync' ? 'Syncing…' : '⬆ Sync gallery → PUBLIC'}
                  </button>
                  <button onClick={handlePlan} disabled={busy === 'plan'}
                    style={{
                      padding: '8px 14px', borderRadius: 8, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                      border: '1px solid var(--green)', background: 'rgba(217,251,113,0.1)', color: 'var(--green)',
                    }}>
                    {busy === 'plan' ? 'Planning…' : '📋 Plan inventory (open shoot orders)'}
                  </button>
                </div>

                {/* Inventory ladder */}
                <div className="panel" style={{ padding: 16 }}>
                  <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 12 }}>Inventory ladder</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {ladder.map(t => (
                      <div key={t.tier} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                        <div style={{ width: 90, fontSize: 12, fontWeight: 600, color: TIER_COLORS[t.tier], textTransform: 'uppercase' }}>
                          {t.tier}
                        </div>
                        <div style={{ flex: 1, display: 'flex', gap: 14, fontSize: 12, color: 'var(--text-muted)' }}>
                          <span>🖼 {t.readyImages} ready</span>
                          <span>🎬 {t.readyVideos} ready</span>
                          <span>📤 {t.posted} posted</span>
                        </div>
                      </div>
                    ))}
                  </div>
                  {inventory.length > 0 && (
                    <div style={{ marginTop: 14, display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 10 }}>
                      {inventory.slice(0, 12).map(i => (
                        <div key={i.id} style={{
                          border: '1px solid var(--border)', borderRadius: 8, padding: 8, fontSize: 11,
                        }}>
                          <div style={{ color: TIER_COLORS[i.tier], fontWeight: 600, textTransform: 'uppercase', fontSize: 10 }}>
                            {i.tier} · {i.content_type}
                          </div>
                          <div style={{ marginTop: 4, color: 'var(--text-muted)', wordBreak: 'break-all', minHeight: 28 }}>
                            {i.caption || i.asset_key.split('/').pop()}
                          </div>
                          <div style={{ marginTop: 6, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <span style={{ color: i.status === 'ready' ? 'var(--green)' : 'var(--text-muted)' }}>{i.status}</span>
                            {i.status === 'ready' && (
                              <button onClick={() => handlePost(i.id)} disabled={busy === i.id}
                                style={{ padding: '3px 8px', borderRadius: 5, fontSize: 10, cursor: 'pointer', border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)' }}>
                                {busy === i.id ? '…' : 'Post'}
                              </button>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Shoot orders */}
                <div className="panel" style={{ padding: 16 }}>
                  <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 12 }}>Shoot orders (shortage-driven)</div>
                  {orders.length === 0 ? (
                    <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                      No orders yet — run inventory planning to detect shortages.
                    </p>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                      {orders.map(o => (
                        <div key={o.id} style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 12 }}>
                          <span style={{
                            width: 10, height: 10, borderRadius: '50%', flexShrink: 0,
                            background: o.status === 'open' ? 'var(--amber)' : 'var(--green)',
                          }} />
                          <span style={{ color: TIER_COLORS[o.tier], fontWeight: 600, width: 86, textTransform: 'uppercase' }}>
                            {o.tier}/{o.content_type}
                          </span>
                          <span style={{ color: 'var(--text)' }}>{o.units_requested} unit(s)</span>
                          <span style={{ color: 'var(--text-muted)', flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            {o.reason}
                          </span>
                          <span style={{ color: o.status === 'open' ? 'var(--amber)' : 'var(--green)', fontWeight: 600 }}>
                            {o.status}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </main>
  )
}
