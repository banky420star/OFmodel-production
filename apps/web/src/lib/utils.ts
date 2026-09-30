export function formatCurrency(n: number): string {
  return 'R\u00a0' + n.toLocaleString('en-ZA', { minimumFractionDigits: 0, maximumFractionDigits: 0 })
}

/**
 * A figure the connected platform reported, in the platform's own currency.
 *
 * `formatCurrency` above is the studio's own Rand presentation and stays that
 * way. Platform earnings are not necessarily Rand \u2014 the app's own wallet is
 * denominated in USD \u2014 so a figure from the platform is formatted with the code
 * the API reported, and with **no symbol at all** when the API did not report
 * one. Prefixing "R" to a number that is not in Rand would be a wrong number
 * wearing a right-looking symbol, which reads as a fact.
 *
 * `null` renders as an em dash, never as R 0.00: "we earned nothing" and "the
 * platform did not answer" are different facts and only one of them is news.
 */
export function formatMoney(value: number | null | undefined, currency = ''): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '\u2014'
  if (/^[A-Za-z]{3}$/.test(currency)) {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currency.toUpperCase(),
      maximumFractionDigits: 2,
    }).format(value)
  }
  return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function getGreeting(): string {
  const h = new Date().getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}

export function getDayString(): string {
  return new Date().toLocaleDateString('en-US', { weekday: 'long', day: 'numeric', month: 'long' })
}

export function statusColor(s: string) {
  if (s === 'completed' || s === 'ready' || s === 'approved' || s === 'assembled')
    return { bg: 'var(--green-dim)', fg: 'var(--green)' }
  if (s === 'running' || s === 'generating')
    return { bg: 'var(--amber-dim)', fg: 'var(--amber)' }
  if (s === 'failed')
    return { bg: 'var(--red-dim)', fg: 'var(--red)' }
  return { bg: 'var(--bg-card)', fg: 'var(--text-muted)' }
}
