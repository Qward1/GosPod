import * as React from "react"
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom"
import { APP_BASE } from "@/lib/api"
import { AppProvider } from "@/hooks/use-app"
import { AppShell } from "@/components/layout/app-shell"
import { LoginPage } from "@/pages/login"
import { AppealsPage } from "@/pages/appeals"
import { CardsPage } from "@/pages/cards"
import { AiChatPage } from "@/pages/ai-chat"
import { ApplicationsPage } from "@/pages/applications"
import { AnalyticsPage } from "@/pages/analytics"
import { BroadcastPage } from "@/pages/broadcast"
import { AuditPage } from "@/pages/audit"
import { SettingsPage } from "@/pages/settings"

/**
 * Держит завершающий слэш на главной: react-router для index-маршрута ставит
 * адрес ровно в basename (`/jnserver/8/application`), а кабинет должен жить по
 * `/jnserver/8/application/` — иначе относительные ссылки и `<base href>`
 * считаются от родительского каталога. Правим адрес через replaceState, без
 * перезагрузки; APP_BASE от этого не меняется (appBaseFromPath режет слэши).
 */
function TrailingSlashKeeper() {
  const location = useLocation()
  React.useEffect(() => {
    if (!APP_BASE || window.location.pathname !== APP_BASE) return
    const url = `${APP_BASE}/${window.location.search}${window.location.hash}`
    window.history.replaceState(window.history.state, "", url)
  }, [location])
  return null
}

function ProtectedShell() {
  return (
    <AppProvider>
      <AppShell />
    </AppProvider>
  )
}

export default function App() {
  const basename = APP_BASE || "/"

  return (
    <BrowserRouter basename={basename}>
      <TrailingSlashKeeper />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<ProtectedShell />}>
          <Route index element={<AppealsPage />} />
          <Route path="cards" element={<CardsPage />} />
          <Route path="ai-chat" element={<AiChatPage />} />
          <Route path="applications" element={<ApplicationsPage />} />
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="broadcast" element={<BroadcastPage />} />
          <Route path="audit" element={<AuditPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="usvo/cards/:id" element={<CardsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
