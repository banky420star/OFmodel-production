'use client'

import { useCallback, useEffect, useState } from 'react'
import { Icons } from '@/lib/icons'
import { Toast, type ToastState } from '@/components/ui/Toast'
import { getManagerRoster, requestAllPlatforms, getSignupPacket, storeCredentials, getManagerBusiness } from '@/lib/api'
import type { ManagerRoster, ManagerAccount, BusinessState } from '@/lib/types'
import { formatMoney } from '@/lib/utils'

/**
 * The model manager view.
 *
 * One table: every model down the side, every platform across the top, and in
 * each cell the state of that account plus the single next thing a person has
 * to do. The point of the page is the `next_action` — an account is never shown
 * as live because a row exists, only because the platform has it and an
 * operator said so.
 */

const ACTION_LABEL: Record<string, string> = {
  request: 'Not requested',
  approve: 'Awaiting approval',
  generate_email: 'Needs inbox',
  open_packet: 'Packet ready',
  signup: 'Ready to sign up',
  finish_signup: 'Finish signup',
  connect_api: 'Connect API',
  operating: 'Live',
  review: 'Suspended',
  closed: 'Rejected',
}

const ACTION_TONE: Record<string, string> = {
  approve: 'var(--amber)',
  generate_email: 'var(--amber)',
  open_packet: 'var(--green)',
  signup: 'var(--green)',
  finish_signup: 'var(--amber)',
  connect_api: 'var(--text-muted)',
  operating: 'var(--green)',
  review: 'var(--red)',
  closed: 'var(--text-muted)',
  request: 'var(--text-muted)',
}

/**
 * The whole book, above the roster.
 *
 * The roster below is a worklist: which account needs what next. This is the
 * question an operator actually has — what can this studio sell, what has it
 * earned, and what is the one thing standing in the way. It is fetched in its
 * own effect so a slow or failing business read cannot blank the worklist.
 *
 * Two things it deliberately does not do. It never renders an unread earnings
 * reading as R 0.00 (`formatMoney` renders null as an em dash). And it never
 * calls a post sellable because a row exists — `shippable` counts only slots
 * that are priced, on a platform this app can publish to, with media that is
 * really on disk.
 */
function BusinessPanel({ state }: { state: BusinessState }) {
  const { money, summary, next_action, stranded_slots } = state
  const { gates_open, gates_total } = next_action
  const clear = gates_open === gates_total

  return (
    <section style={{ display: 'grid', gap: 14, marginBottom: 22 }}>
      <article style={{
        background: 'var(--bg-card)', border: '1px solid var(--border)',
        borderLeft: `3px solid ${clear ? 'var(--green)' : 'var(--amber)'}`,
        borderRadius: 12, padding: 18,
      }}>
        <p style={{
          fontSize: 11, letterSpacing: '0.08em', textTransform: 'uppercase',
          color: 'var(--text-muted)', marginBottom: 6,
        }}>
          Next action · {gates_open} of {gates_total} gates clear
        </p>
        <h2 style={{ fontSize: 17, marginBottom: 6 }}>{next_action.label}</h2>
        <p style={{ fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.5 }}>
          {next_action.detail}
        </p>
        {!!next_action.for_personas?.length && (
          <p style={{ fontSize: 12, color: 'var(--amber)', marginTop: 10 }}>
            Short of shippable content: {next_action.for_personas.join(', ')}
          </p>
        )}
        {next_action.also_blocked.length > 0 && (
          <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 10 }}>
            Also blocked: {next_action.also_blocked.join(' · ')}
          </p>
        )}
      </article>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <Stat
          label="Earned (net)"
          value={money.is_real ? formatMoney(money.headline.net, money.headline.currency) : '—'}
          tone={money.is_real ? 'var(--green)' : undefined}
        />
        <Stat label="Can ship" value={`${summary.personas_that_can_ship} of ${summary.personas}`} />
        <Stat label="Shippable posts" value={summary.shippable_posts} />
        <Stat
          label="Shelf value"
          value={formatMoney(summary.shelf_value, money.headline.currency)}
        />
        {stranded_slots.count > 0 && (
          <Stat label="Stranded slots" value={stranded_slots.count} tone="var(--red)" />
        )}
      </div>

      {!money.is_real && (
        <p style={{
          fontSize: 12, color: 'var(--text-muted)',
          borderLeft: '2px solid var(--amber)', paddingLeft: 10,
        }}>
          No earnings reading — {money.detail}
        </p>
      )}
      {stranded_slots.count > 0 && (
        <p style={{
          fontSize: 12, color: 'var(--text-muted)',
          borderLeft: '2px solid var(--amber)', paddingLeft: 10,
        }}>
          {stranded_slots.count} slot(s) on {stranded_slots.platforms.join(', ')} —{' '}
          {stranded_slots.note}
        </p>
      )}
    </section>
  )
}

export default function ManagerPage() {
  const [data, setData] = useState<ManagerRoster | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [toast, setToast] = useState<ToastState>(null)
  const [packet, setPacket] = useState<any>(null)
  const [onlyPlatform, setOnlyPlatform] = useState('')
  const [error, setError] = useState('')
  // The note from the last request-all response. Shown in the same slot as the
  // roster note, because it is the API's own statement about what that action did
  // or did not do — the roster note is about the roster, and the two differ.
  const [requestNote, setRequestNote] = useState('')
  // The handle the operator actually registered, which is often not the one the
  // packet suggested — platforms reject taken names. Recording it is what clears
  // the `no_platform_password` / username side of the roster's blockers.
  const [credUsername, setCredUsername] = useState('')
  const [savingCreds, setSavingCreds] = useState(false)
  // The whole book, fetched on its own. It must not share `error` with the
  // roster: a business read that fails (a slow platform, an unreadable ledger)
  // would otherwise blank the worklist too, and the worklist is exactly what an
  // operator needs when something upstream is unwell.
  const [business, setBusiness] = useState<BusinessState | null>(null)
  const [businessError, setBusinessError] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    getManagerRoster(onlyPlatform ? { platform: onlyPlatform } : undefined)
      .then((d: ManagerRoster) => { setData(d); setError(''); setLoading(false) })
      .catch((e: Error) => { setError(e.message); setLoading(false) })
  }, [onlyPlatform])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    getManagerBusiness()
      .then((d: BusinessState) => { setBusiness(d); setBusinessError('') })
      .catch((e: Error) => setBusinessError(e.message))
  }, [])

  const notify = (msg: string, type: 'success' | 'error' = 'success') =>
    setToast({ msg, type })

  // Files the local request rows for every platform this model lacks. Nothing
  // is sent anywhere — the note the API returns says so, and it is surfaced
  // rather than paraphrased.
  const handleRequestAll = async (personaId: string, name: string) => {
    setBusy(personaId)
    try {
      const res = await requestAllPlatforms(personaId)
      notify(
        res.created_count
          ? `${name}: ${res.created_count} account request${res.created_count === 1 ? '' : 's'} filed — approve them, then complete each signup packet by hand.`
          : `${name}: nothing to file (${res.skipped_count} already covered).`,
      )
      // The API's own note, appended rather than replaced: it is the statement
      // that nothing here contacted a platform, and the comment above claimed it
      // was surfaced while this function never read it.
      if (res.note) setRequestNote(res.note)
      load()
    } catch (e: any) {
      notify(e.message || 'Could not file requests', 'error')
    } finally {
      setBusy(null)
    }
  }

  const handlePacket = async (account: ManagerAccount) => {
    setBusy(account.account_id)
    try {
      const p = await getSignupPacket(account.account_id)
      setPacket(p)
      setCredUsername((p as any).username || '')
    } catch (e: any) {
      notify(e.message || 'Could not build the signup packet', 'error')
    } finally {
      setBusy(null)
    }
  }

  const handleSaveCredentials = async () => {
    if (!packet) return
    setSavingCreds(true)
    try {
      await storeCredentials(packet.account_id, packet.password, credUsername)
      notify(`Credentials saved for ${packet.platform}`)
      setPacket(null)
      load()
    } catch (e: any) {
      notify(e.message || 'Could not save credentials', 'error')
    } finally {
      setSavingCreds(false)
    }
  }

  return (
    <main className="workspace">
      <header className="topbar">
        <button className="mobile-menu" aria-label="Open menu">{Icons.menu}</button>
        <div className="crumb">
          <a href="/"><span>Persona Studio</span></a><b>/</b>
          <strong>Model manager</strong>
        </div>
        <div className="top-actions">
          <a href="/models/create"><button className="primary-button">{Icons.plus} Create model</button></a>
        </div>
      </header>

      <div className="content">
        <section className="page-heading">
          <h1>Model manager</h1>
          <p>
            Every model against every platform, and the next thing a person has to do.
          </p>
        </section>

        {business
          ? <BusinessPanel state={business} />
          : businessError && (
              <p style={{
                fontSize: 12, color: 'var(--text-muted)', marginBottom: 20,
                borderLeft: '2px solid var(--amber)', paddingLeft: 10,
              }}>
                Business summary unavailable — {businessError}. The roster below is
                unaffected.
              </p>
            )}

        {data && (
          <section style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
            <Stat label="Models" value={data.summary.models} />
            <Stat label="Accounts" value={data.summary.accounts} />
            <Stat label="Live" value={data.summary.live} tone="var(--green)" />
            <Stat label="Needing work" value={data.summary.needing_work} tone="var(--amber)" />
            <Stat label="Not requested" value={data.summary.not_requested} />
          </section>
        )}

        <section style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 22 }}>
          <FilterChip label="All platforms" active={!onlyPlatform} onClick={() => setOnlyPlatform('')} />
          {(data?.platforms || []).map(p => (
            <FilterChip key={p} label={p} active={onlyPlatform === p}
                        onClick={() => setOnlyPlatform(onlyPlatform === p ? '' : p)} />
          ))}
        </section>

        {requestNote && (
          <p style={{
            fontSize: 12, color: 'var(--green)', marginBottom: 12,
            borderLeft: '2px solid var(--green)', paddingLeft: 10,
          }}>{requestNote}</p>
        )}
        {data?.note && (
          <p style={{
            fontSize: 12, color: 'var(--text-muted)', marginBottom: 20,
            borderLeft: '2px solid var(--amber)', paddingLeft: 10,
          }}>{data.note}</p>
        )}

        {loading ? (
          <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>Loading roster…</p>
        ) : error ? (
          <p style={{ color: 'var(--red)', fontSize: 13 }}>{error}</p>
        ) : !data || data.roster.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '48px 0', color: 'var(--text-muted)' }}>
            <p style={{ fontSize: 15, marginBottom: 8 }}>No models yet</p>
            <a href="/models/create" style={{ color: 'var(--green)', textDecoration: 'none' }}>
              Create your first model →
            </a>
          </div>
        ) : (
          <div style={{ display: 'grid', gap: 16 }}>
            {data.roster.map(row => (
              <article key={row.persona_id} style={{
                background: 'var(--bg-card)', border: '1px solid var(--border)',
                borderRadius: 12, padding: 18,
              }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, flexWrap: 'wrap' }}>
                  <div style={{ flex: 1, minWidth: 220 }}>
                    <a href={`/personas/${row.persona_id}`} style={{ textDecoration: 'none' }}>
                      <h3 style={{ fontSize: 16, marginBottom: 4 }}>{row.name}</h3>
                    </a>
                    <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                      {row.status} · {row.platforms_covered}/{data.platforms.length} platforms ·{' '}
                      {row.accounts_needing_work > 0
                        ? `${row.accounts_needing_work} needing work`
                        : 'nothing pending'}
                    </p>
                  </div>
                  <button
                    className="primary-button"
                    disabled={busy === row.persona_id || row.platforms_missing.length === 0}
                    onClick={() => handleRequestAll(row.persona_id, row.name)}
                  >
                    {busy === row.persona_id
                      ? '…'
                      : row.platforms_missing.length === 0
                        ? 'All platforms requested'
                        : `Request ${row.platforms_missing.length} remaining`}
                  </button>
                </div>

                {row.warnings.length > 0 && (
                  <div style={{
                    marginTop: 12, padding: '10px 12px', borderRadius: 8,
                    background: 'var(--amber-dim)', border: '1px solid var(--amber)',
                  }}>
                    <b style={{ fontSize: 11, color: 'var(--amber)', letterSpacing: '.04em' }}>
                      BUILD WARNINGS
                    </b>
                    {row.warnings.map((w, i) => (
                      <p key={i} style={{ fontSize: 12, color: 'var(--text)', marginTop: 4 }}>{w}</p>
                    ))}
                  </div>
                )}

                <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 12 }}>
                  Next: {row.next_action}
                </p>

                <div style={{ marginTop: 12, display: 'grid', gap: 8 }}>
                  {row.accounts.map(a => (
                    <div key={a.account_id} style={{
                      display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
                      padding: '10px 12px', borderRadius: 8,
                      background: 'var(--bg)', border: '1px solid var(--border)',
                    }}>
                      <span style={{
                        fontSize: 11, fontWeight: 700, letterSpacing: '.04em',
                        minWidth: 84, textTransform: 'uppercase', color: ACTION_TONE[a.next_action],
                      }}>{a.platform}</span>
                      <span style={{ fontSize: 13, minWidth: 120 }}>@{a.username}</span>
                      <span style={{
                        fontSize: 11, fontWeight: 700, letterSpacing: '.04em',
                        textTransform: 'uppercase', color: ACTION_TONE[a.next_action],
                      }}>{ACTION_LABEL[a.next_action] || a.next_action}</span>
                      <span style={{ fontSize: 12, color: 'var(--text-muted)', flex: 1, minWidth: 220 }}>
                        {a.next_action_detail}
                      </span>
                      {/* The readiness flags behind the next-action label. The API
                          has always sent these and nothing rendered them, so a row
                          could read "Sign up" with no way to see whether the inbox,
                          the password or the packet itself was the missing piece. */}
                      <span style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        {([
                          ['inbox', a.has_inbox],
                          ['password', a.has_password],
                          ['creds saved', a.credentials_saved],
                          ['packet ready', a.packet_ready],
                        ] as [string, boolean][]).map(([label, ok]) => (
                          <span key={label} style={{
                            fontSize: 10, fontWeight: 600, letterSpacing: '.03em',
                            padding: '3px 7px', borderRadius: 5,
                            background: ok ? 'rgba(217,251,113,0.10)' : 'var(--bg-card)',
                            color: ok ? 'var(--green)' : 'var(--text-muted)',
                            border: `1px solid ${ok ? 'rgba(217,251,113,0.3)' : 'var(--border)'}`,
                          }}>
                            {ok ? '✓' : '○'} {label}
                          </span>
                        ))}
                        {a.signup_step && (
                          <span style={{ fontSize: 10, color: 'var(--text-muted)', padding: '3px 0' }}>
                            step: {a.signup_step}
                          </span>
                        )}
                      </span>
                      {a.blockers.length > 0 && (
                        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                          {a.blockers.join(' · ')}
                        </span>
                      )}
                      <button
                        className="primary-button"
                        disabled={busy === a.account_id}
                        onClick={() => handlePacket(a)}
                      >
                        {busy === a.account_id ? '…' : 'Signup packet'}
                      </button>
                    </div>
                  ))}
                  {row.platforms_missing.length > 0 && (
                    <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                      Not requested: {row.platforms_missing.join(', ')}
                    </p>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
      </div>

      {packet && (
        <div
          role="dialog"
          aria-label={`Signup packet for ${packet.platform}`}
          onClick={() => setPacket(null)}
          style={{
            position: 'fixed', inset: 0, zIndex: 998, background: 'rgba(0,0,0,0.55)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
          }}
        >
          <div
            onClick={e => e.stopPropagation()}
            style={{
              background: 'var(--bg-card)', border: '1px solid var(--border)',
              borderRadius: 12, padding: 24, maxWidth: 620, width: '100%',
              maxHeight: '85vh', overflowY: 'auto',
            }}
          >
            <h3 style={{ fontSize: 17, marginBottom: 4 }}>
              Signup packet — {packet.platform}
            </h3>
            <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 16 }}>
              for {packet.persona_name} · account status stays <b>{packet.status}</b> — this
              packet changes nothing on the platform
            </p>

            <div style={{ display: 'grid', gap: 6, marginBottom: 16 }}>
              {([
                ['Username', packet.username],
                ['Display name', packet.display_name],
                ['Bio', packet.bio],
                ['Email', packet.email || '(none generated yet)'],
                ['Email password', packet.email_password || '(none)'],
                ['Platform password', packet.password],
              ] as [string, string][]).map(([k, v]) => (
                <div key={k} style={{ display: 'flex', gap: 10, fontSize: 13 }}>
                  <span style={{ color: 'var(--text-muted)', minWidth: 130 }}>{k}</span>
                  <span style={{ fontFamily: 'var(--mono, monospace)', wordBreak: 'break-all' }}>{v}</span>
                </div>
              ))}
            </div>

            <ol style={{ fontSize: 13, paddingLeft: 18, marginBottom: 16 }}>
              {(packet.steps || []).map((s: string, i: number) => <li key={i}>{s}</li>)}
            </ol>

            {/* The last step of the packet's own instructions has always said to
                save the credentials, and no such control existed on either page
                that shows a packet. Record the handle that was actually
                registered, so the roster stops reporting it as missing. */}
            <div style={{
              display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap',
              padding: '12px 14px', marginBottom: 16, borderRadius: 8,
              background: 'var(--bg)', border: '1px solid var(--border)',
            }}>
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                Account created? Record the handle you registered:
              </span>
              <input
                value={credUsername}
                onChange={e => setCredUsername(e.target.value)}
                placeholder={packet.username || 'username'}
                style={{ flex: 1, minWidth: 160, fontSize: 13, padding: '6px 10px', borderRadius: 6, background: 'var(--bg-card)', color: 'var(--text)', border: '1px solid var(--border)', outline: 'none' }}
              />
              <button
                className="primary-button"
                disabled={savingCreds || !packet.password}
                onClick={handleSaveCredentials}
              >
                {savingCreds ? '…' : 'Save credentials'}
              </button>
            </div>

            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <a href={packet.signup_url} target="_blank" rel="noreferrer">
                <button className="primary-button">Open {packet.platform} signup page ↗</button>
              </a>
              <button className="primary-button" onClick={() => setPacket(null)}>Close</button>
            </div>

            {packet.note && (
              <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 16 }}>{packet.note}</p>
            )}
          </div>
        </div>
      )}

      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </main>
  )
}

function Stat({ label, value, tone }: { label: string; value: number | string; tone?: string }) {
  return (
    <div style={{
      background: 'var(--bg-card)', border: '1px solid var(--border)',
      borderRadius: 10, padding: '10px 16px', minWidth: 120,
    }}>
      <small style={{ fontSize: 11, color: 'var(--text-muted)', display: 'block' }}>{label}</small>
      <b style={{ fontSize: 20, color: tone || 'var(--text)' }}>{value}</b>
    </div>
  )
}

function FilterChip({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        fontSize: 12, padding: '5px 12px', borderRadius: 999, cursor: 'pointer',
        background: active ? 'var(--green-dim)' : 'var(--bg-card)',
        color: active ? 'var(--green)' : 'var(--text-muted)',
        border: `1px solid ${active ? 'var(--green)' : 'var(--border)'}`,
      }}
    >{label}</button>
  )
}
