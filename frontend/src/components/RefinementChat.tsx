import React, { useState, useEffect, useRef, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import { api } from '../api/client'
import { useTaskWebSocket } from '../contexts/WebSocketContext'
import type { RefinementMessage } from '../types'
import BounceDots from './BounceDots'

interface RefinementChatProps {
    taskId: string
    isSynthesizing: boolean
    goal: string
}

type View = 'chat' | 'confirm'

const RefinementChat: React.FC<RefinementChatProps> = ({ taskId, isSynthesizing, goal }) => {
    const [messages, setMessages] = useState<RefinementMessage[]>([])
    const [input, setInput] = useState('')
    const [sending, setSending] = useState(false)
    const [waitingForOpening, setWaitingForOpening] = useState(false)
    const [view, setView] = useState<View>('chat')

    // Confirm-view editable state
    const [refinedGoal, setRefinedGoal] = useState('')
    const [criteria, setCriteria] = useState<string[]>([])
    const [newCriterion, setNewCriterion] = useState('')
    const [confirming, setConfirming] = useState(false)
    const [confirmError, setConfirmError] = useState<string | null>(null)
    const [synthesizing, setSynthesizing] = useState(false)

    const bottomRef = useRef<HTMLDivElement>(null)
    const inputRef = useRef<HTMLTextAreaElement>(null)
    const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
    const { messages: wsMessages } = useTaskWebSocket(taskId)

    // Load messages on mount
    useEffect(() => {
        api.getContextValue(taskId, 'refine_messages')
            .then(raw => {
                try {
                    const parsed: RefinementMessage[] = JSON.parse(
                        typeof raw === 'string' ? raw : JSON.stringify(raw)
                    )
                    setMessages(parsed)
                    if (parsed.length === 0) setWaitingForOpening(true)
                } catch {
                    setWaitingForOpening(true)
                }
            })
            .catch(() => setWaitingForOpening(true))
    }, [taskId])

    // Poll until the opening AI message arrives
    useEffect(() => {
        if (!waitingForOpening) return

        pollTimerRef.current = setTimeout(function poll() {
            api.getContextValue(taskId, 'refine_messages')
                .then(raw => {
                    try {
                        const parsed: RefinementMessage[] = JSON.parse(
                            typeof raw === 'string' ? raw : JSON.stringify(raw)
                        )
                        if (parsed.length > 0) {
                            setMessages(parsed)
                            setWaitingForOpening(false)
                        } else {
                            pollTimerRef.current = setTimeout(poll, 2000)
                        }
                    } catch {
                        pollTimerRef.current = setTimeout(poll, 2000)
                    }
                })
                .catch(() => {
                    pollTimerRef.current = setTimeout(poll, 2000)
                })
        }, 2000)

        return () => {
            if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
        }
    }, [waitingForOpening, taskId])

    // Update messages when a refine_message WS event arrives
    useEffect(() => {
        const latest = wsMessages[wsMessages.length - 1]
        if (!latest || latest.type !== 'refine_message') return
        setMessages(latest.messages)
        setWaitingForOpening(false)
    }, [wsMessages])

    // Scroll to bottom on new messages
    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, [messages, waitingForOpening])

    const sendMessage = useCallback(async () => {
        const text = input.trim()
        if (!text || sending) return

        // Optimistically add user message
        const userMsg: RefinementMessage = { role: 'user', content: text, timestamp: new Date().toISOString() }
        setMessages(prev => [...prev, userMsg])
        setInput('')
        setSending(true)

        try {
            const result = await api.sendRefineMessage(taskId, text)
            setMessages(result.messages)
        } catch (e) {
            // Remove the optimistic message on failure
            setMessages(prev => prev.filter(m => m !== userMsg))
            setInput(text)
        } finally {
            setSending(false)
            setTimeout(() => inputRef.current?.focus(), 0)
        }
    }, [input, sending, taskId])

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
            e.preventDefault()
            sendMessage()
        }
    }

    const handleFinalize = async () => {
        setSynthesizing(true)
        setConfirmError(null)
        try {
            const synthesis = await api.synthesizeRefine(taskId)
            setRefinedGoal(synthesis.refined_goal)
            setCriteria(Array.isArray(synthesis.acceptance_criteria) ? synthesis.acceptance_criteria : [])
            setView('confirm')
        } catch (e) {
            setConfirmError('Synthesis failed. Please try again.')
        } finally {
            setSynthesizing(false)
        }
    }

    const handleStartExecution = async () => {
        setConfirming(true)
        setConfirmError(null)
        try {
            await api.confirmRefine(taskId, refinedGoal, criteria)
            // StagePanel will transition away from refining via WS task_status event
        } catch (e) {
            setConfirmError('Failed to start execution. Please try again.')
            setConfirming(false)
        }
    }

    const addCriterion = () => {
        const text = newCriterion.trim()
        if (!text) return
        setCriteria(prev => [...prev, text])
        setNewCriterion('')
    }

    const removeCriterion = (i: number) => {
        setCriteria(prev => prev.filter((_, idx) => idx !== i))
    }

    // ── Synthesizing overlay ──────────────────────────────────────────────────
    if (isSynthesizing && view !== 'confirm') {
        return (
            <div className="h-full flex flex-col items-center justify-center gap-4 p-6">
                <BounceDots />
                <p className="text-sm text-gray-500">Synthesizing your refined goal…</p>
            </div>
        )
    }

    // ── Confirm view ──────────────────────────────────────────────────────────
    if (view === 'confirm') {
        return (
            <div className="h-full flex flex-col overflow-hidden">
                {/* Header */}
                <div className="flex-shrink-0 px-5 py-4 border-b border-white/[0.06] flex items-center gap-3">
                    <button
                        onClick={() => setView('chat')}
                        className="text-gray-500 hover:text-gray-300 transition-colors"
                        title="Back to chat"
                    >
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
                        </svg>
                    </button>
                    <span className="text-sm font-medium text-white">Review before execution</span>
                </div>

                <div className="flex-1 overflow-y-auto px-5 py-5 space-y-6">
                    {/* Refined goal */}
                    <div className="space-y-2">
                        <label className="text-xs font-medium text-gray-500 uppercase tracking-wider">Refined Goal</label>
                        <textarea
                            value={refinedGoal}
                            onChange={e => setRefinedGoal(e.target.value)}
                            rows={4}
                            className="w-full bg-white/[0.04] border border-white/[0.08] rounded-lg px-4 py-3 text-sm text-white resize-none focus:outline-none focus:ring-1 focus:ring-blue-500/60 leading-relaxed"
                        />
                    </div>

                    {/* Acceptance criteria */}
                    <div className="space-y-3">
                        <label className="text-xs font-medium text-gray-500 uppercase tracking-wider">Acceptance Criteria</label>
                        <ul className="space-y-2">
                            {criteria.map((item, i) => (
                                <li key={i} className="flex items-start gap-2 group">
                                    <div className="mt-2 w-1.5 h-1.5 rounded-full bg-blue-500 flex-shrink-0" />
                                    <input
                                        type="text"
                                        value={item}
                                        onChange={e => setCriteria(prev => prev.map((c, idx) => idx === i ? e.target.value : c))}
                                        className="flex-1 bg-transparent text-sm text-gray-300 focus:outline-none border-b border-transparent focus:border-white/20 pb-0.5 transition-colors"
                                    />
                                    <button
                                        onClick={() => removeCriterion(i)}
                                        className="mt-0.5 opacity-0 group-hover:opacity-100 text-gray-600 hover:text-gray-400 transition-all flex-shrink-0"
                                    >
                                        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                                            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                                        </svg>
                                    </button>
                                </li>
                            ))}
                        </ul>
                        {/* Add new criterion */}
                        <div className="flex gap-2">
                            <input
                                type="text"
                                value={newCriterion}
                                onChange={e => setNewCriterion(e.target.value)}
                                onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addCriterion() } }}
                                placeholder="Add a criterion…"
                                className="flex-1 bg-white/[0.04] border border-white/[0.08] rounded px-3 py-1.5 text-sm text-gray-300 placeholder-gray-600 focus:outline-none focus:border-white/20"
                            />
                            <button
                                onClick={addCriterion}
                                disabled={!newCriterion.trim()}
                                className="px-3 py-1.5 text-xs bg-white/[0.06] hover:bg-white/10 disabled:opacity-30 disabled:cursor-not-allowed rounded text-gray-300 transition-colors"
                            >
                                Add
                            </button>
                        </div>
                    </div>

                    {confirmError && (
                        <p className="text-sm text-red-400">{confirmError}</p>
                    )}
                </div>

                {/* Footer */}
                <div className="flex-shrink-0 px-5 py-4 border-t border-white/[0.06]">
                    <button
                        onClick={handleStartExecution}
                        disabled={confirming || !refinedGoal.trim()}
                        className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:cursor-not-allowed text-white text-sm font-medium px-4 py-3 rounded-lg transition-colors flex items-center justify-center gap-2"
                    >
                        {confirming ? (
                            <>
                                <BounceDots />
                                <span>Starting…</span>
                            </>
                        ) : 'Start Execution'}
                    </button>
                </div>
            </div>
        )
    }

    // ── Chat view ─────────────────────────────────────────────────────────────
    return (
        <div className="h-full flex flex-col overflow-hidden">
            {/* Header */}
            <div className="flex-shrink-0 px-5 py-4 border-b border-white/[0.06]">
                <p className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">Refining task</p>
                <p className="text-sm text-white leading-snug line-clamp-2">{goal}</p>
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
                {waitingForOpening ? (
                    <div className="flex items-center gap-3 py-2">
                        <BounceDots />
                        <span className="text-sm text-gray-500">Thinking…</span>
                    </div>
                ) : (
                    messages.map((msg, i) => (
                        <div
                            key={i}
                            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                        >
                            <div
                                className={[
                                    'max-w-[80%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed',
                                    msg.role === 'user'
                                        ? 'bg-blue-600 text-white rounded-br-sm whitespace-pre-wrap'
                                        : 'bg-white/[0.06] text-gray-200 rounded-bl-sm',
                                ].join(' ')}
                            >
                                {msg.role === 'assistant' ? (
                                    <ReactMarkdown
                                        components={{
                                            p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                                            ul: ({ children }) => <ul className="list-disc list-inside mb-2 space-y-0.5">{children}</ul>,
                                            ol: ({ children }) => <ol className="list-decimal list-inside mb-2 space-y-0.5">{children}</ol>,
                                            li: ({ children }) => <li className="text-gray-200">{children}</li>,
                                            strong: ({ children }) => <strong className="text-white font-semibold">{children}</strong>,
                                            code: ({ children }) => <code className="bg-white/10 rounded px-1 py-0.5 text-xs font-mono">{children}</code>,
                                            h1: ({ children }) => <p className="font-semibold text-white mb-1">{children}</p>,
                                            h2: ({ children }) => <p className="font-semibold text-white mb-1">{children}</p>,
                                            h3: ({ children }) => <p className="font-medium text-white mb-1">{children}</p>,
                                        }}
                                    >
                                        {msg.content}
                                    </ReactMarkdown>
                                ) : msg.content}
                            </div>
                        </div>
                    ))
                )}

                {sending && (
                    <div className="flex justify-start">
                        <div className="bg-white/[0.06] rounded-2xl rounded-bl-sm px-4 py-3">
                            <BounceDots />
                        </div>
                    </div>
                )}

                <div ref={bottomRef} />
            </div>

            {/* Input */}
            <div className="flex-shrink-0 px-5 py-4 border-t border-white/[0.06] space-y-3">
                <div className="flex gap-2">
                    <textarea
                        ref={inputRef}
                        value={input}
                        onChange={e => setInput(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder="Reply… (Ctrl+Enter to send)"
                        disabled={sending || waitingForOpening}
                        rows={2}
                        className="flex-1 bg-white/[0.04] border border-white/[0.08] rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 resize-none focus:outline-none focus:ring-1 focus:ring-blue-500/60 disabled:opacity-50"
                    />
                    <button
                        onClick={sendMessage}
                        disabled={!input.trim() || sending || waitingForOpening}
                        className="self-end px-3 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:cursor-not-allowed rounded-lg text-white transition-colors"
                        title="Send (Ctrl+Enter)"
                    >
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                        </svg>
                    </button>
                </div>

                <button
                    onClick={handleFinalize}
                    disabled={synthesizing || messages.length < 2}
                    className="w-full text-sm text-gray-400 hover:text-white border border-white/[0.06] hover:border-white/20 rounded-lg py-2 transition-colors disabled:opacity-30 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                >
                    {synthesizing ? (
                        <>
                            <BounceDots />
                            <span>Synthesizing…</span>
                        </>
                    ) : (
                        <>
                            <span>Looks good — finalize</span>
                            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                            </svg>
                        </>
                    )}
                </button>
            </div>
        </div>
    )
}

export default RefinementChat
