'use client'

import { statusColor } from '@/lib/utils'

export function StatusBadge({ status }: { status: string }) {
  const c = statusColor(status)
  return (
    <span style={{
      background: c.bg, color: c.fg,
      fontSize: 10, fontWeight: 700,
      padding: '3px 8px', borderRadius: '4px',
      letterSpacing: '.04em', textTransform: 'uppercase' as const,
    }}>{status}</span>
  )
}
