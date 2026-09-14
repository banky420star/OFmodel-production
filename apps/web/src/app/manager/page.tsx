'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  listPersonas, managerDashboard, startManager, wakeManager, pauseManager,
  resumeManager, managerRunNow, retryManagerFailed, setManagerAutonomy,
} from '@/lib/api'
import { Icons } from '@/lib/icons'

interface TaskDto {
  id: string
  task_type: string
  status: string
  priority: number
  source: string
  error: string | null
  error_kind: string | null
  provider: string | null
  retry_count: number
  max_retries: number
  created_at: string
  started_at: string | null
  completed_at: string | null
  result: Record<string, unknown> | null
}

interface Dashboard {
  manager_id: string
  persona_id: string
  persona_name: string
  status: string
  paused: boolean
  autonomy_level: number
  current_objective: string | null
  current_task: TaskDto | null
  next_action: { decision: string; reason: string; priority: string } | { decision: string }
  next_wake_at: string | null
  last_heartbeat_at: string | null
  identity_lock: { status: string; generation_allowed: boolean }
  inventory: Record<string, number>
  pipeline: {
    shoot_id: string
    stages: Record<string, number>
    shots: {
      shot_id: string; number: number; type: string; status: string
      qa: string | null; asset_key: string | null; error: string | null; retries: number
    }[]
  }[]
  active_jobs: TaskDto[]
  pending_jobs: TaskDto[]
  failed_jobs: TaskDto[]
  recent_tasks: TaskDto[]
  recent_decisions: { at: string; message: string; data: any }[]
  event_log: { at: string; kind: string; message: string }[]
  blockers: { error: string | null; kind: string | null; task: string }[]
}

const STATUS_DOT: Record<string, string> = {
  IDLE: '#22c55e', PLANNING: '#3b82f6', PRODUCING: '#3b82f6', QA: '#a855f7',
  SCHEDULING: '#3b82f6', ENGAGING: '#3b82f6', ANALYZING: '#a855f7',
  WAITING: '#eab308', WAITING_FOR_APPROVAL: '#eab308', BLOCKED: '#ef4444',
  RETRYING: '#eab308', ERROR: '#ef4444', PAUSED: '#6b7280', INITIALIZING: '#3b82f6',
}

function StatusPill({ status, paused }: { status: string; paused?: boolean }) {
  const color = paused ? '#6b7280' : (STATUS_DOT[status] ?? '#6b7280')
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: color, display: 'inline-block' }} />
      <span className="status" style={{ background: 'transparent' }}>{paused ? 'PAUSED' : status}</span>
    </span>
  )
}

function TaskRow({ t }: { t: TaskDto }) {
  return (
    <div className="list-row-compact" style={{ display: 'block' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontWeight: 600 }}>{t.task_type}</span>
        <span className={`status ${t.status.toLowerCase()}`}>{t.status}</span>
      </div>
      <div className="muted-sm" style={{ marginTop: 2 }}>
        {t.source} · prio {t.priority} · retries {t.retry_count}/{t.max_retries}
        {t.provider ? ` · ${t.provider}` : ''}
        {t.error ? ` · ${t.error_kind ?? 'error'}: ${t.error.slice(0, 90)}` : ''}
      </div>
    </div>
  )
}

export default function ManagerPage() {
  const [personas, setPersonas] = useState<{ id: string; name: string; status: string }[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [dash, setDash] = useState<Dashboard | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [expandedShoot, setExpandedShoot] = useState<string | null>(null)

  useEffect(() => {
    listPersonas().then((ps: any[]) => {
      setPersonas(ps.map((p) => ({ id: p.id, name: p.name, status: p.status })))
      setSelected((prev) => prev ?? ps[0]?.id ?? null)
    }).catch(() => setPersonas([]))
  }, [])

  const load = useCallback(async (personaId: string) => {
    try {
      setDash(await managerDashboard(personaId))
    } catch (e: any) {
      setMsg(e.message ?? 'failed to load dashboard')
    }
  }, [])

  useEffect(() => {
    if (selected) load(selected)
    const iv = setInterval(() => { if (selected) load(selected) }, 15000)
    return () => clearInterval(iv)
  }, [selected, load])

  const act = useCallback(async (fn: () => Promise<any>, label: string) => {
    setBusy(true); setMsg(null)
    try {
      await fn()
      setMsg(`${label} ✓`)
      if (selected) await load(selected)
    } catch (e: any) {
      setMsg(`${label} failed: ${e.message}`)
    } finally {
      setBusy(false)
    }
  }, [selected, load])

  const inv = dash?.inventory ?? {}
  const nextAction = dash?.next_action as any

  return (
    <div>
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700 }}>Model Managers</h1>
        <span className="muted-sm">
          Event-driven, state-machine managers — heartbeat every 45s, wakes only on real work
        </span>
      </div>

      {/* Persona selector */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
        {personas.map((p) => (
          <button
            key={p.id}
            onClick={() => setSelected(p.id)}
            className="btn-sm"
            style={{
              border: `1px solid ${selected === p.id ? 'var(--accent, #6366f1)' : 'var(--border)'}`,
              borderRadius: 8, padding: '6px 12px', cursor: 'pointer', background: 'var(--bg-panel)',
            }}
          >
            {p.name}
          </button>
        ))}
        {personas.length === 0 && <span className="muted-md">No personas yet.</span>}
      </div>

      {msg && <div className="panel" style={{ marginBottom: 12, padding: 10, fontSize: 13 }}>{msg}</div>}

      {dash && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          {/* ── STATUS + CONTROLS ─────────────────────────────── */}
          <section className="panel" style={{ gridColumn: '1 / -1' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ fontSize: 16, fontWeight: 700 }}>{dash.persona_name} — MODEL MANAGER</span>
                  <StatusPill status={dash.status} paused={dash.paused} />
                  {dash.autonomy_level === 0 && <span className="status" style={{ background: 'var(--amber-dim, #57430a33)', color: '#eab308' }}>APPROVAL REQUIRED</span>}
                </div>
                <div className="muted-sm" style={{ marginTop: 4 }}>
                  Objective: {dash.current_objective ?? '—'} · Autonomy L{dash.autonomy_level} · Last heartbeat {dash.last_heartbeat_at ? new Date(dash.last_heartbeat_at).toLocaleTimeString() : 'never'}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <button disabled={busy} className="btn-sm" onClick={() => selected && act(() => wakeManager(selected), 'Wake')}>Run now</button>
                {dash.paused
                  ? <button disabled={busy} className="btn-sm" onClick={() => selected && act(() => resumeManager(selected), 'Resume')}>Resume</button>
                  : <button disabled={busy} className="btn-sm" onClick={() => selected && act(() => pauseManager(selected), 'Pause')}>Pause</button>}
                <button disabled={busy} className="btn-sm" onClick={() => selected && act(() => retryManagerFailed(selected), 'Retry failed')}>Retry failed</button>
                <button disabled={busy} className="btn-sm" onClick={() => selected && act(() => setManagerAutonomy(selected, dash.autonomy_level === 2 ? 0 : 2), 'Autonomy')}>Autonomy L{dash.autonomy_level === 2 ? 0 : 2}</button>
              </div>
            </div>
            {dash.identity_lock && !dash.identity_lock.generation_allowed && (
              <div className="muted-sm" style={{ marginTop: 8, color: '#eab308' }}>
                Identity lock: {dash.identity_lock.status} — generation blocked until an ACTIVE lock exists
              </div>
            )}
          </section>

          {/* ── OBJECTIVE / CURRENT TASK / NEXT ───────────────── */}
          <section className="panel">
            <div className="section-title">Current task & next action</div>
            {dash.current_task
              ? <TaskRow t={dash.current_task} />
              : <div className="muted-md">No task executing — manager is {dash.status}.</div>}
            <div style={{ marginTop: 10 }}>
              <div className="muted-sm">NEXT DECISION (rule engine, evaluated live)</div>
              <div style={{ fontWeight: 600, marginTop: 2 }}>
                {nextAction?.decision ?? '—'}
              </div>
              {nextAction?.reason && <div className="muted-sm">{nextAction.reason}</div>}
            </div>
            <div className="muted-sm" style={{ marginTop: 8 }}>
              Next wake: {dash.next_wake_at ? new Date(dash.next_wake_at).toLocaleString() : 'on next event or heartbeat check'}
            </div>
          </section>

          {/* ── INVENTORY ─────────────────────────────────────── */}
          <section className="panel">
            <div className="section-title">Content inventory</div>
            <div className="grid-4">
              {[
                ['Approved images', inv.approved_images], ['Approved videos', inv.approved_videos],
                ['Packs', inv.packs], ['Days of content', inv.days_of_content != null ? Number(inv.days_of_content).toFixed(1) : '—'],
              ].map(([label, value]) => (
                <div key={label as string} className="panel" style={{ padding: 10 }}>
                  <div className="muted-sm">{label}</div>
                  <div className="stat-value">{value ?? 0}</div>
                </div>
              ))}
            </div>
            <div className="muted-sm" style={{ marginTop: 8 }}>
              Target: 7 days of content. Below target → the manager plans a shoot; at/above → IDLE.
            </div>
          </section>

          {/* ── PIPELINE ──────────────────────────────────────── */}
          <section className="panel">
            <div className="section-title">Production pipeline (per shoot)</div>
            {dash.pipeline.length === 0 && <div className="muted-md">No shoots planned yet.</div>}
            {dash.pipeline.map((p) => {
              const st = p.stages
              const open = expandedShoot === p.shoot_id
              return (
                <div key={p.shoot_id} className="list-row-compact" style={{ display: 'block' }}>
                  <div
                    style={{ display: 'flex', justifyContent: 'space-between', cursor: 'pointer', alignItems: 'center' }}
                    onClick={() => setExpandedShoot(open ? null : p.shoot_id)}
                  >
                    <span style={{ fontWeight: 600 }}>
                      Shoot {p.shoot_id.slice(0, 8)} — images {st.images_approved}/{st.images_total} approved
                      {st.images_failed > 0 ? `, ${st.images_failed} failed` : ''}
                    </span>
                    <span className="muted-sm">{open ? '▴' : '▾'}</span>
                  </div>
                  {open && (
                    <div style={{ marginTop: 6 }}>
                      {p.shots.map((s) => (
                        <div key={s.shot_id} className="muted-sm" style={{ display: 'flex', justifyContent: 'space-between', padding: '3px 0', borderTop: '1px solid var(--border)' }}>
                          <span>#{s.number} {s.type}</span>
                          <span style={{ color: s.status === 'APPROVED' ? '#22c55e' : (s.status === 'PENDING' || s.status === 'GENERATING') ? '#3b82f6' : '#ef4444' }}>
                            {s.status}{s.qa ? ` · QA ${s.qa}` : ''}{s.retries > 0 ? ` · retries ${s.retries}` : ''}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </section>

          {/* ── JOBS ──────────────────────────────────────────── */}
          <section className="panel">
            <div className="section-title">Active / pending jobs</div>
            {dash.active_jobs.length === 0 && dash.pending_jobs.length === 0 && <div className="muted-md">No running or pending jobs.</div>}
            {[...dash.active_jobs, ...dash.pending_jobs].map((t) => <TaskRow key={t.id} t={t} />)}
            {dash.failed_jobs.length > 0 && (
              <>
                <div className="section-title" style={{ marginTop: 12 }}>Failed / blocked</div>
                {dash.failed_jobs.map((t) => <TaskRow key={t.id} t={t} />)}
              </>
            )}
          </section>

          {/* ── DECISIONS + EVENTS ────────────────────────────── */}
          <section className="panel">
            <div className="section-title">Recent decisions</div>
            {dash.recent_decisions.length === 0 && <div className="muted-md">No decisions recorded yet.</div>}
            {dash.recent_decisions.map((d, i) => (
              <div key={i} className="muted-sm" style={{ padding: '4px 0', borderBottom: '1px solid var(--border)' }}>
                <span style={{ color: 'var(--text-muted)' }}>{new Date(d.at).toLocaleTimeString()}</span> — {d.message}
              </div>
            ))}
          </section>
          <section className="panel">
            <div className="section-title">Manager event log</div>
            {dash.event_log.length === 0 && <div className="muted-md">No events yet.</div>}
            {dash.event_log.slice(0, 12).map((e, i) => (
              <div key={i} className="muted-sm" style={{ padding: '3px 0' }}>
                <span style={{ color: 'var(--text-muted)' }}>{new Date(e.at).toLocaleTimeString()}</span> [{e.kind}] {e.message}
              </div>
            ))}
          </section>
        </div>
      )}

      {!dash && selected && <div className="panel muted-md">Loading manager dashboard…</div>}
    </div>
  )
}
