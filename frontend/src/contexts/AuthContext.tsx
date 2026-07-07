import React, { createContext, useContext, useCallback, useEffect, useState } from 'react'
import { api, getAuthToken, setAuthToken, setUnauthorizedHandler } from '../api/client'

interface AuthUser {
    id: string
    handle: string
    role: string
}

interface AuthContextValue {
    token: string | null
    user: AuthUser | null
    balance: number | null
    login: (handle: string, password: string) => Promise<void>
    logout: () => Promise<void>
    refreshBalance: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [token, setToken] = useState<string | null>(() => getAuthToken())
    const [user, setUser] = useState<AuthUser | null>(null)
    const [balance, setBalance] = useState<number | null>(null)

    const clear = useCallback(() => {
        setToken(null)
        setUser(null)
        setBalance(null)
    }, [])

    const logout = useCallback(async () => {
        await api.logout()   // revoke server-side before dropping the local token
        setAuthToken(null)
        clear()
    }, [clear])

    // Central 401 handling: the client clears the stored token, we drop back to the login gate.
    useEffect(() => {
        setUnauthorizedHandler(clear)
        return () => setUnauthorizedHandler(null)
    }, [clear])

    const refreshBalance = useCallback(async () => {
        if (!getAuthToken()) return
        try {
            const me = await api.me()
            setUser({ id: me.id, handle: me.handle, role: me.role })
            setBalance(me.balance)
        } catch {
            // A 401 is handled centrally (clears the token); nothing else to do here.
        }
    }, [])

    // Hydrate the user + balance whenever a token is present (mount, or just after login).
    useEffect(() => {
        if (token) refreshBalance()
    }, [token, refreshBalance])

    const login = useCallback(async (handle: string, password: string) => {
        const res = await api.login(handle, password)
        setAuthToken(res.token)
        setUser(res.user)
        setToken(res.token)
    }, [])

    return (
        <AuthContext.Provider value={{ token, user, balance, login, logout, refreshBalance }}>
            {children}
        </AuthContext.Provider>
    )
}

export const useAuth = (): AuthContextValue => {
    const ctx = useContext(AuthContext)
    if (!ctx) throw new Error('useAuth must be used within AuthProvider')
    return ctx
}
