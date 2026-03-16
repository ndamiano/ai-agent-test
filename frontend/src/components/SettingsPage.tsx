import { useState, useEffect } from 'react'
import { api } from '../api/client'
import type { Settings } from '../types'

interface SettingsPageProps {
    onClose: () => void
}

export default function SettingsPage({ onClose }: SettingsPageProps) {
    const [settings, setSettings] = useState<Settings | null>(null)
    const [loading, setLoading] = useState(true)
    const [saving, setSaving] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [success, setSuccess] = useState(false)

    useEffect(() => {
        loadSettings()
    }, [])

    const loadSettings = async () => {
        try {
            setLoading(true)
            setError(null)
            const data = await api.getSettings()
            setSettings(data)
        } catch (err: any) {
            setError(err.response?.data?.detail || 'Failed to load settings')
        } finally {
            setLoading(false)
        }
    }

    const handleSave = async () => {
        if (!settings) return

        try {
            setSaving(true)
            setError(null)
            setSuccess(false)
            await api.updateSettings(settings)
            setSuccess(true)
            setTimeout(() => setSuccess(false), 3000)
        } catch (err: any) {
            setError(err.response?.data?.detail || 'Failed to save settings')
        } finally {
            setSaving(false)
        }
    }

    const updateLMStudioSetting = (key: keyof Settings['lmstudio'], value: any) => {
        if (!settings) return
        setSettings({
            ...settings,
            lmstudio: {
                ...settings.lmstudio,
                [key]: value
            }
        })
    }


    if (loading) {
        return (
            <div className="p-6">
                <div className="text-gray-400">Loading settings...</div>
            </div>
        )
    }

    if (!settings) {
        return (
            <div className="p-6">
                <div className="text-red-400">Failed to load settings</div>
            </div>
        )
    }

    return (
        <div className="p-6 space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <h2 className="text-2xl font-semibold text-white">Settings</h2>
                <button
                    onClick={onClose}
                    className="text-gray-400 hover:text-white transition-colors"
                >
                    <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                </button>
            </div>

            {/* Error Message */}
            {error && (
                <div className="bg-red-900/20 border border-red-500/50 text-red-400 px-4 py-3 rounded">
                    {error}
                </div>
            )}

            {/* Success Message */}
            {success && (
                <div className="bg-green-900/20 border border-green-500/50 text-green-400 px-4 py-3 rounded">
                    Settings saved successfully!
                </div>
            )}

            {/* LLM Provider Section */}
            <div className="space-y-4">
                <h3 className="text-lg font-semibold text-white">LLM Provider</h3>
                <div className="space-y-2">
                    <label className="block text-sm text-gray-400">Provider</label>
                    <select
                        value={settings.connector_type}
                        disabled
                        className="w-full bg-gray-700 text-gray-400 border border-gray-600 rounded px-3 py-2 cursor-not-allowed"
                    >
                        <option value="lmstudio">LMStudio</option>
                    </select>
                    <p className="text-xs text-gray-500">Currently only LMStudio is supported</p>
                </div>
            </div>

            {/* LMStudio Settings Section */}
            <div className="space-y-4 pt-4 border-t border-white/[0.06]">
                <h3 className="text-lg font-semibold text-white">LMStudio Configuration</h3>

                <div className="space-y-2">
                    <label className="block text-sm text-gray-400">Base URL *</label>
                    <input
                        type="text"
                        value={settings.lmstudio.base_url}
                        onChange={(e) => updateLMStudioSetting('base_url', e.target.value)}
                        placeholder="http://localhost:1234"
                        className="w-full bg-gray-700 text-white border border-gray-600 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
                    />
                </div>

                <div className="space-y-2">
                    <label className="block text-sm text-gray-400">Model *</label>
                    <input
                        type="text"
                        value={settings.lmstudio.model}
                        onChange={(e) => updateLMStudioSetting('model', e.target.value)}
                        placeholder="local-model"
                        className="w-full bg-gray-700 text-white border border-gray-600 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
                    />
                </div>

                <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="block text-sm text-gray-400">Temperature</label>
                        <input
                            type="number"
                            value={settings.lmstudio.temperature ?? 0.7}
                            onChange={(e) => updateLMStudioSetting('temperature', parseFloat(e.target.value))}
                            min="0"
                            max="2"
                            step="0.1"
                            placeholder="0.7"
                            className="w-full bg-gray-700 text-white border border-gray-600 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
                        />
                        <p className="text-xs text-gray-500">Range: 0-2</p>
                    </div>

                    <div className="space-y-2">
                        <label className="block text-sm text-gray-400">Max Tokens</label>
                        <input
                            type="number"
                            value={settings.lmstudio.max_tokens ?? 50000}
                            onChange={(e) => updateLMStudioSetting('max_tokens', parseInt(e.target.value))}
                            min="1"
                            placeholder="50000"
                            className="w-full bg-gray-700 text-white border border-gray-600 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
                        />
                        <p className="text-xs text-gray-500">Minimum: 1</p>
                    </div>
                </div>
            </div>

            {/* Save Button */}
            <div className="pt-4 border-t border-white/[0.06]">
                <button
                    onClick={handleSave}
                    disabled={saving}
                    className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 disabled:cursor-not-allowed text-white font-medium px-4 py-2 rounded transition-colors"
                >
                    {saving ? 'Saving...' : 'Save Settings'}
                </button>
            </div>
        </div>
    )
}
