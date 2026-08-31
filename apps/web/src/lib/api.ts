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
export const listPersonas = () => apiFetch('/personas');
export const getPersona = (id: string) => apiFetch(`/personas/${id}`);

// Identities
export const listIdentities = (personaId: string) => apiFetch(`/personas/${personaId}/identities`);
export const approveIdentity = (personaId: string, identityId: string) =>
  apiFetch(`/personas/${personaId}/identities/${identityId}/approve`, { method: 'POST' });

// Shoots
export const createShoot = (personaId: string, data: any) =>
  apiFetch(`/personas/${personaId}/shoots`, { method: 'POST', body: JSON.stringify(data) });
export const listShoots = (personaId: string) => apiFetch(`/personas/${personaId}/shoots`);
export const generateShoot = (shootId: string) => apiFetch(`/shoots/${shootId}/generate`, { method: 'POST' });

// Content Packs
export const createPack = (personaId: string, data: any) =>
  apiFetch(`/personas/${personaId}/packs`, { method: 'POST', body: JSON.stringify(data) });
export const listPacks = (personaId: string) => apiFetch(`/personas/${personaId}/packs`);
export const assemblePack = (packId: string) => apiFetch(`/packs/${packId}/assemble`, { method: 'POST' });

// Workflows
export const listWorkflows = (params?: { persona_id?: string }) => {
  const qs = params?.persona_id ? `?persona_id=${params.persona_id}` : '';
  return apiFetch(`/workflows${qs}`);
};
export const getWorkflow = (id: string) => apiFetch(`/workflows/${id}`);
export const getWorkflowSteps = (id: string) => apiFetch(`/workflows/${id}/steps`);
export const retryWorkflow = (id: string) => apiFetch(`/workflows/${id}/retry`, { method: 'POST' });
export const cancelWorkflow = (id: string) => apiFetch(`/workflows/${id}/cancel`, { method: 'POST' });

// QA
export const listQA = (personaId: string) => apiFetch(`/personas/${personaId}/qa`);

// Analytics
export const getAnalytics = (personaId: string) => apiFetch(`/personas/${personaId}/analytics`);
export const generateAnalytics = (personaId: string) =>
  apiFetch(`/personas/${personaId}/analytics/generate`, { method: 'POST' });

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

// Health
export const getHealth = () => apiFetch('/health');
