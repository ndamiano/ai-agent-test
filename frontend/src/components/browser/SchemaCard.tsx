import React, { useState } from 'react'
import type { AssetRenderer } from './types'

function isPrimitive(v: unknown): v is string | number | boolean | null | undefined {
    return v === null || v === undefined || typeof v !== 'object'
}

// One field of the asset's content, editable in place (D5). Primitives get a plain input/select;
// objects/arrays fall back to a JSON textarea. Saving a field commits the whole content object
// (the server's edit endpoint replaces content wholesale) with just that key changed.
const FieldEditor: React.FC<{
    fieldKey: string
    value: unknown
    editable: boolean
    onCommit: (next: unknown) => Promise<void>
}> = ({ fieldKey, value, editable, onCommit }) => {
    const [editing, setEditing] = useState(false)
    const [draft, setDraft] = useState('')
    const [busy, setBusy] = useState(false)
    const [err, setErr] = useState<string | null>(null)

    const isBool = typeof value === 'boolean'
    const isNum = typeof value === 'number'
    const prim = isPrimitive(value)

    const startEdit = () => {
        setErr(null)
        setDraft(prim ? String(value ?? '') : JSON.stringify(value, null, 2))
        setEditing(true)
    }

    const save = async () => {
        setBusy(true); setErr(null)
        try {
            let next: unknown
            if (isBool) next = draft === 'true'
            else if (isNum) {
                next = Number(draft)
                if (Number.isNaN(next as number)) throw new Error('not a number')
            } else if (prim) next = draft
            else next = JSON.parse(draft)
            await onCommit(next)
            setEditing(false)
        } catch (e) {
            setErr(e instanceof Error ? e.message : 'invalid value')
        } finally {
            setBusy(false)
        }
    }

    return (
        <div className="py-1.5 border-b border-white/[0.04] last:border-0">
            <div className="flex items-center justify-between gap-2">
                <span className="text-gray-500 text-[11px] font-mono">{fieldKey}</span>
                {editable && !editing && (
                    <button onClick={startEdit} className="text-gray-600 hover:text-gray-300 text-[10px]">edit</button>
                )}
            </div>
            {editing ? (
                <div className="mt-1 space-y-1">
                    {isBool ? (
                        <select value={draft} onChange={e => setDraft(e.target.value)}
                            className="bg-black/40 border border-white/[0.1] rounded text-xs text-gray-200 px-1.5 py-1">
                            <option value="true">true</option>
                            <option value="false">false</option>
                        </select>
                    ) : prim ? (
                        <input value={draft} onChange={e => setDraft(e.target.value)}
                            className="w-full bg-black/40 border border-white/[0.1] rounded text-xs text-gray-200 px-2 py-1" />
                    ) : (
                        <textarea value={draft} onChange={e => setDraft(e.target.value)} spellCheck={false}
                            className="w-full bg-black/50 border border-white/[0.1] rounded p-2 font-mono text-[11px] text-gray-200 h-32" />
                    )}
                    <div className="flex gap-2">
                        <button onClick={save} disabled={busy} className="text-green-400 hover:text-green-300 text-[11px]">Save</button>
                        <button onClick={() => setEditing(false)} disabled={busy} className="text-gray-500 hover:text-gray-300 text-[11px]">Cancel</button>
                    </div>
                    {err && <p className="text-red-400 text-[11px]">{err}</p>}
                </div>
            ) : (
                <div className="text-gray-200 text-[13px] mt-0.5 whitespace-pre-wrap break-words">
                    {prim ? String(value ?? '—') : <span className="text-gray-500 font-mono text-[11px]">{JSON.stringify(value)}</span>}
                </div>
            )}
        </div>
    )
}

// The generic fallback (D3): renders ANY asset's content as an editable field list. Every
// component uses this until a bespoke renderer registers (Wave 5 / D4) — see registry.ts.
const SchemaCard: AssetRenderer = ({ asset, editable, onSave }) => {
    const content = asset.content ?? {}
    const keys = Object.keys(content)
    if (keys.length === 0) return <p className="text-gray-600 text-sm">Empty component.</p>
    return (
        <div>
            {keys.map(k => (
                <FieldEditor key={k} fieldKey={k} value={content[k]} editable={editable}
                    onCommit={next => onSave({ ...content, [k]: next })} />
            ))}
        </div>
    )
}

export default SchemaCard
