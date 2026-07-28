import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"
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
