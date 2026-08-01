import React, { useEffect, useRef } from 'react'

// FNV-1a: any string to a stable 32-bit number, so a game's mark never changes.
export const hashSeed = (s: string): number => {
    let h = 2166136261
    for (let i = 0; i < s.length; i++) {
        h ^= s.charCodeAt(i)
        h = Math.imul(h, 16777619)
    }
    return h >>> 0
}

export const drawSigil = (ctx: CanvasRenderingContext2D, w: number, h: number, seed: number): void => {
    const cx = w / 2
    const cy = h / 2
    const hue = seed % 360
    const r = Math.min(w, h) * 0.28

    ctx.clearRect(0, 0, w, h)
    ctx.fillStyle = '#0E1220'
    ctx.fillRect(0, 0, w, h)

    const glow = ctx.createRadialGradient(cx, cy, 8, cx, cy, w * 0.62)
    glow.addColorStop(0, `hsla(${hue}, 46%, 62%, 0.22)`)
    glow.addColorStop(1, 'hsla(0, 0%, 0%, 0)')
    ctx.fillStyle = glow
    ctx.fillRect(0, 0, w, h)

    ctx.strokeStyle = `hsla(${hue}, 52%, 70%, 0.75)`
    ctx.lineWidth = 1.25
    ctx.beginPath()
    ctx.arc(cx, cy, r, 0, Math.PI * 2)
    ctx.stroke()

    ctx.globalAlpha = 0.5
    ctx.beginPath()
    ctx.arc(cx, cy, r * 1.34, 0, Math.PI * 2)
    ctx.stroke()
    ctx.globalAlpha = 1

    const spokes = 3 + (seed % 5)
    const turn = ((seed % 97) / 97) * Math.PI
    ctx.beginPath()
    for (let i = 0; i < spokes; i++) {
        const a = turn + i * ((Math.PI * 2) / spokes)
        ctx.moveTo(cx + Math.cos(a) * r * 0.28, cy + Math.sin(a) * r * 0.28)
        ctx.lineTo(cx + Math.cos(a) * r * 1.34, cy + Math.sin(a) * r * 1.34)
    }
    ctx.stroke()

    ctx.save()
    ctx.translate(cx, cy)
    ctx.rotate(turn * 1.7)
    ctx.strokeStyle = `hsla(${hue}, 60%, 76%, 0.9)`
    ctx.strokeRect(-r * 0.42, -r * 0.42, r * 0.84, r * 0.84)
    ctx.restore()

    ctx.fillStyle = `hsla(${hue}, 64%, 78%, 0.9)`
    ctx.beginPath()
    ctx.arc(cx, cy, Math.max(2, r * 0.06), 0, Math.PI * 2)
    ctx.fill()
}

// What a game wears when it rendered no art of its own: a mark derived from its run id, so the
// same game always looks the same and nothing has to stand in for a screenshot it never took.
export const Sigil: React.FC<{ seed: string; className?: string; size?: number }> = ({
    seed, className = '', size = 380,
}) => {
    const ref = useRef<HTMLCanvasElement>(null)

    useEffect(() => {
        const ctx = ref.current?.getContext('2d')
        if (ctx) drawSigil(ctx, ref.current!.width, ref.current!.height, hashSeed(seed))
    }, [seed, size])

    return <canvas ref={ref} width={size} height={Math.round(size * 0.75)} aria-hidden
        className={`w-full h-full block ${className}`} />
}
