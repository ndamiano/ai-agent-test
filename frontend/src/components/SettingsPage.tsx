import { useState, useEffect } from 'react'
import { api } from '../api/client'
import type { Settings, LMStudioSettings, ClineSettings } from '../types'

export default function SettingsPage() {
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
            // Ensure both connector configs exist
            if (!data.lmstudio) {
                data.lmstudio = {
                    base_url: 'http://localhost:1234',
                    model: 'local-model',
                    temperature: 0.7,
                    max_tokens: 50000
                }
            }
            if (!data.cline) {
                data.cline = {
                    api_key: '',
                    model: 'claude-sonnet-4-5',
                    temperature: 0.7,
                    max_tokens: 50000
                }
            }
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

    const updateConnectorType = (type: string) => {
        if (!settings) return
        setSettings({
            ...settings,
            connector_type: type
        })
    }

    const updateLMStudioSetting = (key: keyof LMStudioSettings, value: any) => {
        if (!settings || !settings.lmstudio) return
        setSettings({
            ...settings,
            lmstudio: {
                ...settings.lmstudio,
                [key]: value
            }
        })
    }

    const updateClineSetting = (key: keyof ClineSettings, value: any) => {
        if (!settings || !settings.cline) return
        setSettings({
            ...settings,
            cline: {
                ...settings.cline,
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
        <div className="p-6 pt-2 space-y-6">
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
                        onChange={(e) => updateConnectorType(e.target.value)}
                        className="w-full bg-gray-700 text-white border border-gray-600 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
                    >
                        <option value="lmstudio">LMStudio (Local)</option>
                        <option value="cline">Cline (Cloud)</option>
                    </select>
                </div>
            </div>

            {/* LMStudio Settings Section */}
            {settings.connector_type === 'lmstudio' && settings.lmstudio && (
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
            )}

            {/* Cline Settings Section */}
            {settings.connector_type === 'cline' && settings.cline && (
                <div className="space-y-4 pt-4 border-t border-white/[0.06]">
                    <h3 className="text-lg font-semibold text-white">Cline Configuration</h3>

                    <div className="space-y-2">
                        <label className="block text-sm text-gray-400">API Key *</label>
                        <input
                            type="password"
                            value={settings.cline.api_key}
                            onChange={(e) => updateClineSetting('api_key', e.target.value)}
                            placeholder="Enter your Cline API key"
                            className="w-full bg-gray-700 text-white border border-gray-600 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
                        />
                        <p className="text-xs text-gray-500">Your API key is stored locally and never shared</p>
                    </div>

                    <div className="space-y-2">
                        <label className="block text-sm text-gray-400">Model *</label>
                        <input
                            type="text"
                            value={settings.cline.model}
                            onChange={(e) => updateClineSetting('model', e.target.value)}
                            placeholder="claude-sonnet-4-5"
                            className="w-full bg-gray-700 text-white border border-gray-600 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
                        />
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <label className="block text-sm text-gray-400">Temperature</label>
                            <input
                                type="number"
                                value={settings.cline.temperature ?? 0.7}
                                onChange={(e) => updateClineSetting('temperature', parseFloat(e.target.value))}
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
                                value={settings.cline.max_tokens ?? 50000}
                                onChange={(e) => updateClineSetting('max_tokens', parseInt(e.target.value))}
                                min="1"
                                placeholder="50000"
                                className="w-full bg-gray-700 text-white border border-gray-600 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
                            />
                            <p className="text-xs text-gray-500">Minimum: 1</p>
                        </div>
                    </div>
                </div>
            )}

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
