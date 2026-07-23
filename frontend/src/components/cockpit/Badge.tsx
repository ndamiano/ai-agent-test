import React from 'react'

export type Tone = 'green' | 'blue' | 'gray' | 'amber' | 'red'

const TONES: Record<Tone, string> = {
    green: 'bg-green-500/15 text-green-400',
    blue: 'bg-blue-500/15 text-blue-400',
    amber: 'bg-amber-500/15 text-amber-400',
    red: 'bg-red-500/15 text-red-400',
    gray: 'bg-white/[0.06] text-gray-400',
}

export const Badge: React.FC<{ label: string; tone: Tone }> = ({ label, tone }) => (
    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${TONES[tone]}`}>{label}</span>
)
