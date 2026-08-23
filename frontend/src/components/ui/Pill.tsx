import React from 'react'

export type Tone = 'live' | 'wait' | 'fail' | 'idle' | 'accent'

const TONES: Record<Tone, string> = {
    live: 'text-live bg-live/[0.13]',
    wait: 'text-wait bg-wait/[0.13]',
    fail: 'text-fail bg-fail/[0.13]',
    accent: 'text-ember bg-ember/[0.13]',
    idle: 'text-slate bg-bone/[0.07]',
}

export const Pill: React.FC<{ label: string; tone?: Tone }> = ({ label, tone = 'idle' }) => (
    <span className={`inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-xs font-semibold ${TONES[tone]}`}>
        <span className="w-1.5 h-1.5 rounded-full bg-current" />
        {label}
    </span>
)
