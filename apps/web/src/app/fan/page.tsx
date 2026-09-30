'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { FanApiError, listPlans, login, me, signup, type FanPlan } from '@/lib/fanApi'

// The landing page and the door.
//
// One screen does both sign-up and log-in, because a visitor who has to choose
// between two forms before they have an account is being asked a question they
// cannot answer yet. The date of birth field is on the sign-up side and is
// labelled as the age check it is, not buried as "additional details".

type Mode = 'signup' | 'login'

export default function FanLanding() {
  const router = useRouter()
  const [mode, setMode] = useState<Mode>('signup')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [dob, setDob] = useState('')
  const [plans, setPlans] = useState<FanPlan[]>([])
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  // Already signed in? Straight to the chat.
  //
  // Deliberately NOT gated behind a "checking…" state. Gating it meant the
  // server-rendered HTML for this page was nothing but a spinner — a
  // first-time visitor on a slow connection saw a blank screen where the
  // explanation of what this site is should be. The hero and the form render
  // immediately; a signed-in visitor is redirected a moment later.
  useEffect(() => {
    me()
      .then(() => router.replace('/fan/chat'))
      .catch(() => {
        // Not signed in, which is the normal case for this page.
      })
  }, [router])

  useEffect(() => {
    listPlans()
      .then((data) => setPlans(data.plans))
      .catch(() => {
        // The price list is a nicety, not the door. If it fails the form still
        // has to work.
      })
  }, [])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setNotice('')
    try {
      if (mode === 'signup') {
        await signup({
          email: email.trim(),
          password,
          display_name: displayName.trim(),
          date_of_birth: dob,
        })
      } else {
        await login({ email: email.trim(), password })
      }
      router.replace('/fan/chat')
    } catch (err) {
      if (err instanceof FanApiError && err.status === 403) {
        // A refusal, phrased as a sentence rather than "403". Someone typing
        // the wrong year should not read a status code.
        setNotice(
          'This service is for adults only — the date of birth you entered is ' +
          'under 18, so no account was created.',
        )
      } else if (err instanceof FanApiError && err.status === 409) {
        setNotice('That email already has an account. Try signing in instead.')
      } else if (err instanceof FanApiError && err.status === 401) {
        setNotice('That email and password do not match an account.')
      } else {
        setNotice(err instanceof Error ? err.message : 'Something went wrong.')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fan-landing">
      <div className="fan-landing-hero">
        <span className="fan-ai-tag fan-ai-tag-lg">AI</span>
        <h1>
          She&apos;s not a person.
          <br />
          She&apos;s still good company.
        </h1>
        <p className="fan-hero-sub">
          Every creator here is an AI character. No human is on the other end,
          the pictures are synthetic, and the money is simulated — nothing is
          charged and nothing is hidden.
        </p>

        <ul className="fan-hero-facts">
          <li>
            <b>Disclosed, always.</b> She will tell you she is an AI if you ask,
            and the AI badge stays on screen the whole time.
          </li>
          <li>
            <b>Simulated money.</b> You start with $5.00 of credit. No card, no
            charge, nothing to redeem.
          </li>
          <li>
            <b>18+ only.</b> A date of birth is required and under-18 sign-ups
            are refused.
          </li>
        </ul>

        {plans.length > 0 && (
          <div className="fan-plan-preview">
            {plans.map((plan) => (
              <div key={plan.code} className="fan-plan-chip">
                <b>{plan.name}</b>
                <span>${plan.price_display}/mo</span>
                <small>{plan.perks}</small>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="fan-landing-form">
        <div className="fan-tabs">
          <button
            className={mode === 'signup' ? 'active' : ''}
            onClick={() => { setMode('signup'); setNotice('') }}
          >
            Create account
          </button>
          <button
            className={mode === 'login' ? 'active' : ''}
            onClick={() => { setMode('login'); setNotice('') }}
          >
            Sign in
          </button>
        </div>

        <form onSubmit={submit}>
          <label>
            Email
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
            />
          </label>

          {mode === 'signup' && (
            <>
              <label>
                What should she call you?
                <input
                  type="text"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="optional"
                />
              </label>

              <label>
                Date of birth
                <input
                  type="date"
                  required
                  value={dob}
                  onChange={(e) => setDob(e.target.value)}
                  max={new Date().toISOString().slice(0, 10)}
                />
                <small className="fan-field-note">
                  Used once, to confirm you are 18 or over. Records are kept for
                  the age check and nothing else.
                </small>
              </label>
            </>
          )}

          <label>
            Password
            <input
              type="password"
              required
              autoComplete={mode === 'signup' ? 'new-password' : 'current-password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={mode === 'signup' ? 'at least 8 characters' : ''}
            />
          </label>

          <button type="submit" className="fan-btn-primary" disabled={busy}>
            {busy ? 'One moment…' : mode === 'signup' ? 'Create account' : 'Sign in'}
          </button>
        </form>

        {notice && <p className="fan-notice">{notice}</p>}

        <p className="fan-fineprint">
          Simulated money. Synthetic images. An AI you are told is an AI.
        </p>
      </div>
    </div>
  )
}
