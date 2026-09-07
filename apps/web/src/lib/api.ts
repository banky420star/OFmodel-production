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

// Video Generation
export const generateVideo = (personaId: string, params?: { prompt?: string; duration?: number }) => {
  const qs = new URLSearchParams();
  if (params?.prompt) qs.set('prompt', params.prompt);
  if (params?.duration) qs.set('duration', String(params.duration));
  const q = qs.toString();
  return apiFetch(`/personas/${personaId}/generate-video${q ? '?' + q : ''}`, { method: 'POST' });
};

export const generateShootVideo = (shootId: string, params?: { shot_index?: number; prompt?: string; duration?: number }) => {
  const qs = new URLSearchParams();
  if (params?.shot_index !== undefined) qs.set('shot_index', String(params.shot_index));
  if (params?.prompt) qs.set('prompt', params.prompt);
  if (params?.duration) qs.set('duration', String(params.duration));
  const q = qs.toString();
  return apiFetch(`/shoots/${shootId}/generate-video${q ? '?' + q : ''}`, { method: 'POST' });
};

export const listVideos = (personaId: string) => apiFetch(`/personas/${personaId}/videos`);

// Adult Content
export const generateAdultContent = (personaId: string, data: { scene_prompt: string; content_type?: string }) =>
  apiFetch(`/personas/${personaId}/adult-content`, { method: 'POST', body: JSON.stringify(data) });

export const batchAdultContent = (personaId: string, data: { scenes: string[]; content_type?: string }) =>
  apiFetch(`/personas/${personaId}/batch-adult-content`, { method: 'POST', body: JSON.stringify(data) });

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

// Health
export const getHealth = () => apiFetch('/health');

// Fan Chat
export const listFans = (params?: { persona_id?: string; status?: string }) => {
  const qs = new URLSearchParams();
  if (params?.persona_id) qs.set('persona_id', params.persona_id);
  if (params?.status) qs.set('status', params.status);
  const q = qs.toString();
  return apiFetch(`/fans${q ? '?' + q : ''}`);
};

export const createFan = (personaId: string, username: string, displayName?: string) =>
  apiFetch('/fans', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      persona_id: personaId, username, display_name: displayName || username,
    }),
  });

export const listFanMessages = (fanId: string, limit = 50) =>
  apiFetch(`/fans/${fanId}/messages?limit=${limit}`);

export const autoReply = (fanId: string, message: string) =>
  apiFetch(`/fans/${fanId}/reply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ message }),
  });

export const sendPPV = (fanId: string, contentKey: string, price: number, caption?: string) =>
  apiFetch(`/fans/${fanId}/ppv`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      content_key: contentKey, price: String(price), caption: caption || '',
    }),
  });

export const massMessage = (personaId: string, messageType: string, fanIds?: string[]) =>
  apiFetch('/fans/mass-message', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      persona_id: personaId, message_type: messageType, fan_ids: fanIds,
    }),
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

export const getFanAnalytics = (personaId?: string) => {
  const qs = personaId ? `?persona_id=${personaId}` : '';
  return apiFetch(`/fans/analytics${qs}`);
};

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

export const listPersonaSocialAccounts = (personaId: string) =>
  apiFetch(`/personas/${personaId}/social-accounts`);

export const generateAccountEmail = (accountId: string) =>
  apiFetch(`/social-accounts/${accountId}/generate-email`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(),
  });

export const checkAccountEmails = (accountId: string) =>
  apiFetch(`/social-accounts/${accountId}/emails`);
