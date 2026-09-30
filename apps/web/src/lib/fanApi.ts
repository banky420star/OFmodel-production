// The fan-facing API client.
//
// Separate from `api.ts` for one reason that matters more than tidiness:
// **every call here sends credentials.** `apiFetch` in `api.ts` does not, and
// it cannot be changed without touching the operator routes that call it — so
// the fan surface gets its own client rather than a shared one with a flag.
//
// The failure mode this prevents is worth stating: `fetch` defaults to
// `credentials: 'same-origin'`, which *does* send cookies through the Next
// rewrite. So a missing `credentials` would look fine in local development and
// break the moment the API is called cross-origin — as a fan silently signed
// out, on every request, with no error to point at.

const API_URL = process.env.NEXT_PUBLIC_API_URL || ''

export class FanApiError extends Error {
  status: number
  /** The 402/403/503 cases the UI renders as a state rather than a failure. */
  constructor(status: number, message: string) {
    super(message)
    this.name = 'FanApiError'
    this.status = status
  }
}

function detailToMessage(status: number, text: string): string {
  let detail: unknown = text
  try {
    detail = JSON.parse(text).detail ?? text
  } catch {
    // Not JSON — keep the raw text. A proxy error page is still better than
    // "Something went wrong".
  }
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((d: any) => d?.msg?.replace(/^Value error, /, '') || JSON.stringify(d)).join('; ')
  }
  return JSON.stringify(detail)
}

async function fanFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_URL}/api/v1${path}`, {
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', ...options.headers },
      ...options,
    })
  } catch (err) {
    // A network failure is not a 500 from the API and must not be reported as
    // one — the difference is "the API is down" vs "the API said no".
    throw new FanApiError(0, 'Cannot reach the server. Is the API running?')
  }

  if (!res.ok) {
    throw new FanApiError(res.status, detailToMessage(res.status, await res.text()))
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

// ── types ─────────────────────────────────────────────────────────────

export interface FanUser {
  id: string
  email: string
  display_name: string
  date_of_birth: string
}

export interface FanPersona {
  id: string
  name: string
  age: number
  brand: string
  avatar_url: string
  bio: string
  synthetic_identity: boolean
  disclosure: string
  disclosure_is_ai: boolean
  money_notice: string
}

export interface SignupResponse {
  user: FanUser
  persona: FanPersona
  fan_id: string
  signup_credit_minor: number
  simulated: boolean
}

export interface ThreadMessage {
  id: string
  direction: 'inbound' | 'outbound'
  content: string
  intent: string
  served_by: string
  created_at: string
}

export interface WalletStatementRow {
  transaction_id: string
  kind: string
  memo: string
  amount_minor: number
  created_at: string
}

export interface FanWallet {
  balance_minor: number
  balance_display: string
  currency: string
  simulated: boolean
  notice: string
  statement: WalletStatementRow[]
}

export interface FanProduct {
  id: string
  title: string
  description: string
  price_minor: number
  price_display: string
  kind: string
  is_adult: boolean
  unlocked: boolean
  reason: string
  min_tier_rank: number
  media_count: number
  cover_url: string
}

export interface FanPlan {
  code: string
  name: string
  price_minor: number
  price_display: string
  period_days: number
  rank: number
  perks: string
}

export interface ActivityEvent {
  action: string
  actor: string
  object_type: string
  object_id: string
  detail: Record<string, unknown>
  created_at: string | null
}

export interface AccessEventRow {
  product_id: string | null
  allowed: boolean
  reason: string
  created_at: string | null
}

// ── auth ──────────────────────────────────────────────────────────────

export const signup = (data: {
  email: string
  password: string
  display_name?: string
  date_of_birth: string
}) => fanFetch<SignupResponse>('/auth/signup', { method: 'POST', body: JSON.stringify(data) })

export const login = (data: { email: string; password: string }) =>
  fanFetch<{ user: FanUser; persona: FanPersona }>('/auth/login', {
    method: 'POST',
    body: JSON.stringify(data),
  })

export const logout = () => fanFetch<{ ok: boolean }>('/auth/logout', { method: 'POST' })

export const me = () =>
  fanFetch<{ user: FanUser; persona: FanPersona; fan_id: string }>('/auth/me')

// ── the fan's own surface ─────────────────────────────────────────────

export const getPersona = () => fanFetch<FanPersona>('/fan/persona')

export const getThread = () =>
  fanFetch<{ messages: ThreadMessage[]; disclosure: string }>('/fan/thread')

export const sendMessage = (text: string) =>
  fanFetch<{ reply: string; intent: string; served_by: string; created_at: string }>(
    '/fan/messages',
    { method: 'POST', body: JSON.stringify({ text }) },
  )

export const getWallet = () => fanFetch<FanWallet>('/fan/wallet')

export const topup = (amountMinor: number) =>
  fanFetch<{ balance_minor: number; notice: string }>('/fan/wallet/topup', {
    method: 'POST',
    body: JSON.stringify({ amount_minor: amountMinor }),
  })

export const listProducts = () =>
  fanFetch<{ products: FanProduct[]; subscription: { plan_code: string; rank: number } | null; disclosure: string }>(
    '/fan/products',
  )

export const unlockProduct = (productId: string) =>
  fanFetch<{ balance_minor: number; already_owned: boolean; unlocked: boolean }>(
    `/fan/products/${productId}/unlock`,
    { method: 'POST' },
  )

export const getContent = (productId: string) =>
  fanFetch<{
    id: string
    title: string
    description: string
    disclosure: string
    media: { index: number; caption: string; url: string }[]
  }>(`/fan/content/${productId}`)

/** A media URL for an <img src>. Relative, so it goes through the rewrite. */
export const mediaUrl = (productId: string, index: number) =>
  `${API_URL}/api/v1/fan/content/${productId}/media/${index}`

export const listPlans = () => fanFetch<{ plans: FanPlan[]; simulated: boolean; notice: string }>(
  '/fan/subscription/plans',
)

export const subscribe = (planCode: string) =>
  fanFetch<{ balance_minor: number; plan_code: string }>('/fan/subscription', {
    method: 'POST',
    body: JSON.stringify({ plan_code: planCode }),
  })

export const getActivity = () =>
  fanFetch<{ events: ActivityEvent[]; accesses: AccessEventRow[] }>('/fan/activity')

// ── formatting ────────────────────────────────────────────────────────

export function money(minor: number): string {
  return `$${(minor / 100).toFixed(2)}`
}

export function when(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}
