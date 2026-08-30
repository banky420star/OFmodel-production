'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { createPersona } from '@/lib/api'

export default function CreateModelPage() {
  const router = useRouter()
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({
    name: '',
    age: 24,
    hair: 'long blonde',
    eyeColour: 'blue',
    brand: 'luxury lifestyle',
    personality: 'confident, playful',
    voiceStyle: 'South African English',
    publishingFrequency: '5 packs/week',
  })

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.name) return
    setCreating(true)
    try {
      const persona = await createPersona({
        name: form.name,
        age: form.age,
        description: `${form.brand} creator`,
        adult_verified: true,
        synthetic_identity: true,
        appearance: {
          hair: form.hair,
          hair_length: form.hair.split(' ')[0] || 'long',
          hair_colour: form.hair.split(' ').slice(1).join(' ') || 'blonde',
          eye_colour: form.eyeColour,
          style_preferences: [form.brand],
        },
        personality: form.personality.split(',').map(s => s.trim()),
        brand: form.brand,
        voice_style: form.voiceStyle,
        publishing_frequency: form.publishingFrequency,
      })
      router.push(`/personas/${persona.id}`)
    } catch (err) {
      console.error(err)
    } finally {
      setCreating(false)
    }
  }

  const update = (field: string, value: any) => setForm(prev => ({ ...prev, [field]: value }))

  return (
    <div className="max-w-2xl mx-auto px-4 py-8">
      <a href="/" className="text-gray-400 hover:text-white text-sm">← Back to Dashboard</a>
      <h1 className="text-3xl font-bold mt-4 mb-8 bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">
        CREATE MODEL
      </h1>

      <form onSubmit={handleSubmit} className="bg-gray-900 rounded-xl p-6 space-y-6">
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-1">Name</label>
          <input
            type="text" required value={form.name}
            onChange={e => update('name', e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2.5 text-white focus:border-purple-500 focus:outline-none"
            placeholder="e.g. Ava"
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">Age</label>
            <input
              type="number" min={18} max={99} value={form.age}
              onChange={e => update('age', parseInt(e.target.value))}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2.5 text-white focus:border-purple-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">Eye Colour</label>
            <select
              value={form.eyeColour}
              onChange={e => update('eyeColour', e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2.5 text-white focus:border-purple-500 focus:outline-none"
            >
              {['blue', 'green', 'hazel', 'brown', 'grey'].map(c => <option key={c}>{c}</option>)}
            </select>
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-300 mb-1">Hair</label>
          <input
            type="text" value={form.hair}
            onChange={e => update('hair', e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2.5 text-white focus:border-purple-500 focus:outline-none"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-300 mb-1">Brand</label>
          <input
            type="text" value={form.brand}
            onChange={e => update('brand', e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2.5 text-white focus:border-purple-500 focus:outline-none"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-300 mb-1">Personality (comma-separated)</label>
          <input
            type="text" value={form.personality}
            onChange={e => update('personality', e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2.5 text-white focus:border-purple-500 focus:outline-none"
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">Voice Style</label>
            <input
              type="text" value={form.voiceStyle}
              onChange={e => update('voiceStyle', e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2.5 text-white focus:border-purple-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">Publishing Frequency</label>
            <input
              type="text" value={form.publishingFrequency}
              onChange={e => update('publishingFrequency', e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2.5 text-white focus:border-purple-500 focus:outline-none"
            />
          </div>
        </div>

        {/* Safety gates */}
        <div className="bg-gray-800 rounded-lg p-4 space-y-2">
          <div className="flex items-center gap-2 text-sm">
            <span className="w-3 h-3 rounded-full bg-green-400" />
            <span>Adult verification: Required (18+)</span>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <span className="w-3 h-3 rounded-full bg-green-400" />
            <span>Synthetic identity only — no real person data</span>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <span className="w-3 h-3 rounded-full bg-green-400" />
            <span>Training data requires explicit rights</span>
          </div>
        </div>

        <button
          type="submit"
          disabled={creating || !form.name}
          className="w-full bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-700 hover:to-pink-700 disabled:opacity-50 px-6 py-3 rounded-lg font-semibold transition"
        >
          {creating ? '⏳ BUILDING MODEL...' : '🚀 BUILD MODEL'}
        </button>
      </form>
    </div>
  )
}
