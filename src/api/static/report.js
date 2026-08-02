// Maestro console reporter — injected into a staged game's index.html AS IT IS SERVED (the game
// folder on disk stays pristine). Runs inside the game document because a cross-origin parent's
// window.onerror only ever sees a scrubbed "Script error." with no file, line, or stack.
// Everything it posts is untrusted display data to the parent — nothing there may act on it.
(() => {
    'use strict'
    if (window.parent === window) return

    // A throw inside requestAnimationFrame repeats ~60/s forever; the parent dedupes, this cap
    // just keeps a pathological game from flooding postMessage itself.
    const MAX_SENDS = 500
    let sent = 0

    const post = (kind, message, extra) => {
        if (sent >= MAX_SENDS) return
        sent += 1
        try {
            window.parent.postMessage(Object.assign({
                source: 'maestro-report',
                kind,
                message: String(message).slice(0, 2000),
            }, extra || {}), '*')
        } catch (_) { /* an unclonable payload must never take the game down */ }
    }

    const frame = (stack) => {
        if (!stack) return null
        const lines = String(stack).split('\n')
        return (lines[1] || lines[0] || '').trim().slice(0, 300) || null
    }

    // Capture phase, so failed <img>/<script>/<link> loads (which never bubble) land here too.
    window.addEventListener('error', (e) => {
        if (e.target && e.target !== window && (e.target.src || e.target.href)) {
            post('resource', 'failed to load ' + (e.target.src || e.target.href))
            return
        }
        post('error', e.message || 'uncaught error', {
            src: e.filename || null, line: e.lineno || null, frame: frame(e.error && e.error.stack),
        })
    }, true)

    window.addEventListener('unhandledrejection', (e) => {
        const r = e.reason
        post('rejection', r && r.message ? r.message : String(r), {
            frame: frame(r && r.stack),
        })
    })

    for (const level of ['error', 'warn']) {
        const original = console[level].bind(console)
        console[level] = (...args) => {
            post('console-' + level, args.map((a) => {
                if (typeof a === 'string') return a
                try { return JSON.stringify(a) } catch (_) { return String(a) }
            }).join(' ').slice(0, 2000))
            original(...args)
        }
    }
})()
