'use client'

import { useEffect, useState } from 'react'
import { listPersonas, getHealth } from '@/lib/api'

interface HealthCheck {
  service: string
  status: string
}

interface Persona {
  id: string
  name: string
  age: number
  status: string
  identity_status: string | null
  identity_score: number | null
  packs_count: number
  brand: string
}

export default function Dashboard() {
  const [personas, setPersonas] = useState<Persona[]>([])
  const [health, setHealth] = useState<HealthCheck[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      listPersonas().catch(() => []),
      getHealth().catch(() => ({ checks: [] })),
    ]).then(([p, h]) => {
      setPersonas(p)
      setHealth(h.checks || [])
      setLoading(false)
    })
  }, [])

  const activeCount = personas.filter(p => p.status === 'active').length
  const trainingCount = personas.filter(p => p.identity_status === 'training').length
  const totalPacks = personas.reduce((sum, p) => sum + p.packs_count, 0)

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <h1 className="text-3xl font-bold mb-8 bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">
        PERSONA STUDIO
      </h1>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <StatCard label="MODELS" value={personas.length} icon="👤" />
        <StatCard label="ACTIVE" value={activeCount} icon="✅" />
        <StatCard label="CONTENT PACKS" value={totalPacks} icon="📦" />
        <StatCard label="WORKFLOWS" value={0} icon="⚡" />
      </div>

      {/* Health */}
      <div className="bg-gray-900 rounded-xl p-6 mb-8">
        <h2 className="text-lg font-semibold mb-4">System Health</h2>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          {health.map((h) => (
            <div key={h.service} className="flex items-center gap-2 bg-gray-800 rounded-lg px-4 py-2">
              <span className={`w-2.5 h-2.5 rounded-full ${
                h.status === 'green' ? 'bg-green-400' :
                h.status === 'yellow' ? 'bg-yellow-400' : 'bg-red-400'
              }`} />
              <span className="text-sm capitalize">{h.service}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Models */}
      <div className="bg-gray-900 rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Models</h2>
          <a href="/models/create" className="bg-purple-600 hover:bg-purple-700 px-4 py-2 rounded-lg text-sm transition">
            + CREATE MODEL
          </a>
        </div>
        {loading ? (
          <p className="text-gray-400">Loading...</p>
        ) : personas.length === 0 ? (
          <p className="text-gray-400">No models yet. Create your first persona to get started.</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {personas.map((p) => (
              <a key={p.id} href={`/personas/${p.id}`} className="block bg-gray-800 hover:bg-gray-750 rounded-lg p-4 transition">
                <div className="flex items-center gap-3 mb-3">
                  <div className="w-10 h-10 rounded-full bg-gradient-to-br from-purple-400 to-pink-400 flex items-center justify-center text-lg font-bold">
                    {p.name[0]}
                  </div>
                  <div>
                    <h3 className="font-semibold">{p.name}</h3>
                    <p className="text-xs text-gray-400">Age {p.age} · {p.brand}</p>
                  </div>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span className={`px-2 py-0.5 rounded text-xs ${
                    p.status === 'active' ? 'bg-green-900 text-green-300' : 'bg-gray-700 text-gray-300'
                  }`}>
                    {p.status.toUpperCase()}
                  </span>
                  {p.identity_score && (
                    <span className="text-gray-400">Identity: {(p.identity_score * 100).toFixed(1)}%</span>
                  )}
                </div>
                <div className="mt-2 text-xs text-gray-400">
                  {p.packs_count} content packs
                </div>
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function StatCard({ label, value, icon }: { label: string; value: number; icon: string }) {
  return (
    <div className="bg-gray-900 rounded-xl p-4">
      <div className="text-2xl mb-1">{icon}</div>
      <div className="text-2xl font-bold">{value}</div>
      <div className="text-xs text-gray-400 uppercase tracking-wider">{label}</div>
    </div>
  )
}
