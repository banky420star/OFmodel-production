'use client'

import { useCallback, useEffect, useState } from 'react'
import {
  FanApiError, getContent, getWallet, listPlans, listProducts, mediaUrl, money,
  subscribe, unlockProduct,
  type FanPlan, type FanProduct,
} from '@/lib/fanApi'

// The content shelf.
//
// The locked state is a real state: a padlock, the price, and what unlocking
// would cost them. Clicking unlock twice must charge once, so the button goes
// down on click and the response's `already_owned` flag is what decides the
// message — not a local guess about whether the click was a repeat.

export default function FanContentPage() {
  const [products, setProducts] = useState<FanProduct[]>([])
  const [plans, setPlans] = useState<FanPlan[]>([])
  const [balance, setBalance] = useState(0)
  const [subscription, setSubscription] = useState<{ plan_code: string; rank: number } | null>(null)
  const [disclosure, setDisclosure] = useState('')
  const [busyId, setBusyId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [flash, setFlash] = useState('')
  const [viewing, setViewing] = useState<{ id: string; title: string; media: { index: number; caption: string }[] } | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const [shelf, planData, wallet] = await Promise.all([listProducts(), listPlans(), getWallet()])
      setProducts(shelf.products)
      setDisclosure(shelf.disclosure)
      setSubscription(shelf.subscription)
      setPlans(planData.plans)
      setBalance(wallet.balance_minor)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load content.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  async function unlock(product: FanProduct) {
    setBusyId(product.id)
    setError('')
    setFlash('')
    try {
      const result = await unlockProduct(product.id)
      setFlash(
        result.already_owned
          ? `You already own “${product.title}”. Nothing was charged.`
          : `Unlocked “${product.title}” for ${money(product.price_minor)}.`,
      )
      await refresh()
    } catch (err) {
      if (err instanceof FanApiError && err.status === 402) {
        setError(
          err.message.includes('enough')
            ? `Not enough credit — you have ${money(balance)} and it costs ${money(product.price_minor)}.`
            : err.message,
        )
      } else {
        setError(err instanceof Error ? err.message : 'Could not unlock.')
      }
    } finally {
      setBusyId(null)
    }
  }

  async function open(product: FanProduct) {
    setError('')
    try {
      const content = await getContent(product.id)
      setViewing({ id: content.id, title: content.title, media: content.media })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not open.')
    }
  }

  async function join(plan: FanPlan) {
    setError('')
    setFlash('')
    try {
      await subscribe(plan.code)
      setFlash(`Subscribed to ${plan.name}. Simulated — no card was charged.`)
      await refresh()
    } catch (err) {
      if (err instanceof FanApiError && err.status === 402) {
        setError(`Not enough credit for that plan — you have ${money(balance)}.`)
      } else {
        setError(err instanceof Error ? err.message : 'Could not subscribe.')
      }
    }
  }

  if (loading) {
    return <div className="fan-center"><div className="fan-spinner" /></div>
  }

  return (
    <div className="fan-page">
      <h1>Content</h1>
      <p className="fan-disclosure">{disclosure}</p>

      <div className="fan-shelf-head">
        <span className="fan-balance-inline">Balance {money(balance)}</span>
        {subscription && <span className="fan-pill">Subscribed · {subscription.plan_code}</span>}
      </div>

      {flash && <p className="fan-flash">{flash}</p>}
      {error && <p className="fan-error">{error}</p>}

      <div className="fan-grid">
        {products.map((product) => (
          <div key={product.id} className={`fan-card ${product.unlocked ? '' : 'locked'}`}>
            <div className="fan-card-cover">
              {product.unlocked && product.media_count > 0 ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={mediaUrl(product.id, 0)} alt={product.title} />
              ) : (
                <div className="fan-card-locked">
                  <span aria-hidden>🔒</span>
                  <small>Locked</small>
                </div>
              )}
            </div>

            <div className="fan-card-body">
              <b>{product.title}</b>
              <p className="fan-muted">{product.description}</p>

              <div className="fan-card-foot">
                {product.unlocked ? (
                  <>
                    <span className="fan-pos">
                      {product.min_tier_rank > 0 && subscription
                        ? 'Included with your plan'
                        : 'Unlocked'}
                    </span>
                    <button className="fan-btn-ghost" onClick={() => open(product)}>
                      Open · {product.media_count}
                    </button>
                  </>
                ) : (
                  <>
                    <span className="fan-price">{money(product.price_minor)}</span>
                    <button
                      className="fan-btn-primary"
                      disabled={busyId === product.id}
                      onClick={() => unlock(product)}
                    >
                      {busyId === product.id ? 'Unlocking…' : 'Unlock'}
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {plans.length > 0 && (
        <>
          <h2>Subscriptions</h2>
          <div className="fan-plans">
            {plans.map((plan) => (
              <div key={plan.code} className="fan-plan-chip">
                <b>{plan.name}</b>
                <span>{money(plan.price_minor)}/mo</span>
                <small>{plan.perks}</small>
                <button
                  className="fan-btn-ghost"
                  onClick={() => join(plan)}
                  disabled={subscription?.plan_code === plan.code}
                >
                  {subscription?.plan_code === plan.code ? 'Current plan' : 'Subscribe'}
                </button>
              </div>
            ))}
          </div>
          <p className="fan-fineprint">Simulated billing. No card is stored or charged.</p>
        </>
      )}

      {viewing && (
        <div className="fan-viewer" onClick={() => setViewing(null)}>
          <div className="fan-viewer-inner" onClick={(e) => e.stopPropagation()}>
            <header>
              <b>{viewing.title}</b>
              <button onClick={() => setViewing(null)} aria-label="Close">✕</button>
            </header>
            <div className="fan-viewer-grid">
              {viewing.media.map((item) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img key={item.index} src={mediaUrl(viewing.id, item.index)} alt={item.caption} />
              ))}
            </div>
            <p className="fan-fineprint">{disclosure}</p>
          </div>
        </div>
      )}
    </div>
  )
}
