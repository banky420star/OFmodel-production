'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { createPersona, getJob, rebuildPersona } from '@/lib/api'
import { Icons } from '@/lib/icons'

type JobStatus = {
  id: string
  status: string
  progress: number
  message: string
  // Returned by GET /jobs/{id}. persona_id was missing from this type and from
  // every setJob call, so the redirect below always interpolated `undefined` and
  // sent every completed build to `/personas/` — the list — instead of the model
  // that had just been built.
  persona_id?: string | null
  // The workflow engine now writes these into job metadata; before that they were
  // declared on the API response and populated by nothing.
  current_step?: number | null
  total_steps?: number | null
}

const STEPS = [
  'Generating identity candidates',
  'Approving identity',
  'Building reference dataset',
  'Training LoRA model',
  'Validating identity',
  'Creating voice profile',
  'Activating persona',
]

export default function CreateModelPage() {
  const router = useRouter()
  const [creating, setCreating] = useState(false)
  const [job, setJob] = useState<JobStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const [form, setForm] = useState({
    name: '', age: 24, hair: 'long blonde', eyeColour: 'blue',
    brand: 'luxury lifestyle', personality: 'confident, playful',
    voiceStyle: 'South African English', publishingFrequency: '5 packs/week',
  })

  const jobId = job?.id || null

  // Poll job progress while a build is running. Keyed on the job id alone: the
  // previous effect depended on the whole `job` object and set it on every
  // response, so each poll tore down its own interval and built a new one — the
  // cadence was never the 1500 ms it claimed to be.
  useEffect(() => {
    if (!jobId) return
    let stopped = false
    const tick = async () => {
      try {
        const data = await getJob(jobId) as any
        if (stopped) return
        setJob({
          id: data.id, status: data.status, progress: data.progress,
          message: data.message, persona_id: data.persona_id,
          current_step: data.current_step, total_steps: data.total_steps,
        })
        if (data.status === 'completed' || data.status === 'failed') {
          stopped = true
          clearInterval(timer)
        }
      } catch {}
    }
    const timer = setInterval(tick, 1500)
    tick()
    return () => { stopped = true; clearInterval(timer) }
  }, [jobId])

  // Redirect to the model that was just built. persona_id comes from the job; if
  // the API ever omits it, fall back to the Models list rather than navigating to
  // `/personas/` with an empty id, which is what this did on every build.
  useEffect(() => {
    if (job?.status !== 'completed') return
    setDone(true)
    const target = job.persona_id ? `/personas/${job.persona_id}` : '/models'
    const timer = setTimeout(() => router.push(target), 1500)
    return () => clearTimeout(timer)
  }, [job?.status, job?.persona_id, router])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.name) return
    setCreating(true)
    setError(null)
    try {
      const persona = await createPersona({
        name: form.name, age: form.age,
        description: `${form.brand} creator`,
        adult_verified: true, synthetic_identity: true,
        appearance: {
          hair: form.hair,
          hair_length: form.hair.split(' ')[0] || 'long',
          hair_colour: form.hair.split(' ').slice(1).join(' ') || 'blonde',
          eye_colour: form.eyeColour,
          style_preferences: [form.brand],
        },
        personality: form.personality.split(',').map(s => s.trim()),
        brand: form.brand, voice_style: form.voiceStyle,
        publishing_frequency: form.publishingFrequency,
      }) as any
      // Start tracking the job
      if (persona.job_id) {
        setJob({ id: persona.job_id, status: 'running', progress: 0, message: 'Starting build…' })
      } else {
        // No job — persona was created instantly (shouldn't happen but handle gracefully)
        setDone(true)
        setTimeout(() => router.push(`/personas/${persona.id}`), 1000)
      }
    } catch (err: any) {
      setError(err.message || 'Failed to create persona')
    } finally { setCreating(false) }
  }

  const update = (field: string, value: any) => setForm(prev => ({ ...prev, [field]: value }))
  const inputCls = 'w-full bg-[var(--bg-card)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-white focus:border-[var(--green)] focus:outline-none text-sm'
  const labelCls = 'block text-xs font-medium text-[var(--text-secondary)] mb-1'

  // ── Progress view ──────────────────────────────────────
  if (job && !done) {
    // Trust the engine's own step counter when it is present. Deriving the step
    // from `progress` (as this did) reads as step 1 of 7 through the whole first
    // seventh of a build and could contradict the `message` printed above it,
    // which has always carried the real "Step 3/7: …" text.
    const stepCount = job.total_steps && job.total_steps > 0 ? job.total_steps : STEPS.length
    const stepIndex = job.current_step
      ? Math.min(job.current_step, stepCount) - 1
      : Math.min(Math.floor((job.progress / 100) * stepCount), stepCount - 1)
    const stepFinished = (job.message || '').trimEnd().endsWith('✓')
    const currentStep = stepIndex
    return (
      <main className="workspace">
        <header className="topbar">
          <div className="crumb">
            <a href="/"><span>Persona Studio</span></a><b>/</b>
            <span>Models</span><b>/</b><strong>Create</strong>
          </div>
        </header>
        <div className="content" style={{ maxWidth: 600, display: 'flex', flexDirection: 'column', alignItems: 'center', paddingTop: 80 }}>
          <div style={{ width: '100%' }}>
            <h1 style={{ fontSize: 22, fontWeight: 600, marginBottom: 8 }}>Building {form.name}…</h1>
            <p style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 24 }}>{job.message}</p>

            {/* Progress bar */}
            <div style={{ background: 'var(--border)', borderRadius: 8, height: 8, overflow: 'hidden', marginBottom: 24 }}>
              <div style={{
                background: job.status === 'failed' ? '#ef4444' : 'var(--green)',
                height: '100%', borderRadius: 8,
                width: `${job.progress}%`,
                transition: 'width 0.5s ease',
              }} />
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 32 }}>
              <span style={{ fontSize: 13, color: job.status === 'failed' ? '#ef4444' : 'var(--green)', fontWeight: 600 }}>
                {job.progress}%
              </span>
              <span style={{ fontSize: 13, color: 'var(--muted)' }}>
                {job.status === 'failed' ? 'Build failed' : `Step ${Math.min(currentStep + 1, stepCount)} of ${stepCount}`}
              </span>
            </div>

            {/* Step checklist */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {STEPS.map((step, i) => {
                // A step is done when the engine has moved past it, or when it is
                // the current one and its message is ticked off.
                const isComplete = job.current_step
                  ? i < currentStep || (i === currentStep && stepFinished)
                  : job.progress > ((i / stepCount) * 100)
                const isCurrent = i === currentStep && !isComplete && job.status !== 'failed'
                return (
                  <div key={step} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', borderRadius: 6, background: isCurrent ? 'rgba(217,251,113,0.08)' : 'transparent' }}>
                    <span style={{
                      width: 20, height: 20, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: 11, fontWeight: 600, flexShrink: 0,
                      background: isComplete ? 'var(--green)' : isCurrent ? 'rgba(217,251,113,0.2)' : 'var(--border)',
                      color: isComplete ? '#0c0e12' : isCurrent ? 'var(--green)' : 'var(--muted)',
                    }}>
                      {isComplete ? '✓' : i + 1}
                    </span>
                    <span style={{ fontSize: 13, color: isComplete ? 'var(--text-primary)' : isCurrent ? 'var(--green)' : 'var(--muted)' }}>
                      {step}
                    </span>
                    {isCurrent && <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--green)' }}>…</span>}
                  </div>
                )
              })}
            </div>

            {job.status === 'failed' && (
              <div style={{ marginTop: 24, padding: 16, background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 8 }}>
                <p style={{ color: '#ef4444', fontSize: 13, marginBottom: 8 }}>Build failed: {job.message}</p>
                <button
                  disabled={retrying || !job.persona_id}
                  onClick={async () => {
                    if (!job.persona_id) return
                    setRetrying(true)
                    setError(null)
                    try {
                      // Re-run the build that failed rather than submitting the
                      // form again: the persona already exists, so a second
                      // POST /personas with the same name is a 409 and the old
                      // "Try again" could never have succeeded.
                      const res = await rebuildPersona(job.persona_id) as any
                      setJob({
                        id: res.job_id, status: 'running', progress: 0,
                        message: 'Restarting build…', persona_id: job.persona_id,
                        current_step: null, total_steps: null,
                      })
                    } catch (err: any) {
                      setError(err.message || 'Could not restart the build')
                    } finally { setRetrying(false) }
                  }}
                  className="secondary-button" style={{ fontSize: 12 }}>
                  {retrying ? 'Restarting…' : 'Retry build'}
                </button>
              </div>
            )}
          </div>
        </div>
      </main>
    )
  }

  // ── Success view ──────────────────────────────────────
  if (done) {
    return (
      <main className="workspace">
        <header className="topbar">
          <div className="crumb">
            <a href="/"><span>Persona Studio</span></a><b>/</b>
            <span>Models</span><b>/</b><strong>Create</strong>
          </div>
        </header>
        <div className="content" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '60vh' }}>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 48, marginBottom: 16 }}>✓</div>
            <h2 style={{ fontSize: 20, fontWeight: 600, marginBottom: 8 }}>Build complete!</h2>
            <p style={{ color: 'var(--muted)', fontSize: 13 }}>Redirecting to {form.name}…</p>
          </div>
        </div>
      </main>
    )
  }

  // ── Form view ──────────────────────────────────────
  return (
    <main className="workspace">
      <header className="topbar">
        <div className="crumb">
          <a href="/"><span>Persona Studio</span></a><b>/</b>
          <span>Models</span><b>/</b><strong>Create</strong>
        </div>
      </header>
      <div className="content" style={{ maxWidth: 600 }}>
        <h1 style={{ fontSize: 24, fontWeight: 600, marginBottom: 24 }}>Create Model</h1>
        {error && (
          <div style={{ padding: '10px 14px', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 8, marginBottom: 16, fontSize: 13, color: '#ef4444' }}>
            {error}
          </div>
        )}
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <label className={labelCls}>Name</label>
            <input type="text" required value={form.name} onChange={e => update('name', e.target.value)}
              className={inputCls} placeholder="e.g. Ava" />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label className={labelCls}>Age</label>
              <input type="number" min={18} max={99} value={form.age}
                onChange={e => update('age', parseInt(e.target.value))} className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>Eye Colour</label>
              <select value={form.eyeColour} onChange={e => update('eyeColour', e.target.value)} className={inputCls}>
                {['blue', 'green', 'hazel', 'brown', 'grey'].map(c => <option key={c}>{c}</option>)}
              </select>
            </div>
          </div>
          <div>
            <label className={labelCls}>Hair</label>
            <input type="text" value={form.hair} onChange={e => update('hair', e.target.value)} className={inputCls} />
          </div>
          <div>
            <label className={labelCls}>Brand</label>
            <input type="text" value={form.brand} onChange={e => update('brand', e.target.value)} className={inputCls} />
          </div>
          <div>
            <label className={labelCls}>Personality (comma-separated)</label>
            <input type="text" value={form.personality} onChange={e => update('personality', e.target.value)} className={inputCls} />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label className={labelCls}>Voice Style</label>
              <input type="text" value={form.voiceStyle} onChange={e => update('voiceStyle', e.target.value)} className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>Publishing Frequency</label>
              <input type="text" value={form.publishingFrequency} onChange={e => update('publishingFrequency', e.target.value)} className={inputCls} />
            </div>
          </div>
          <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8, padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {[
              'Adult verification: Required (18+)',
              'Synthetic identity only — no real person data',
              'Training data requires explicit rights',
            ].map(text => (
              <div key={text} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--green)', flexShrink: 0 }} />
                <span style={{ color: 'var(--text-secondary)' }}>{text}</span>
              </div>
            ))}
          </div>
          <button type="submit" disabled={creating || !form.name}
            style={{
              background: 'var(--green)', color: '#0c0e12', padding: '10px 0', borderRadius: 8,
              fontWeight: 600, fontSize: 13, opacity: creating || !form.name ? .5 : 1, border: 'none', cursor: 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
            }}>
            {creating ? 'Starting build…' : 'Build model'}
          </button>
        </form>
      </div>
    </main>
  )
}
