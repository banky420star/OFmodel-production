// In production/Docker: use relative URLs — Next.js rewrite proxies /api/* to backend
// For local dev outside Docker: set NEXT_PUBLIC_API_URL=http://localhost:8000
const API_URL = process.env.NEXT_PUBLIC_API_URL || '';

async function apiFetch(path: string, options: RequestInit = {}) {
  const res = await fetch(`${API_URL}/api/v1${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    let detail: unknown = text;
    try { detail = JSON.parse(text).detail ?? text; } catch { /* keep raw text */ }
    const msg = typeof detail === 'string'
      ? detail
      : Array.isArray(detail)
        ? detail.map((d: any) => d?.msg?.replace(/^Value error, /, '') || JSON.stringify(d)).join('; ')
        : JSON.stringify(detail);
    throw new Error(`API ${res.status}: ${msg}`);
  }
  return res.json();
}

// Personas
export const createPersona = (data: any) => apiFetch('/personas', { method: 'POST', body: JSON.stringify(data) });
export const getPersona = (id: string) => apiFetch(`/personas/${id}`);
export const listPersonas = () => apiFetch('/personas');
// Re-run the identity build for a persona whose build did not finish. Returns
// {status, persona_id, job_id} — the job id is what the progress view polls.
export const rebuildPersona = (id: string) => apiFetch(`/personas/${id}/rebuild`, { method: 'POST' });

// Identities
export const listIdentities = (personaId: string) => apiFetch(`/personas/${personaId}/identities`);

// Shoots
export const createShoot = (personaId: string, data: any) =>
  apiFetch(`/personas/${personaId}/shoots`, { method: 'POST', body: JSON.stringify(data) });
export const listShoots = (personaId: string) => apiFetch(`/shoots?persona_id=${personaId}`);

// Content Packs
export const createPack = (personaId: string, data: any) =>
  apiFetch(`/personas/${personaId}/packs`, { method: 'POST', body: JSON.stringify(data) });
export const listPacks = (personaId: string) => apiFetch(`/personas/${personaId}/packs`);

// Workflows
export const listWorkflows = (params?: { persona_id?: string }) => {
  const qs = params?.persona_id ? `?persona_id=${params.persona_id}` : '';
  return apiFetch(`/workflows${qs}`);
};

// Analytics
export const getAnalytics = (personaId: string) => apiFetch(`/personas/${personaId}/analytics`);
export const generateAnalytics = (personaId: string) =>
  apiFetch(`/personas/${personaId}/analytics/generate`, { method: 'POST' });
export const syncInstagramAnalytics = (personaId: string) =>
  apiFetch(`/personas/${personaId}/analytics/sync`, { method: 'POST' });
export const submitManualAnalytics = (personaId: string, data: { followers: number; engagement_rate: number; revenue: number; platform?: string }) =>
  apiFetch(`/personas/${personaId}/analytics/manual`, { method: 'POST', body: JSON.stringify(data) });

// Forecasts
export const getForecasts = (personaId: string) => apiFetch(`/personas/${personaId}/forecasts`);
export const generateForecast = (personaId: string) =>
  apiFetch(`/personas/${personaId}/forecasts/generate`, { method: 'POST' });

// Schedule
export const getSchedule = (personaId: string) => apiFetch(`/personas/${personaId}/schedule`);
export const generateSchedule = (personaId: string) =>
  apiFetch(`/personas/${personaId}/schedule/generate`, { method: 'POST' });
// Autopilot
export const toggleAutopilot = (personaId: string, mode: string) =>
  apiFetch(`/personas/${personaId}/autopilot?mode=${mode}`, { method: 'POST' });

// Gallery
export const getGallery = (personaId: string) => apiFetch(`/personas/${personaId}/gallery`);

// Dashboard
export const getDashboardSummary = () => apiFetch('/dashboard/summary');
export const getSystemProviders = () => apiFetch('/system/providers');

// The five gates between the studio and a first real payment. Measured from
// configuration and the calendar — never asserted, which is what the constants
// this replaced were.
export const getMonetizationReadiness = () => apiFetch('/monetization/readiness');

// The connected platform's inbox. Paid DMs are the revenue engine here, so
// this is the surface closest to where money is actually made — and it is
// read-only: `can_send` comes back false whatever the state, because replying
// to a paying fan as the creator is not something this app does.
//
// This is *not* the `/fans` list the `/chat` page renders. Those rows are the
// studio's own simulated ledger (`Fan.total_spent` and friends are derived
// caches over it, and the only payment processor in the app is `fake`), so
// nothing on that page is evidence that anyone paid. This one is the platform's
// own record.
export const getConversations = (limit = 25) =>
  apiFetch(`/publish/conversations?limit=${limit}`);

// Liveness of the API itself. Answers even when degraded — `status` and the
// per-provider rows are what say whether anything is actually reachable.
export const getHealth = () => apiFetch('/health');

// Jobs
export const getJob = (jobId: string) => apiFetch(`/jobs/${jobId}`);
export const listJobs = (params?: { persona_id?: string; status?: string }) => {
  const qs = new URLSearchParams();
  if (params?.persona_id) qs.set('persona_id', params.persona_id);
  if (params?.status) qs.set('status', params.status);
  const q = qs.toString();
  return apiFetch(`/jobs${q ? '?' + q : ''}`);
};

export const listVideos = (personaId: string) => apiFetch(`/personas/${personaId}/videos`);

// Auto Production
export const autoProduce = (personaId: string, params?: {
  shoot_count?: number;
  images_per_shoot?: number;
  generate_videos?: boolean;
  adult_content?: boolean;
  themes?: string;
}) => {
  const qs = new URLSearchParams();
  if (params?.shoot_count) qs.set('shoot_count', String(params.shoot_count));
  if (params?.images_per_shoot) qs.set('images_per_shoot', String(params.images_per_shoot));
  if (params?.generate_videos !== undefined) qs.set('generate_videos', String(params.generate_videos));
  if (params?.adult_content !== undefined) qs.set('adult_content', String(params.adult_content));
  if (params?.themes) qs.set('themes', params.themes);
  const q = qs.toString();
  return apiFetch(`/personas/${personaId}/auto-produce${q ? '?' + q : ''}`, { method: 'POST' });
};

// Fan Chat
export const listFans = (params?: { persona_id?: string; status?: string }) => {
  const qs = new URLSearchParams();
  if (params?.persona_id) qs.set('persona_id', params.persona_id);
  if (params?.status) qs.set('status', params.status);
  const q = qs.toString();
  return apiFetch(`/fans${q ? '?' + q : ''}`);
};

export const listFanMessages = (fanId: string, limit = 50) =>
  apiFetch(`/fans/${fanId}/messages?limit=${limit}`);

export const autoReply = (fanId: string, message: string) =>
  apiFetch(`/fans/${fanId}/reply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ message }),
  });

// Mailboxes (per-persona AI mailboxes)
export const listMailboxes = () => apiFetch('/mailboxes');

export const getMailbox = (personaId: string) => apiFetch(`/mailboxes/${personaId}`);

export const sendAsPersona = (personaId: string, fanId: string, content: string) =>
  apiFetch(`/mailboxes/${personaId}/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ fan_id: fanId, content }),
  });

// Social Accounts (platform signup + approval)
export const listSocialAccounts = (params?: { persona_id?: string; platform?: string; status?: string }) => {
  const qs = new URLSearchParams();
  if (params?.persona_id) qs.set('persona_id', params.persona_id);
  if (params?.platform) qs.set('platform', params.platform);
  if (params?.status) qs.set('status', params.status);
  const q = qs.toString();
  return apiFetch(`/social-accounts${q ? '?' + q : ''}`);
};

export const requestSocialAccount = (params: {
  persona_id: string; platform: string; username: string;
  display_name?: string; email?: string; bio?: string;
}) => {
  // These are Query(...) params on the API (app/routes/socials.py:76-83), not a
  // form body. POSTing them as application/x-www-form-urlencoded left every
  // field "missing" and returned 422. Params go in the URL.
  const qs = new URLSearchParams(params);
  return apiFetch(`/social-accounts?${qs}`, { method: 'POST' });
};

export const approveSocialAccount = (accountId: string, notes?: string) => {
  const params = notes ? `?notes=${encodeURIComponent(notes)}` : '';
  return apiFetch(`/social-accounts/${accountId}/approve${params}`, { method: 'POST' });
};

export const rejectSocialAccount = (accountId: string, reason: string) =>
  apiFetch(`/social-accounts/${accountId}/reject?reason=${encodeURIComponent(reason)}`, { method: 'POST' });

export const activateSocialAccount = (accountId: string) =>
  apiFetch(`/social-accounts/${accountId}/activate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(),
  });

export const generateAccountEmail = (accountId: string) =>
  apiFetch(`/social-accounts/${accountId}/generate-email`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(),
  });

export const checkAccountEmails = (accountId: string) =>
  apiFetch(`/social-accounts/${accountId}/emails`);

// Everything a person needs to complete one platform signup by hand.
// Read-only: the API never contacts the platform for this.
export const getSignupPacket = (accountId: string) =>
  apiFetch(`/social-accounts/${accountId}/signup-packet`);

// ── Model manager ────────────────────────────────────────────────────
// The roster derives each account's state from the evidence it actually has.
// Nothing here asserts a platform signup happened — see the note on the roster
// response and `app/routes/manager.py`.
export const getManagerRoster = (params?: { persona_id?: string; platform?: string }) => {
  const qs = new URLSearchParams();
  if (params?.persona_id) qs.set('persona_id', params.persona_id);
  if (params?.platform) qs.set('platform', params.platform);
  const q = qs.toString();
  return apiFetch(`/manager/roster${q ? '?' + q : ''}`);
};

// The studio's operating structure: the divisions that own production work,
// each with what it makes, whether it can act against the providers actually
// configured right now, and the reason when it cannot. Read-only — it resolves
// providers but never fails for one that is missing.
export const getDivisions = (params?: { persona_id?: string }) => {
  const qs = new URLSearchParams();
  if (params?.persona_id) qs.set('persona_id', params.persona_id);
  const q = qs.toString();
  return apiFetch(`/manager/divisions${q ? '?' + q : ''}`);
};

// Files the local request rows for every platform the persona lacks. It is a
// worklist step: nothing is sent to any platform and no account is created.
export const requestAllPlatforms = (personaId: string, platforms?: string[]) => {
  const qs = new URLSearchParams();
  if (platforms?.length) qs.set('platforms', platforms.join(','));
  const q = qs.toString();
  return apiFetch(`/manager/personas/${personaId}/request-all${q ? '?' + q : ''}`, {
    method: 'POST',
  });
};

// The whole book: what the studio can actually ship, what the connected platform
// has actually paid, and the single next thing to do. It joins the roster, the
// divisions and the ledger — none of which mentioned money — and every figure in
// it is read from configuration, the calendar, or the platform's own books. A
// money figure is `null` rather than 0 when there is no reading at all.
export const getManagerBusiness = (params?: { days?: number }) => {
  const qs = new URLSearchParams();
  if (params?.days) qs.set('days', String(params.days));
  const q = qs.toString();
  return apiFetch(`/manager/business${q ? '?' + q : ''}`);
};

export const syncProfile = (accountId: string) =>
  apiFetch(`/social-accounts/${accountId}/sync-profile`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(),
  });

export const storeCredentials = (accountId: string, password: string, username?: string) => {
  // Query(...) params on the API (app/routes/socials.py:355-356) — see the note
  // on requestSocialAccount. A form body here returns 422 and saves nothing.
  const qs = new URLSearchParams({ platform_password: password });
  if (username) qs.set('platform_username', username);
  return apiFetch(`/social-accounts/${accountId}/store-credentials?${qs}`, { method: 'POST' });
};

export const bulkSyncProfiles = () =>
  apiFetch('/social-accounts/bulk-sync', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(),
  });

// TikTok — Login Kit OAuth + Display API (read-only analytics on the account's
// own data; this integration never posts, follows, or likes).
export const connectTikTok = (accountId: string) =>
  apiFetch(`/tiktok/connect?account_id=${accountId}`);

export const tiktokStatus = (accountId: string) =>
  apiFetch(`/social-accounts/${accountId}/tiktok/status`);

export const tiktokSync = (accountId: string) =>
  apiFetch(`/social-accounts/${accountId}/tiktok/sync`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(),
  });

export const tiktokDisconnect = (accountId: string) =>
  apiFetch(`/social-accounts/${accountId}/tiktok/disconnect`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(),
  });

// ── Fanvue (the money platform) — workspace-level, not per-account ───────
//
// Unlike TikTok above, these take no account id. Fanvue credentials belong to
// the *workspace*: one grant covers every persona, which is why the connect
// button lives on Monetization beside the gate it clears rather than on a
// per-account row in Socials.
//
// Fanvue issues no static API keys, so `connect` is the only way to a token —
// it returns a consent URL the account owner must open and approve themselves.
export const connectFanvue = () => apiFetch('/fanvue/connect');

// Reports `configured`, `authorized` and `armed` separately, because they are
// three different states with three different fixes. `ok` means only that Fanvue
// was asked and agreed the token is good.
export const fanvueStatus = () => apiFetch('/fanvue/status');

// Removes the stored grant locally. Fanvue documents no revoke endpoint, so the
// response says so rather than implying the token died on their side.
export const disconnectFanvue = () =>
  apiFetch('/fanvue/disconnect', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(),
  });

// Team invites (roster only — this build has no login, so nothing is gated)
export const listInvites = () => apiFetch('/team/invites');

export const createInvite = (params: { email?: string; role?: string; note?: string; ttl_days?: number }) => {
  const qs = new URLSearchParams();
  if (params.email) qs.set('email', params.email);
  if (params.role) qs.set('role', params.role);
  if (params.note) qs.set('note', params.note);
  if (params.ttl_days) qs.set('ttl_days', String(params.ttl_days));
  // Query(...) params on the API (app/routes/team.py:68-72) — a form body
  // returns 422 and creates no invite.
  return apiFetch(`/team/invites?${qs}`, { method: 'POST' });
};

export const getInvite = (token: string) => apiFetch(`/team/invites/${token}`);

export const acceptInvite = (token: string) =>
  apiFetch(`/team/invites/${token}/accept`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(),
  });

export const revokeInvite = (inviteId: string) =>
  apiFetch(`/team/invites/${inviteId}/revoke`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(),
  });

