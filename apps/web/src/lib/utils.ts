export function formatCurrency(n: number): string {
  return 'R\u00a0' + n.toLocaleString('en-ZA', { minimumFractionDigits: 0, maximumFractionDigits: 0 })
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
