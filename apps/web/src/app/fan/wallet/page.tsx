'use client'

import { useCallback, useEffect, useState } from 'react'
import {
  FanApiError, getActivity, getWallet, money, topup, when,
  type ActivityEvent, type FanWallet,
} from '@/lib/fanApi'

// The wallet.
//
// The word "simulated" appears on this page four times on purpose: in the
// banner, next to the balance, on the top-up button, and in the fine print.
// Money-shaped UI is the most likely thing on this site to be mistaken for
// real, so the labelling is not decoration.

const TOPUPS = [500, 1000, 2500]

export default function FanWalletPage() {
  const [wallet, setWallet] = useState<FanWallet | null>(null)
  const [activity, setActivity] = useState<ActivityEvent[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [flash, setFlash] = useState('')
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const [walletData, activityData] = await Promise.all([getWallet(), getActivity()])
      setWallet(walletData)
      setActivity(activityData.events)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the wallet.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  async function addFunds(amountMinor: number) {
    setBusy(true)
    setError('')
    setFlash('')
    try {
      const result = await topup(amountMinor)
      setFlash(`Added ${money(amountMinor)}. No card was charged.`)
      await refresh()
      setWallet((prev) => (prev ? { ...prev, balance_minor: result.balance_minor } : prev))
    } catch (err) {
      setError(err instanceof FanApiError ? err.message : 'Top-up failed.')
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return <div className="fan-center"><div className="fan-spinner" /></div>
  }

  return (
    <div className="fan-page">
      <h1>Wallet</h1>

      {wallet && (
        <>
          <div className="fan-sim-banner">
            <b>Simulated money.</b> {wallet.notice}
          </div>

          <div className="fan-balance-card">
            <div>
              <small>Balance (simulated)</small>
              <b className="fan-balance">{money(wallet.balance_minor)}</b>
            </div>
            <div className="fan-topup-row">
              {TOPUPS.map((amount) => (
                <button
                  key={amount}
                  className="fan-btn-ghost"
                  disabled={busy}
                  onClick={() => addFunds(amount)}
                >
                  + {money(amount)}
                </button>
              ))}
            </div>
          </div>

          {flash && <p className="fan-flash">{flash}</p>}
          {error && <p className="fan-error">{error}</p>}

          <h2>Statement</h2>
          {wallet.statement.length === 0 ? (
            <p className="fan-muted">Nothing yet.</p>
          ) : (
            <table className="fan-table">
              <thead>
                <tr><th>When</th><th>What</th><th className="right">Amount</th></tr>
              </thead>
              <tbody>
                {wallet.statement.map((row) => (
                  <tr key={row.transaction_id}>
                    <td className="fan-muted">{when(row.created_at)}</td>
                    <td>
                      <b>{row.kind.replace(/_/g, ' ')}</b>
                      {row.memo && <small className="fan-muted"> · {row.memo}</small>}
                    </td>
                    <td className={`right ${row.amount_minor < 0 ? 'fan-neg' : 'fan-pos'}`}>
                      {row.amount_minor > 0 ? '+' : ''}
                      {money(row.amount_minor)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h2>Activity</h2>
          {activity.length === 0 ? (
            <p className="fan-muted">Nothing yet.</p>
          ) : (
            <ul className="fan-activity">
              {activity.map((event, index) => (
                <li key={`${event.action}-${index}`}>
                  <span className="fan-activity-action">{event.action.replace(/_/g, ' ')}</span>
                  <span className="fan-muted">{when(event.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
