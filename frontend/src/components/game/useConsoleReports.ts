import { useCallback, useEffect, useState } from 'react'

// What the injected reporter (src/api/static/report.js) posts out of the game iframe. Untrusted
// display data ONLY — nothing in the parent may branch on it beyond showing it to the human.
export interface ConsoleReport {
    key: string
    kind: string
    message: string
    detail: string | null  // src:line or the first stack frame, when the reporter had one
    count: number
}

// A throw inside requestAnimationFrame arrives ~60/s; dedupe on (kind, message, frame) and count
// repeats instead of listing them. Hard cap so a chatty game can't grow the list unbounded.
export const MAX_REPORTS = 50

interface ReporterMessage {
    source?: string
    kind?: string
    message?: string
    src?: string | null
    line?: number | null
    frame?: string | null
}

export const foldReport = (list: ConsoleReport[], data: ReporterMessage): ConsoleReport[] => {
    if (data?.source !== 'maestro-report' || typeof data.message !== 'string') return list
    const kind = typeof data.kind === 'string' ? data.kind : 'error'
    const detail = data.frame ?? (data.src ? `${data.src}${data.line ? `:${data.line}` : ''}` : null)
    const key = `${kind}|${data.message}|${detail ?? ''}`
    const existing = list.find(r => r.key === key)
    if (existing) return list.map(r => (r.key === key ? { ...r, count: r.count + 1 } : r))
    if (list.length >= MAX_REPORTS) return list
    return [...list, { key, kind, message: data.message, detail, count: 1 }]
}

export const reportsAsNote = (reports: ConsoleReport[]): string =>
    reports.map(r => {
        const times = r.count > 1 ? ` (×${r.count})` : ''
        const where = r.detail ? ` — ${r.detail}` : ''
        return `- [${r.kind}] ${r.message}${times}${where}`
    }).join('\n')

// Collects reporter messages while a play session is live. `expectedOrigin` is the game origin
// the session came from — a message from anywhere else is ignored, so no other page (or ad in
// another tab's frame) can plant "errors" in front of the fix button.
export function useConsoleReports(expectedOrigin: string | null): {
    reports: ConsoleReport[]
    clear: () => void
} {
    const [reports, setReports] = useState<ConsoleReport[]>([])

    useEffect(() => {
        if (expectedOrigin == null) return
        const onMessage = (e: MessageEvent) => {
            if (e.origin !== expectedOrigin) return
            setReports(list => foldReport(list, e.data as ReporterMessage))
        }
        window.addEventListener('message', onMessage)
        return () => window.removeEventListener('message', onMessage)
    }, [expectedOrigin])

    const clear = useCallback(() => setReports([]), [])
    return { reports, clear }
}
