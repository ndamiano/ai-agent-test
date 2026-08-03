import React, { useState } from 'react'
import { Button } from '../ui/Button'

const SKIP_KEY = 'maestro_skip_enhance_notice'

// Client-side only, by design: whether someone wants the reminder again is not account data.
export const shouldShowNotice = () => localStorage.getItem(SKIP_KEY) === null

// Shown when the user builds with enhancement off. Not a gate — "Build anyway" proceeds.
export const EnhanceNotice: React.FC<{
    onEnhance: () => void
    onBuildAnyway: () => void
    onClose: () => void
}> = ({ onEnhance, onBuildAnyway, onClose }) => {
    const [dontShow, setDontShow] = useState(false)

    const remember = () => { if (dontShow) localStorage.setItem(SKIP_KEY, '1') }

    return (
        <div className="fixed inset-0 z-50 bg-ink/80 grid place-items-center p-6" onClick={onClose}>
            <div className="w-full max-w-lg bg-panel border border-edge rounded-md p-5 flex flex-col gap-4"
                onClick={e => e.stopPropagation()}>
                <h3 className="font-display text-lg">Build without the plan?</h3>
                <p className="text-sm text-slate">
                    Planning first is how we get the best games: it splits your idea into build
                    stages so the hardest part gets full attention before the rest is added. We
                    measured it — planned builds come out stronger, faster, and cheaper to fix.
                    You can always skip it; you'll still get a game.
                </p>
                <label className="flex items-center gap-2 text-xs text-dim cursor-pointer">
                    <input type="checkbox" checked={dontShow}
                        onChange={e => setDontShow(e.target.checked)} />
                    Don't show this again
                </label>
                <div className="flex items-center gap-3">
                    <Button variant="primary" size="md"
                        onClick={() => { remember(); onEnhance() }}>
                        Plan it first
                    </Button>
                    <Button variant="ghost" size="md"
                        onClick={() => { remember(); onBuildAnyway() }}>
                        Build anyway
                    </Button>
                </div>
            </div>
        </div>
    )
}
