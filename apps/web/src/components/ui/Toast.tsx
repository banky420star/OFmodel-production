'use client'

import { useEffect } from 'react'

export type ToastState = { msg: string; type: 'success' | 'error' } | null

/**
 * Fixed-position action feedback. With `onDismiss`, it dismisses itself
 * after 4s (success) / 5s (error); callers that manage their own timers
 * (e.g. production) may omit it.
 */
export function Toast({ toast, onDismiss }: { toast: ToastState; onDismiss?: () => void }) {
  useEffect(() => {
    if (!toast || !onDismiss) return
    const t = setTimeout(onDismiss, toast.type === 'success' ? 4000 : 5000)
    return () => clearTimeout(t)
  }, [toast, onDismiss])

  if (!toast) return null
  return (
    <div
      role="status"
      style={{
        position: 'fixed', bottom: 24, right: 24, zIndex: 999,
        padding: '12px 20px', borderRadius: 8, fontSize: 13, fontWeight: 500,
        background: toast.type === 'success' ? 'var(--green)' : '#ef4444',
        color: toast.type === 'success' ? '#0c0e12' : '#fff',
        boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
        animation: 'slideUp 0.3s ease',
        maxWidth: 420,
      }}
    >
      {toast.type === 'success' ? '✓' : '✗'} {toast.msg}
    </div>
  )
}
