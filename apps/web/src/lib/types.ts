export interface PersonaDetail {
  id: string; name: string; age: number; status: string
  brand: string; identity_score: number | null; identity_status: string | null
  packs_count: number; shoots_count: number; avatar_url: string
}

export interface HealthCheck {
  service: string; status: string
}

export interface ShootDetail {
  id: string; name: string; status: string; asset_type: string
  progress: number; image_count: number; generated_count: number
  generated_images: string[]
  theme: string; persona_name: string; created_at: string | null
}

export interface AttentionItem {
  id: string; name: string; type: string; status: string
}

export interface DashboardSummary {
  active_models: number
  training_models: number
  total_models: number
  total_packs: number
  total_shoots: number
  revenue: number
  followers: number
  engagement_rate: number
  health: { online: number; total: number; checks: HealthCheck[] }
  attention_items: AttentionItem[]
  shoots: ShootDetail[]
  personas: PersonaDetail[]
}
