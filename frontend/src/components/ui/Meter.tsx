import React from 'react'

// A fraction 0..1 as a bar and nothing else — the budget it shows is deliberately numberless.
export const Meter: React.FC<{ value: number; tone?: 'mana' | 'ember'; label?: string }> = ({
    value, tone = 'mana', label,
}) => (
    <div className="flex flex-col gap-1.5">
        {label && <span className="text-xs text-dim font-mono uppercase tracking-wider">{label}</span>}
        <div className="h-[3px] rounded-sm bg-bone/[0.08] overflow-hidden">
            <div
                className={`h-full transition-[width] duration-700 ${tone === 'mana' ? 'bg-mana' : 'bg-ember'}`}
                style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
            />
        </div>
    </div>
)
