import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useTaskSocket } from './useTaskSocket'

vi.stubEnv('VITE_WS_URL', 'ws://localhost:8000')

type WsHandler = {
    onopen: (() => void) | null
    onmessage: ((event: { data: string }) => void) | null
    onclose: (() => void) | null
    onerror: (() => void) | null
    close: ReturnType<typeof vi.fn>
    send: ReturnType<typeof vi.fn>
}

function createMockWs(): WsHandler {
    return {
        onopen: null,
        onmessage: null,
        onclose: null,
        onerror: null,
        close: vi.fn(),
        send: vi.fn(),
    }
}

let mockWsInstances: WsHandler[] = []
let wsConstructorSpy: ReturnType<typeof vi.fn>

beforeEach(() => {
    vi.useFakeTimers()
    mockWsInstances = []
    // Must use a real function (not arrow) so `new WebSocket(...)` works
    const MockWebSocket = function (this: WsHandler) {
        Object.assign(this, createMockWs())
        mockWsInstances.push(this)
    } as unknown as typeof WebSocket
    wsConstructorSpy = vi.fn(MockWebSocket as any)
    // @ts-expect-error stubbing global
    global.WebSocket = wsConstructorSpy
})

afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
})

describe('useTaskSocket', () => {
    it('does not connect when taskId is null', () => {
        const { result } = renderHook(() => useTaskSocket(null))
        expect(wsConstructorSpy).not.toHaveBeenCalled()
        expect(result.current.connected).toBe(false)
        expect(result.current.messages).toEqual([])
    })

    it('connects when taskId is provided', () => {
        renderHook(() => useTaskSocket('task-1'))
        expect(wsConstructorSpy).toHaveBeenCalledWith(
            'ws://localhost:8000/api/tasks/task-1/ws',
        )
        expect(mockWsInstances).toHaveLength(1)
    })

    it('sets connected to true on open', () => {
        const { result } = renderHook(() => useTaskSocket('task-1'))
        act(() => {
            mockWsInstances[0].onopen!()
        })
        expect(result.current.connected).toBe(true)
        expect(result.current.error).toBeNull()
    })

    it('receives messages', () => {
        const { result } = renderHook(() => useTaskSocket('task-1'))
        const msg = { type: 'task_status', task_id: 'task-1', task: { id: 'task-1' } }
        act(() => {
            mockWsInstances[0].onmessage!({ data: JSON.stringify(msg) })
        })
        expect(result.current.messages).toHaveLength(1)
        expect(result.current.messages[0]).toEqual(msg)
    })

    it('sets finished on task_completed', () => {
        const { result } = renderHook(() => useTaskSocket('task-1'))
        act(() => {
            mockWsInstances[0].onmessage!({
                data: JSON.stringify({ type: 'task_completed', task_id: 'task-1' }),
            })
        })
        expect(result.current.finished).toBe(true)
    })

    it('sets finished on task_failed', () => {
        const { result } = renderHook(() => useTaskSocket('task-1'))
        act(() => {
            mockWsInstances[0].onmessage!({
                data: JSON.stringify({ type: 'task_failed', task_id: 'task-1', error: 'boom' }),
            })
        })
        expect(result.current.finished).toBe(true)
    })

    it('reconnects with exponential backoff on close', () => {
        renderHook(() => useTaskSocket('task-1'))

        act(() => { mockWsInstances[0].onclose!() })
        expect(mockWsInstances).toHaveLength(1)

        act(() => { vi.advanceTimersByTime(1000) })
        expect(mockWsInstances).toHaveLength(2)

        // Second close: backoff = 2s
        act(() => { mockWsInstances[1].onclose!() })
        act(() => { vi.advanceTimersByTime(1999) })
        expect(mockWsInstances).toHaveLength(2)
        act(() => { vi.advanceTimersByTime(1) })
        expect(mockWsInstances).toHaveLength(3)

        // Third close: backoff = 4s
        act(() => { mockWsInstances[2].onclose!() })
        act(() => { vi.advanceTimersByTime(4000) })
        expect(mockWsInstances).toHaveLength(4)

        // Fourth close: backoff = 8s
        act(() => { mockWsInstances[3].onclose!() })
        act(() => { vi.advanceTimersByTime(8000) })
        expect(mockWsInstances).toHaveLength(5)
    })

    it('resets retry count on successful open', () => {
        renderHook(() => useTaskSocket('task-1'))

        // Trigger 2 closes so retryCount = 2
        act(() => { mockWsInstances[0].onclose!() })
        act(() => { vi.advanceTimersByTime(1000) })
        act(() => { mockWsInstances[1].onclose!() })
        act(() => { vi.advanceTimersByTime(2000) })
        expect(mockWsInstances).toHaveLength(3)

        // Successful open resets counter
        act(() => { mockWsInstances[2].onopen!() })

        // Next close should backoff from 1s again
        act(() => { mockWsInstances[2].onclose!() })
        act(() => { vi.advanceTimersByTime(999) })
        expect(mockWsInstances).toHaveLength(3)
        act(() => { vi.advanceTimersByTime(1) })
        expect(mockWsInstances).toHaveLength(4)
    })
})

describe('useTaskSocket - advanced', () => {
    it('caps backoff at 30s', () => {
        renderHook(() => useTaskSocket('task-1'))

        for (let i = 0; i < 5; i++) {
            act(() => { mockWsInstances[i].onclose!() })
            const delay = Math.min(1000 * 2 ** (i + 1), 30000)
            act(() => { vi.advanceTimersByTime(delay) })
        }
        expect(mockWsInstances).toHaveLength(6)

        act(() => { mockWsInstances[5].onclose!() })
        act(() => { vi.advanceTimersByTime(29999) })
        expect(mockWsInstances).toHaveLength(6)
        act(() => { vi.advanceTimersByTime(1) })
        expect(mockWsInstances).toHaveLength(7)
    })

    it('stops reconnecting after MAX_RETRIES (10)', () => {
        renderHook(() => useTaskSocket('task-1'))

        for (let i = 0; i <= 10; i++) {
            act(() => { mockWsInstances[i].onclose!() })
            if (i < 10) {
                const delay = Math.min(1000 * 2 ** i, 30000)
                act(() => { vi.advanceTimersByTime(delay) })
            }
        }
        expect(mockWsInstances).toHaveLength(11)

        act(() => { vi.advanceTimersByTime(60000) })
        expect(mockWsInstances).toHaveLength(11)
    })

    it('stops reconnecting when task finishes', () => {
        renderHook(() => useTaskSocket('task-1'))

        act(() => {
            mockWsInstances[0].onmessage!({
                data: JSON.stringify({ type: 'task_completed', task_id: 'task-1' }),
            })
        })

        act(() => { mockWsInstances[0].onclose!() })
        act(() => { vi.advanceTimersByTime(60000) })
        expect(mockWsInstances).toHaveLength(1)
    })

    it('does not reconnect after task_failed', () => {
        renderHook(() => useTaskSocket('task-1'))

        act(() => {
            mockWsInstances[0].onmessage!({
                data: JSON.stringify({ type: 'task_failed', task_id: 'task-1', error: 'x' }),
            })
        })
        act(() => { mockWsInstances[0].onclose!() })
        act(() => { vi.advanceTimersByTime(60000) })
        expect(mockWsInstances).toHaveLength(1)
    })

    it('cleans up on unmount', () => {
        const { unmount } = renderHook(() => useTaskSocket('task-1'))

        const firstWs = mockWsInstances[0]
        expect(firstWs.close).not.toHaveBeenCalled()
        unmount()
        expect(firstWs.close).toHaveBeenCalled()
    })

    it('cleans up reconnect timer on unmount', () => {
        const { unmount } = renderHook(() => useTaskSocket('task-1'))

        act(() => { mockWsInstances[0].onclose!() })
        unmount()
        act(() => { vi.advanceTimersByTime(60000) })
        expect(mockWsInstances).toHaveLength(1)
    })

    it('cleans up and reconnects when taskId changes', () => {
        const { rerender } = renderHook(
            ({ taskId }) => useTaskSocket(taskId),
            { initialProps: { taskId: 'task-1' as string | null } },
        )

        const firstWs = mockWsInstances[0]
        expect(firstWs.close).not.toHaveBeenCalled()

        rerender({ taskId: 'task-2' })
        expect(firstWs.close).toHaveBeenCalled()
        expect(wsConstructorSpy).toHaveBeenLastCalledWith(
            'ws://localhost:8000/api/tasks/task-2/ws',
        )
        expect(mockWsInstances).toHaveLength(2)
    })

    it('does not reconnect on null taskId', () => {
        const { rerender } = renderHook(
            ({ taskId }) => useTaskSocket(taskId),
            { initialProps: { taskId: 'task-1' as string | null } },
        )

        rerender({ taskId: null })
        act(() => { vi.advanceTimersByTime(60000) })
        expect(mockWsInstances).toHaveLength(1)
    })

    it('accumulates multiple messages', () => {
        const { result } = renderHook(() => useTaskSocket('task-1'))
        act(() => {
            mockWsInstances[0].onmessage!({
                data: JSON.stringify({ type: 'subtask_started', task_id: 'task-1', subtask_id: 's1', message: 'hi', timestamp: 't' }),
            })
        })
        act(() => {
            mockWsInstances[0].onmessage!({
                data: JSON.stringify({ type: 'subtask_completed', task_id: 'task-1', subtask_id: 's1', message: 'done', timestamp: 't' }),
            })
        })
        expect(result.current.messages).toHaveLength(2)
        expect(result.current.messages[0].type).toBe('subtask_started')
        expect(result.current.messages[1].type).toBe('subtask_completed')
    })
})