import * as React from "react"
import { api, APP_BASE } from "@/lib/api"
import type { Meta, User } from "@/lib/types"

type AppContextValue = {
  user: User | null
  setUser: (user: User | null) => void
  meta: Meta | null
  isAdmin: boolean
  operator: string
  setOperator: (v: string) => void
  openAppeals: number
  setOpenAppeals: (n: number) => void
  openApps: number
  setOpenApps: (n: number) => void
  refreshMeta: () => Promise<void>
  logout: () => Promise<void>
  loading: boolean
  error: string | null
}

const AppContext = React.createContext<AppContextValue | null>(null)

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = React.useState<User | null>(null)
  const [meta, setMeta] = React.useState<Meta | null>(null)
  const [operator, setOperatorState] = React.useState(() => localStorage.getItem("op") || "")
  const [openAppeals, setOpenAppeals] = React.useState(0)
  const [openApps, setOpenApps] = React.useState(0)
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)

  const isAdmin = !!user?.is_admin

  const setOperator = React.useCallback((v: string) => {
    setOperatorState(v)
    localStorage.setItem("op", v)
  }, [])

  const refreshMeta = React.useCallback(async () => {
    const m = await api<Meta>("/meta")
    setMeta(m)
    if (m.title) document.title = m.title
  }, [])

  const logout = React.useCallback(async () => {
    try {
      await fetch(`${APP_BASE}/api/web/auth/logout`, { method: "POST", credentials: "same-origin" })
    } catch {
      /* ignore */
    }
    window.location.assign(`${APP_BASE || ""}/login`.replace(/\/{2,}/g, "/") || "/login")
  }, [])

  React.useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const loginPath = `${APP_BASE || ""}/login`.replace(/\/{2,}/g, "/") || "/login"
        const who = await fetch(`${APP_BASE}/api/web/auth/whoami`, { credentials: "same-origin" })
        if (who.status === 401 || !who.ok) {
          window.location.assign(loginPath)
          return
        }
        const { user: u } = (await who.json()) as { user?: User }
        if (!u) {
          window.location.assign(loginPath)
          return
        }
        if (cancelled) return
        setUser(u)
        const op = u.is_admin ? localStorage.getItem("op") || u.name : u.name
        setOperatorState(op || "")
        const m = await api<Meta>("/meta")
        if (cancelled) return
        setMeta(m)
        if (m.title) document.title = m.title
        if (m.usvo_error) {
          // toast later
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Сервис недоступен")
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const value: AppContextValue = {
    user,
    setUser,
    meta,
    isAdmin,
    operator,
    setOperator,
    openAppeals,
    setOpenAppeals,
    openApps,
    setOpenApps,
    refreshMeta,
    logout,
    loading,
    error,
  }

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>
}

export function useApp() {
  const ctx = React.useContext(AppContext)
  if (!ctx) throw new Error("useApp outside AppProvider")
  return ctx
}
