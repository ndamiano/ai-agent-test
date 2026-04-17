import React, { useState, useRef, useEffect } from 'react'
import { api } from '../api/client'

interface Message {
    role: 'user' | 'assistant'
    content: string
}

const ChatPanel: React.FC = () => {
    const [messages, setMessages] = useState<Message[]>(() => {
        try { return JSON.parse(localStorage.getItem('maestro_chat') || '[]') } catch { return [] }
    })
    const [input, setInput] = useState('')
    const [loading, setLoading] = useState(false)
    const bottomRef = useRef<HTMLDivElement>(null)
    const textareaRef = useRef<HTMLTextAreaElement>(null)

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, [messages, loading])

    useEffect(() => {
        localStorage.setItem('maestro_chat', JSON.stringify(messages))
    }, [messages])

    useEffect(() => {
        const el = textareaRef.current
        if (!el) return
        el.style.height = 'auto'
        el.style.height = `${Math.min(el.scrollHeight, 120)}px`
    }, [input])

    const send = async () => {
        const text = input.trim()
        if (!text || loading) return
        setInput('')
        setMessages(prev => [...prev, { role: 'user', content: text }])
        setLoading(true)
        try {
            const res = await api.sendChatMessage(text)
            setMessages(prev => [...prev, { role: 'assistant', content: res.message }])
        } catch (e) {
            setMessages(prev => [...prev, { role: 'assistant', content: 'Something went wrong. Try again.' }])
        } finally {
            setLoading(false)
        }
    }

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
            e.preventDefault()
            send()
        }
    }

    return (
        <div className="flex flex-col h-full">
            {/* Message list */}
            <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
                {messages.length === 0 && (
                    <div className="flex items-center justify-center h-full">
                        <p className="text-gray-500 text-sm">Say something. Ask for anything.</p>
                    </div>
                )}
                {messages.length > 0 && (
                    <div className="flex justify-center">
                        <button
                            onClick={() => { setMessages([]); api.clearChatSession() }}
                            className="text-xs text-gray-600 hover:text-gray-400 transition-colors"
                        >
                            Clear conversation
                        </button>
                    </div>
                )}
                {messages.map((msg, i) => (
                    <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                        <div className={`max-w-[80%] rounded-lg px-4 py-2 text-sm whitespace-pre-wrap ${
                            msg.role === 'user'
                                ? 'bg-blue-600 text-white'
                                : 'bg-[#1a1a1a] text-gray-200 border border-white/[0.06]'
                        }`}>
                            {msg.content}
                        </div>
                    </div>
                ))}
                {loading && (
                    <div className="flex justify-start">
                        <div className="bg-[#1a1a1a] border border-white/[0.06] rounded-lg px-4 py-2">
                            <div className="flex gap-1 items-center h-4">
                                <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                                <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                                <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                            </div>
                        </div>
                    </div>
                )}
                <div ref={bottomRef} />
            </div>

            {/* Input */}
            <div className="flex-shrink-0 border-t border-white/[0.06] px-4 py-3">
                <div className="flex gap-3 items-end">
                    <textarea
                        ref={textareaRef}
                        className="flex-1 bg-[#1a1a1a] text-white border border-white/[0.06] rounded-lg px-3 py-2 text-sm resize-none focus:outline-none focus:border-blue-500"
                        placeholder="Message Maestro... (Ctrl+Enter to send)"
                        value={input}
                        onChange={e => setInput(e.target.value)}
                        onKeyDown={handleKeyDown}
                        rows={1}
                        style={{ minHeight: '40px', maxHeight: '120px' }}
                        disabled={loading}
                    />
                    <button
                        onClick={send}
                        disabled={loading || !input.trim()}
                        className="bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
                    >
                        Send
                    </button>
                </div>
            </div>
        </div>
    )
}

export default ChatPanel
