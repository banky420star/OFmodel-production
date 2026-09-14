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

// Model managers (API prefix: /manager)
export const listManagers = () => apiFetch('/manager');
export const managerDashboard = (personaId: string) => apiFetch(`/manager/personas/${personaId}/dashboard`);
export const startManager = (personaId: string) => apiFetch(`/manager/personas/${personaId}/start`, { method: 'POST' });
export const wakeManager = (personaId: string, source = 'manual') => apiFetch(`/manager/personas/${personaId}/wake?source=${source}`, { method: 'POST' });
export const pauseManager = (personaId: string) => apiFetch(`/manager/personas/${personaId}/pause`, { method: 'POST' });
export const resumeManager = (personaId: string) => apiFetch(`/manager/personas/${personaId}/resume`, { method: 'POST' });
export const managerRunNow = (personaId: string, taskType: string) => apiFetch(`/manager/personas/${personaId}/run-now?task_type=${taskType}`, { method: 'POST' });
export const retryManagerFailed = (personaId: string) => apiFetch(`/manager/personas/${personaId}/retry-failed`, { method: 'POST' });
export const setManagerAutonomy = (personaId: string, level: number) => apiFetch(`/manager/personas/${personaId}/autonomy?level=${level}`, { method: 'POST' });

// Identities
export const listIdentities = (personaId: string) => apiFetch(`/personas/${personaId}/identities`);

// Shoots
export const createShoot = (personaId: string, data: any) =>
  apiFetch(`/personas/${personaId}/shoots`, { method: 'POST', body: JSON.stringify(data) });
export const listShoots = (personaId: string) => apiFetch(`/shoots?persona_id=${personaId}`);
export const getShootImages = (shootId: string) => apiFetch(`/shoots/${shootId}/images`);

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

// Fanvue Platform Manager (compliance-gated inventory)
export const listPlatformAccounts = () => apiFetch('/platform/accounts');
export const createPlatformAccount = (data: {
  persona_id: string; platform?: string; handle?: string; subscription_price?: number;
  is_ai_disclosed: boolean; kyc_status: string; consent_owner: string;
  target_public_posts_per_day?: number; target_subscriber_posts_per_day?: number;
  target_premium_items_per_week?: number;
}) => apiFetch('/platform/accounts', { method: 'POST', body: JSON.stringify(data) });
export const getPlatformAccount = (id: string) => apiFetch(`/platform/accounts/${id}`);
export const patchPlatformAccount = (id: string, data: Record<string, unknown>) =>
  apiFetch(`/platform/accounts/${id}`, { method: 'PATCH', body: JSON.stringify(data) });
export const getPlatformInventory = (id: string, tier?: string) =>
  apiFetch(`/platform/accounts/${id}/inventory${tier ? `?tier=${tier}` : ''}`);
export const syncPlatformGallery = (id: string) =>
  apiFetch(`/platform/accounts/${id}/inventory/sync-gallery`, { method: 'POST' });
export const planPlatformInventory = (id: string) =>
  apiFetch(`/platform/inventory/plan?account_id=${id}`, { method: 'POST' });
export const listPlatformOrders = (id: string) => apiFetch(`/platform/accounts/${id}/orders`);
export const postPlatformInventoryItem = (itemId: string) =>
  apiFetch(`/platform/inventory/${itemId}/post`, { method: 'POST' });

// Full-auto social signup (robot fills form, polls inbox, types the code)
export const startFullAutoSignup = (accountId: string) =>
  apiFetch(`/social-accounts/${accountId}/fullauto-signup`, { method: 'POST' });
export const getFullAutoSignupStatus = (accountId: string) =>
  apiFetch(`/social-accounts/${accountId}/fullauto-status`);

// Always-on social worker (official platform APIs ONLY — Fanvue, X; Fansly/
// OnlyFans have no official API and are never integrated)
export const getSocialWorkerStatus = () => apiFetch('/social-worker/status');
export const runSocialWorkerOnce = () => apiFetch('/social-worker/run-once', { method: 'POST' });
export const connectSocialApi = (platform: string, username: string, token: string, tokenKind = 'oauth_access_token') =>
  apiFetch('/social-worker/connect-api', {
    method: 'POST',
    body: JSON.stringify({ platform, username, token, token_kind: tokenKind }),
  });
export const disconnectSocialApi = (accountId: string) =>
  apiFetch(`/social-worker/${accountId}/disconnect`, { method: 'POST' });

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
  const qs = new URLSearchParams(params);
  return apiFetch('/social-accounts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: qs,
  });
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

export const syncProfile = (accountId: string) =>
  apiFetch(`/social-accounts/${accountId}/sync-profile`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(),
  });

export const storeCredentials = (accountId: string, password: string, username?: string) => {
  const qs = new URLSearchParams({ platform_password: password });
  if (username) qs.set('platform_username', username);
  return apiFetch(`/social-accounts/${accountId}/store-credentials`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: qs,
  });
};

export const bulkSyncProfiles = () =>
  apiFetch('/social-accounts/bulk-sync', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(),
  });


