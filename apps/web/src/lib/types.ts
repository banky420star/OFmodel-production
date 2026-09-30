/**
 * A persona as `GET /dashboard/summary` returns it. That route adds two fields
 * the plain `PersonaResponse` does not carry — `shoots_count` (counted from the
 * shoots table) and nothing else — so this is not the shape of `GET /personas`.
 * The two agreed by accident before: both returned the same defaults for the
 * identity fields because nothing populated them anywhere.
 */
export interface PersonaDetail {
  id: string; name: string; age: number; status: string
  brand: string; identity_score: number | null; identity_status: string | null
  // `match` / `mismatch` / `unknown` for the trained adapter's base against the
  // render checkpoint; empty when the persona has no adapter. `unknown` is not a
  // pass — a build whose base predates the record cannot be shown to match.
  identity_lora_base_state: string; identity_lora_base_note: string
  packs_count: number; shoots_count: number; avatar_url: string
}

export interface HealthCheck {
  service: string; status: string
}

/**
 * A shoot as `GET /dashboard/summary` returns it. `asset_type` and `progress`
 * are added by that route and are absent from `ShootResponse`, which is what
 * `GET /shoots` and `GET /personas/{id}/shoots` return — so a value typed as
 * ShootDetail that came from those endpoints is missing both. `generated_count`
 * was declared here and returned by no endpoint at all; the count is `image_count`.
 */
export interface ShootDetail {
  id: string; name: string; status: string; asset_type: string
  progress: number; image_count: number
  generated_images: string[]
  theme: string; persona_name: string; created_at: string | null
}

export interface AttentionItem {
  id: string; name: string; type: string; status: string
}

/**
 * One bucket of the platform's own ledger — `overTime` on the earnings summary.
 * This is a real series (Fanvue buckets its own accounting by day or week), which
 * is what the old hardcoded sparkline pretended to be. `period_start` is the
 * platform's own bucket boundary; a bucket it did not timestamp is dropped
 * server-side rather than plotted at an invented x-position.
 */
export interface RevenuePoint {
  period_start: string
  gross: number | null
  net: number | null
}

/**
 * What the connected platform has actually paid, as `app.earnings.headline`
 * shapes it. Every figure is `null` when the platform did not report it — a
 * `0` here would mean "we earned nothing", which is a different and much more
 * comfortable fact than "we could not ask".
 *
 * `currency` is the code the figures are in, or `""` when the structure did not
 * carry one. Render it through `formatMoney`; `formatCurrency` (the studio's own
 * Rand presentation) must not be used on these, because a right number under the
 * wrong symbol reads as a fact.
 */
export interface RealRevenue {
  gross: number | null
  net: number | null
  this_month_net: number | null
  previous_month_net: number | null
  currency: string
  /** Net per source, largest first. Paid DMs are the engine on Fanvue. */
  by_source: Record<string, number>
  /** Sum of the sources the platform reported, or `null` if it reported none. */
  sources_total: number | null
  /** The platform's ledger over the window, oldest bucket first. Often empty. */
  over_time: RevenuePoint[]
  /** The bucket size and window `over_time` is aggregated at. `{}` if unknown. */
  period: { startDate?: string | null; endDate?: string | null; granularity?: string; timezone?: string }
}

/**
 * One conversation in the connected platform's inbox, as
 * `app.conversations` reads it. A *reading*: this app never replies, never
 * marks read, never changes anything on the account — `can_send` is false on
 * every response and will stay false until someone deliberately builds the
 * sending side, which messages real paying fans as the creator.
 *
 * `last_message_is_paid` identifies a paid message by its type
 * (`LOCKED_MESSAGE_UNLOCKED`, `TIP`). It carries **no amount**: the chat record
 * has no price field, so the size of a sale is only knowable from the earnings
 * rows. Never infer a figure from it.
 */
export interface ConversationRow {
  user_uuid: string | null
  handle: string | null
  display_name: string | null
  is_top_spender: boolean
  /**
   * The badge. `unread_messages` is 0 for a conversation the creator marked
   * unread by hand, so the count alone hides it and this alone is authoritative.
   */
  is_read: boolean
  is_muted: boolean
  unread_messages: number
  online: boolean
  last_message_at: string | null
  last_message_text: string | null
  last_message_type: string | null
  last_message_is_paid: boolean
  last_message_from_creator: boolean
}

/**
 * The inbox as the platform reports it, or why that is unknown.
 *
 * `chats` and `counts` are `null` for every state but `ok`. An empty list would
 * read as "no fan has written", which is a different and more comfortable fact
 * than "we could not ask" — so the reader refuses to express the second one with
 * the first one's shape.
 */
export interface ConversationsReading {
  state: 'ok' | 'not_configured' | 'disabled' | 'unsupported' | 'forbidden' | 'error' | string
  provider: string
  detail: string
  counts: { unreadChatsCount?: number; unreadMessagesCount?: number } | null
  chats: ConversationRow[] | null
  note: string
  can_send: false
}

/**
 * One launch gate, as `app.readiness` measures it. `next_step` is present
 * exactly when the gate is not clear — a red light with a lever.
 */
export interface ReadinessCheck {
  key: string
  label: string
  done: boolean
  detail: string
  next_step: string
}

export interface MonetizationReadiness {
  ready: boolean
  platform: string
  checks: ReadinessCheck[]
  blockers: string[]
  note: string
}

/** Why the ledger is unreadable, when it is. Each one is a different next action. */
export type RealRevenueState =
  | 'ok' | 'not_configured' | 'disabled' | 'unsupported' | 'error' | string

export interface DashboardSummary {
  active_models: number
  training_models: number
  total_models: number
  total_packs: number
  total_shoots: number
  /**
   * Recorded analytics revenue — what `AnalyticsSnapshot` rows say, which the
   * Instagram and TikTok syncs write as `0` because neither API reports money.
   * Not payments. The figure to watch is `real_revenue`; `revenue_note` says so
   * in the API's own words and is rendered rather than paraphrased.
   */
  revenue: number
  revenue_note: string
  real_revenue: RealRevenue
  real_revenue_state: RealRevenueState
  real_revenue_detail: string
  followers: number
  engagement_rate: number
  health: { online: number; total: number; checks: HealthCheck[] }
  attention_items: AttentionItem[]
  shoots: ShootDetail[]
  personas: PersonaDetail[]
}

// ── Model manager ────────────────────────────────────────────────────
// Derived from what each account actually has, never from what a row implies.
export interface ManagerAccount {
  account_id: string
  platform: string
  username: string
  display_name: string
  email: string
  status: string
  manual_signup: boolean
  signup_step: string
  packet_ready: boolean
  has_inbox: boolean
  has_password: boolean
  credentials_saved: boolean
  approved_by: string
  api_connected: boolean
  profile_url: string
  followers: number
  posts_count: number
  blockers: string[]
  next_action: string
  next_action_detail: string
  last_posted_at: string | null
}

export interface ManagerRow {
  persona_id: string
  name: string
  status: string
  adult_verified: boolean
  synthetic_identity: boolean
  warnings: string[]
  accounts: ManagerAccount[]
  platforms_covered: number
  platforms_missing: string[]
  accounts_needing_work: number
  next_action: string
}

export interface ManagerRoster {
  platforms: string[]
  roster: ManagerRow[]
  summary: {
    models: number
    accounts: number
    live: number
    needing_work: number
    not_requested: number
    by_next_action: Record<string, number>
  }
  note: string
}

// ── Divisions ─────────────────────────────────────────────────────────
// The studio's operating structure. `maturity` is set by whether the
// entrypoint reaches a real provider, never by whether a page exists for it:
//   live    — wired end to end
//   partial — the record and the provider exist; the loop between them does not
//   unbuilt — named so it can be seen missing; nothing runs it yet
// `can_act` is a separate question from maturity: a division with every
// provider configured still cannot act if it is unbuilt.
export type DivisionMaturity = 'live' | 'partial' | 'unbuilt'

export interface Division {
  key: string
  name: string
  owns: string
  produces: string[]
  cadence: string
  requires: string[]
  routes: string[]
  workflow: string
  maturity: DivisionMaturity
  gap: string
  capabilities_ready: string[]
  capabilities_missing: string[]
  publish_ready: boolean | null
  can_act: boolean
  blocked_by: string[]
  counts: Record<string, number>
  counts_error: string
}

export interface SchedulerStatus {
  enabled: boolean
  running: boolean
  interval_seconds: number
  max_per_tick: number
  max_lateness_seconds: number
  ticks: number
  last_tick_at: string | null
  next_tick_at: string | null
  due_now: number
  published_total: number
  failed_total: number
  missed_total: number
  blocked: string
  blocked_detail: string
  last_error: string
  last_actions: Array<Record<string, unknown>>
  note: string
}

export interface DivisionsResponse {
  divisions: Division[]
  summary: {
    total: number
    can_act: number
    blocked: number
    by_maturity: Record<string, number>
    scheduled_divisions: number
  }
  scheduler: SchedulerStatus
  note: string
}

// ── The whole book (GET /manager/business) ──────────────────────────────
//
// Every money figure is `number | null`, never `number`. `null` means the
// platform did not report it, which is a different fact from earning nothing —
// see `formatMoney`, which renders null as an em dash rather than R 0.00.

export interface BusinessMoney {
  state: 'ok' | 'not_configured' | 'disabled' | 'unsupported' | 'error' | string
  provider: string
  detail: string
  note: string
  is_real: boolean
  headline: {
    gross: number | null
    net: number | null
    this_month_net: number | null
    previous_month_net: number | null
    currency: string
    by_source: Record<string, number>
    sources_total: number | null
    over_time: Array<Record<string, unknown>>
    period: Record<string, unknown>
  }
}

export interface BusinessGate {
  key: string
  label: string
  done: boolean
  detail: string
  next_step: string
}

export interface BusinessPersona {
  persona_id: string
  name: string
  status: string
  adult_verified: boolean
  synthetic_identity: boolean
  publishable_accounts: string[]
  content: { shoots_completed: number; packs: number }
  // `scheduled` counts only slots on a platform this app can publish to;
  // `stranded` counts the rest, which can never ship whatever they carry.
  calendar: { scheduled: number; priced: number; shippable: number; stranded: number }
  // The sum of the prices on posts that could actually ship. Not revenue, and
  // not a forecast.
  shelf_value: number
  can_ship: boolean
  blocked_by: string[]
  blocked_by_detail: string[]
}

export interface BusinessState {
  money: BusinessMoney
  gates: { checks: BusinessGate[]; blockers: string[]; ready: boolean; note: string }
  personas: BusinessPersona[]
  stranded_slots: { count: number; platforms: string[]; note: string }
  summary: {
    personas: number
    personas_that_can_ship: number
    accounts: number
    accounts_live: number
    scheduled_posts: number
    shippable_posts: number
    shelf_value: number
  }
  next_action: {
    key: string
    label: string
    detail: string
    gates_open: number
    gates_total: number
    also_blocked: string[]
    for_personas?: string[]
  }
  note: string
}
