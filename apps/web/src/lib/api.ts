// In production/Docker: use relative URLs — Next.js rewrite proxies /api/* to backend
// For local dev outside Docker: set NEXT_PUBLIC_API_URL=http://localhost:8000
const API_URL = process.env.NEXT_PUBLIC_API_URL || '';

// ---------------------------------------------------------------------------
// API auth gate (single shared operator token — no users/sessions/OAuth).
// The backend compares `Authorization: Bearer <token>` against its
// API_AUTH_TOKEN setting; leave empty to disable auth entirely (local dev).
// Operators paste the token once on the /settings page, which stores it here.
// ---------------------------------------------------------------------------
export const API_TOKEN_STORAGE_KEY = 'persona-studio:api-token';

export function getApiToken(): string | null {
  try {
    return localStorage.getItem(API_TOKEN_STORAGE_KEY) || null;
  } catch {
    return null;
  }
}

export function setApiToken(token: string): void {
  try {
    if (token) localStorage.setItem(API_TOKEN_STORAGE_KEY, token);
    else localStorage.removeItem(API_TOKEN_STORAGE_KEY);
  } catch {
    // Storage unavailable (private mode, etc.) — token simply won't persist.
  }
}

function authHeaders(): Record<string, string> {
  const token = getApiToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// For <img src> URLs served by the API (avatars, gallery, shoots, adult content).
// Browser image tags cannot send Authorization headers, so the token is appended
// as a query param. TRADEOFF (documented on the backend too): with auth enabled
// the token appears in image URLs; acceptable for a single-operator gate.
// No-op when auth is disabled (no token) or the path is not an API media URL.
export function mediaUrl(path: string): string {
  const token = getApiToken();
  if (!token || !path.startsWith('/api/v1/')) return path;
  return `${path}${path.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`;
}

export async function apiFetch(path: string, options: RequestInit = {}) {
  const res = await fetch(`${API_URL}/api/v1${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...authHeaders(), ...options.headers },
  });
  if (!res.ok) {
    if (res.status === 401) {
      throw new Error('Unauthorized — check API token in Settings');
    }
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json();
}

// Personas
export const createPersona = (data: any) => apiFetch('/personas', { method: 'POST', body: JSON.stringify(data) });
export const getPersona = (id: string) => apiFetch(`/personas/${id}`);
export const listPersonas = () => apiFetch('/personas');

// Identities
export const listIdentities = (personaId: string) => apiFetch(`/personas/${personaId}/identities`);

// Shoots
export const createShoot = (personaId: string, data: any) =>
  apiFetch(`/personas/${personaId}/shoots`, { method: 'POST', body: JSON.stringify(data) });
export const listShoots = (personaId: string) => apiFetch(`/personas/${personaId}/shoots`);

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
  const qs = new URLSearchParams();
  if (notes) qs.set('notes', notes);
  return apiFetch(`/social-accounts/${accountId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: qs,
  });
};

export const rejectSocialAccount = (accountId: string, reason: string) => {
  const qs = new URLSearchParams({ reason });
  return apiFetch(`/social-accounts/${accountId}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: qs,
  });
};

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


