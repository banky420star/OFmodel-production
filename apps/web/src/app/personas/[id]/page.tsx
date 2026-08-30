'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import {
  getPersona, listIdentities, listShoots, listPacks, listWorkflows,
  listQA, getAnalytics, getForecasts, getSchedule,
  createShoot, generateShoot, createPack, assemblePack,
  generateAnalytics, generateForecast, generateSchedule,
  toggleAutopilot,
} from '@/lib/api'

type Tab = 'overview' | 'identity' | 'shoots' | 'content' | 'analytics' | 'revenue' | 'schedule' | 'workflows'

export default function PersonaPage() {
  const params = useParams()
  const id = params.id as string
  const [tab, setTab] = useState<Tab>('overview')
  const [persona, setPersona] = useState<any>(null)
  const [identities, setIdentities] = useState<any[]>([])
  const [shoots, setShoots] = useState<any[]>([])
  const [packs, setPacks] = useState<any[]>([])
  const [workflows, setWorkflows] = useState<any[]>([])
  const [qaResults, setQaResults] = useState<any[]>([])
  const [analytics, setAnalytics] = useState<any[]>([])
  const [forecasts, setForecasts] = useState<any[]>([])
  const [schedule, setSchedule] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!id) return
    Promise.all([
      getPersona(id).catch(() => null),
      listIdentities(id).catch(() => []),
      listShoots(id).catch(() => []),
      listPacks(id).catch(() => []),
      listWorkflows({ persona_id: id }).catch(() => []),
      listQA(id).catch(() => []),
      getAnalytics(id).catch(() => []),
      getForecasts(id).catch(() => []),
      getSchedule(id).catch(() => []),
    ]).then(([p, i, s, pk, wf, qa, a, f, sc]) => {
      setPersona(p)
      setIdentities(i)
      setShoots(s)
      setPacks(pk)
      setWorkflows(wf)
      setQaResults(qa)
      setAnalytics(a)
      setForecasts(f)
      setSchedule(sc)
      setLoading(false)
    })
  }, [id])

  if (loading) return <div className="max-w-7xl mx-auto px-4 py-8 text-gray-400">Loading...</div>
  if (!persona) return <div className="max-w-7xl mx-auto px-4 py-8 text-gray-400">Persona not found</div>

  const tabs: { key: Tab; label: string }[] = [
    { key: 'overview', label: 'Overview' },
    { key: 'identity', label: 'Identity' },
    { key: 'shoots', label: 'Shoots' },
    { key: 'content', label: 'Content' },
    { key: 'analytics', label: 'Analytics' },
    { key: 'revenue', label: 'Revenue' },
    { key: 'schedule', label: 'Calendar' },
    { key: 'workflows', label: 'Workflows' },
  ]

  const handleCreateShoot = async () => {
    const shoot = await createShoot(id, { name: 'New Shoot', theme: 'lifestyle', image_count: 8 })
    setShoots(prev => [shoot, ...prev])
    await generateShoot(shoot.id)
  }

  const handleCreatePack = async () => {
    const pack = await createPack(id, { name: `${persona.name} Pack`, platform: 'instagram' })
    setPacks(prev => [pack, ...prev])
    await assemblePack(pack.id)
  }

  const handleGenerateAnalytics = async () => {
    await generateAnalytics(id)
    const a = await getAnalytics(id)
    setAnalytics(a)
  }

  const handleGenerateForecast = async () => {
    await generateForecast(id)
    const f = await getForecasts(id)
    setForecasts(f)
  }

  const handleGenerateSchedule = async () => {
    await generateSchedule(id)
    const sc = await getSchedule(id)
    setSchedule(sc)
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      {/* Header */}
      <div className="flex items-center gap-4 mb-8">
        <a href="/" className="text-gray-400 hover:text-white">← Back</a>
        <div className="w-12 h-12 rounded-full bg-gradient-to-br from-purple-400 to-pink-400 flex items-center justify-center text-xl font-bold">
          {persona.name[0]}
        </div>
        <div>
          <h1 className="text-2xl font-bold">{persona.name}</h1>
          <p className="text-sm text-gray-400">Age {persona.age} · {persona.brand} · {persona.status}</p>
        </div>
        <div className="ml-auto flex gap-2">
          <button onClick={() => toggleAutopilot(id, 'on')} className="bg-green-600 hover:bg-green-700 px-3 py-1.5 rounded text-sm">
            🤖 Autopilot ON
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-6 overflow-x-auto">
        {tabs.map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 rounded-lg text-sm whitespace-nowrap transition ${
              tab === t.key ? 'bg-purple-600 text-white' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="bg-gray-900 rounded-xl p-6">
        {tab === 'overview' && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="bg-gray-800 rounded-lg p-4">
              <div className="text-xs text-gray-400 uppercase">Status</div>
              <div className="text-lg font-bold capitalize">{persona.status}</div>
            </div>
            <div className="bg-gray-800 rounded-lg p-4">
              <div className="text-xs text-gray-400 uppercase">Identity</div>
              <div className="text-lg font-bold">{persona.identity_score ? `${(persona.identity_score * 100).toFixed(1)}%` : 'N/A'}</div>
            </div>
            <div className="bg-gray-800 rounded-lg p-4">
              <div className="text-xs text-gray-400 uppercase">Content Packs</div>
              <div className="text-lg font-bold">{persona.packs_count}</div>
            </div>
            <div className="bg-gray-800 rounded-lg p-4">
              <div className="text-xs text-gray-400 uppercase">Voice</div>
              <div className="text-lg font-bold">{persona.identity_status === 'ready' ? 'READY' : 'PENDING'}</div>
            </div>
          </div>
        )}

        {tab === 'identity' && (
          <div>
            <h3 className="text-lg font-semibold mb-4">Identities ({identities.length})</h3>
            {identities.map(i => (
              <div key={i.id} className="bg-gray-800 rounded-lg p-4 mb-3">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{i.name}</span>
                  <span className={`px-2 py-0.5 rounded text-xs ${
                    i.status === 'ready' ? 'bg-green-900 text-green-300' :
                    i.status === 'approved' ? 'bg-blue-900 text-blue-300' :
                    'bg-gray-700 text-gray-300'
                  }`}>{i.status.toUpperCase()}</span>
                </div>
                <div className="text-sm text-gray-400 mt-1">
                  Consistency: {(i.consistency_score * 100).toFixed(1)}%
                </div>
              </div>
            ))}
            {identities.length === 0 && <p className="text-gray-400">No identities yet.</p>}
          </div>
        )}

        {tab === 'shoots' && (
          <div>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold">Shoots ({shoots.length})</h3>
              <button onClick={handleCreateShoot} className="bg-purple-600 hover:bg-purple-700 px-4 py-2 rounded-lg text-sm">
                + CREATE SHOOT
              </button>
            </div>
            {shoots.map(s => (
              <div key={s.id} className="bg-gray-800 rounded-lg p-4 mb-3">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{s.name || s.theme}</span>
                  <span className={`px-2 py-0.5 rounded text-xs ${
                    s.status === 'completed' ? 'bg-green-900 text-green-300' :
                    s.status === 'generating' ? 'bg-yellow-900 text-yellow-300' :
                    'bg-gray-700 text-gray-300'
                  }`}>{s.status.toUpperCase()}</span>
                </div>
                <div className="text-sm text-gray-400 mt-1">
                  {s.image_count} images · {s.theme}
                </div>
              </div>
            ))}
          </div>
        )}

        {tab === 'content' && (
          <div>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold">Content Packs ({packs.length})</h3>
              <button onClick={handleCreatePack} className="bg-purple-600 hover:bg-purple-700 px-4 py-2 rounded-lg text-sm">
                + GENERATE PACK
              </button>
            </div>
            {packs.map(p => (
              <div key={p.id} className="bg-gray-800 rounded-lg p-4 mb-3">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{p.name}</span>
                  <span className={`px-2 py-0.5 rounded text-xs ${
                    p.status === 'assembled' ? 'bg-green-900 text-green-300' : 'bg-gray-700 text-gray-300'
                  }`}>{p.status.toUpperCase()}</span>
                </div>
                <div className="text-sm text-gray-400 mt-1">
                  Platform: {p.platform} · Images: {p.images.length} · Videos: {p.videos.length}
                </div>
              </div>
            ))}
          </div>
        )}

        {tab === 'analytics' && (
          <div>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold">Analytics ({analytics.length} days)</h3>
              <button onClick={handleGenerateAnalytics} className="bg-purple-600 hover:bg-purple-700 px-4 py-2 rounded-lg text-sm">
                🔄 Generate Analytics
              </button>
            </div>
            {analytics.length > 0 && (
              <div className="grid grid-cols-4 gap-4 mb-4">
                <div className="bg-gray-800 rounded-lg p-3">
                  <div className="text-xs text-gray-400">Latest Followers</div>
                  <div className="text-xl font-bold">{analytics[0]?.followers.toLocaleString()}</div>
                </div>
                <div className="bg-gray-800 rounded-lg p-3">
                  <div className="text-xs text-gray-400">Engagement Rate</div>
                  <div className="text-xl font-bold">{(analytics[0]?.engagement_rate * 100).toFixed(1)}%</div>
                </div>
                <div className="bg-gray-800 rounded-lg p-3">
                  <div className="text-xs text-gray-400">Monthly Revenue</div>
                  <div className="text-xl font-bold">${analytics[0]?.revenue.toLocaleString()}</div>
                </div>
                <div className="bg-gray-800 rounded-lg p-3">
                  <div className="text-xs text-gray-400">Monthly Costs</div>
                  <div className="text-xl font-bold">${analytics[0]?.costs.toLocaleString()}</div>
                </div>
              </div>
            )}
          </div>
        )}

        {tab === 'revenue' && (
          <div>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold">24-Month Forecast</h3>
              <button onClick={handleGenerateForecast} className="bg-purple-600 hover:bg-purple-700 px-4 py-2 rounded-lg text-sm">
                🔄 Generate Forecast
              </button>
            </div>
            {forecasts.length > 0 && forecasts[0]?.scenarios.map((s: any) => (
              <div key={s.scenario} className="bg-gray-800 rounded-lg p-4 mb-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="font-medium capitalize">{s.scenario}</span>
                  <span className="text-sm text-gray-400">
                    24-month total: ${s.monthly_revenue.reduce((a: number, b: number) => a + b, 0).toLocaleString()}
                  </span>
                </div>
                <div className="h-2 bg-gray-700 rounded overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-purple-500 to-pink-500 rounded"
                    style={{ width: `${Math.min(100, s.monthly_revenue[23] / 500)}%` }}
                  />
                </div>
              </div>
            ))}
            {forecasts.length === 0 && <p className="text-gray-400">Generate a forecast to see projections.</p>}
          </div>
        )}

        {tab === 'schedule' && (
          <div>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold">Content Calendar ({schedule.length} posts)</h3>
              <button onClick={handleGenerateSchedule} className="bg-purple-600 hover:bg-purple-700 px-4 py-2 rounded-lg text-sm">
                📅 Auto-Schedule
              </button>
            </div>
            {schedule.map(s => (
              <div key={s.id} className="bg-gray-800 rounded-lg p-3 mb-2 flex items-center justify-between">
                <span className="text-sm">{s.platform}</span>
                <span className="text-sm text-gray-400">{new Date(s.scheduled_at).toLocaleDateString()}</span>
                <span className={`px-2 py-0.5 rounded text-xs ${
                  s.status === 'posted' ? 'bg-green-900 text-green-300' : 'bg-blue-900 text-blue-300'
                }`}>{s.status}</span>
              </div>
            ))}
          </div>
        )}

        {tab === 'workflows' && (
          <div>
            <h3 className="text-lg font-semibold mb-4">Workflows ({workflows.length})</h3>
            {workflows.map(w => (
              <div key={w.id} className="bg-gray-800 rounded-lg p-4 mb-3">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{w.name}</span>
                  <span className={`px-2 py-0.5 rounded text-xs ${
                    w.status === 'completed' ? 'bg-green-900 text-green-300' :
                    w.status === 'running' ? 'bg-yellow-900 text-yellow-300' :
                    w.status === 'failed' ? 'bg-red-900 text-red-300' :
                    'bg-gray-700 text-gray-300'
                  }`}>{w.status.toUpperCase()}</span>
                </div>
                <div className="text-xs text-gray-400 mt-1">
                  {w.workflow_type} · Steps: {w.current_step || 'pending'}
                </div>
              </div>
            ))}
            {workflows.length === 0 && <p className="text-gray-400">No workflows yet.</p>}
          </div>
        )}
      </div>
    </div>
  )
}
