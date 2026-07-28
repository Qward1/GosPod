import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom"
import * as React from "react"
import {
  MessageSquare,
  Users,
  Bot,
  FileCheck2,
  BarChart3,
  Send,
  ScrollText,
  Settings,
  Shield,
  LogOut,
  Moon,
  Sun,
  Monitor,
  ChevronDown,
} from "lucide-react"
import { useTheme } from "next-themes"
import { useApp } from "@/hooks/use-app"
import { cn, initials } from "@/lib/utils"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import type { ViewId } from "@/lib/types"

const THEME_TABS = [
  { value: "light", label: "Светлая", icon: Sun },
  { value: "dark", label: "Тёмная", icon: Moon },
  { value: "system", label: "Авто", icon: Monitor },
] as const

const NAV: { id: ViewId; to: string; label: string; icon: typeof MessageSquare; admin?: boolean }[] = [
  { id: "appeals", to: "/", label: "Обращения", icon: MessageSquare },
  { id: "cards", to: "/cards", label: "Карточки УСВО", icon: Users },
  { id: "ai-chat", to: "/ai-chat", label: "ИИ ассистент", icon: Bot },
  { id: "applications", to: "/applications", label: "Заявления", icon: FileCheck2 },
  { id: "analytics", to: "/analytics", label: "Аналитика", icon: BarChart3 },
  { id: "broadcast", to: "/broadcast", label: "Рассылки", icon: Send, admin: true },
  { id: "audit", to: "/audit", label: "Журнал действий", icon: ScrollText, admin: true },
]

const TITLES: Record<string, { title: string; subtitle: string }> = {
  "/": { title: "Обращения", subtitle: "Вопросы граждан, распределённые на оператора" },
  "/cards": { title: "Карточки УСВО", subtitle: "Учёт семей военнослужащих" },
  "/ai-chat": { title: "ИИ ассистент", subtitle: "Ваш персональный ИИ ассистент" },
  "/applications": { title: "Заявления", subtitle: "Заявки на меры поддержки граждан" },
  "/analytics": { title: "Аналитика", subtitle: "Сводка по обращениям и карточкам" },
  "/broadcast": { title: "Рассылки", subtitle: "Массовые уведомления в MAX" },
  "/audit": { title: "Журнал действий", subtitle: "Аудит действий операторов" },
  "/settings": { title: "Настройки", subtitle: "Профиль, тема и параметры кабинета" },
}

function pageHead(pathname: string) {
  if (/^\/usvo\/cards\/\d+/.test(pathname)) {
    return { title: "Карточки УСВО", subtitle: "Учёт семей военнослужащих" }
  }
  return TITLES[pathname] || TITLES["/"]
}

export function AppShell() {
  const { user, isAdmin, openAppeals, openApps, logout, loading, error } = useApp()
  const { theme, setTheme } = useTheme()
  const [mounted, setMounted] = React.useState(false)
  const location = useLocation()
  const navigate = useNavigate()
  const head = pageHead(location.pathname)
  const currentTheme = mounted ? theme || "system" : "system"

  React.useEffect(() => setMounted(true), [])

  if (loading) {
    return (
      <div className="flex min-h-svh items-center justify-center text-sm text-muted-foreground">
        Загрузка кабинета…
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex min-h-svh flex-col items-center justify-center gap-2 p-8 text-center">
        <h1 className="text-xl font-semibold">Сервис недоступен</h1>
        <p className="text-muted-foreground">{error}</p>
      </div>
    )
  }

  return (
    <div className="flex h-svh overflow-hidden bg-background">
      <aside className="flex h-full w-64 shrink-0 flex-col overflow-hidden border-r border-sidebar-border bg-sidebar text-sidebar-foreground">
        <div className="flex h-20 shrink-0 items-center gap-3 border-b border-sidebar-border px-4">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground">
            <Shield className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold leading-tight">Контакт-центр СВО</div>
            <div className="truncate text-xs text-muted-foreground">Господдержка · МО</div>
          </div>
        </div>
        <div className="min-h-0 min-w-0 flex-1 overflow-hidden px-4 py-3">
          <nav className="flex w-full flex-col gap-1">
            {NAV.filter((n) => !n.admin || isAdmin).map((item) => {
              const Icon = item.icon
              const badge = item.id === "appeals" ? openAppeals : item.id === "applications" ? openApps : 0
              return (
                <NavLink
                  key={item.id}
                  to={item.to}
                  end={item.to === "/"}
                  className={({ isActive }) =>
                    cn(
                      "flex w-full min-w-0 items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                      isActive
                        ? "bg-sidebar-accent text-sidebar-accent-foreground"
                        : "text-muted-foreground hover:bg-sidebar-accent/70 hover:text-sidebar-accent-foreground",
                    )
                  }
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  <span className="min-w-0 flex-1 truncate">{item.label}</span>
                  {badge > 0 ? (
                    <Badge variant="secondary" className="h-5 min-w-5 justify-center border-border px-1.5">
                      {badge}
                    </Badge>
                  ) : null}
                </NavLink>
              )
            })}
          </nav>
        </div>
        <div className="shrink-0 border-t border-sidebar-border px-4 py-3">
          <div className="w-full">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  className="flex w-full min-w-0 items-center gap-2 rounded-lg px-2 py-2 text-left outline-none transition-colors hover:bg-sidebar-accent/70 focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <Avatar className="h-8 w-8 shrink-0">
                    <AvatarFallback className="text-xs">{initials(user?.name)}</AvatarFallback>
                  </Avatar>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium leading-tight">{user?.name}</div>
                    <div className="truncate text-xs text-muted-foreground leading-tight">
                      {user?.sub || user?.role}
                    </div>
                  </div>
                  <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent
                side="top"
                align="start"
                className="w-[var(--radix-dropdown-menu-trigger-width)]"
              >
              <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
                Тема оформления
              </DropdownMenuLabel>
              <div
                role="tablist"
                aria-label="Тема оформления"
                className="mx-2 mb-1.5 grid h-8 grid-cols-3 rounded-lg bg-muted p-0.5"
              >
                {THEME_TABS.map((tab) => {
                  const Icon = tab.icon
                  const active = currentTheme === tab.value
                  return (
                    <button
                      key={tab.value}
                      type="button"
                      role="tab"
                      aria-selected={active}
                      aria-label={tab.label}
                      title={tab.label}
                      className={cn(
                        "inline-flex items-center justify-center rounded-md transition-colors",
                        active
                          ? "bg-background text-foreground shadow-sm"
                          : "text-muted-foreground hover:text-foreground",
                      )}
                      onClick={() => setTheme(tab.value)}
                      onPointerDown={(e) => e.preventDefault()}
                    >
                      <Icon className="h-4 w-4" />
                    </button>
                  )
                })}
              </div>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => navigate("/settings")}>
                <Settings />
                Настройки
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                className="text-destructive focus:text-destructive"
                onSelect={() => void logout()}
              >
                <LogOut />
                Выйти
              </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </aside>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex h-20 shrink-0 flex-col justify-center border-b px-6">
          <h1 className="text-xl font-semibold tracking-tight">{head.title}</h1>
          <p className="text-sm text-muted-foreground">{head.subtitle}</p>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto p-6">
          <div id="page-actions" className="mb-4 flex w-full flex-wrap items-center justify-end gap-2 empty:hidden [&:has(.flex-1)]:justify-start [&_input]:h-8 [&_button]:h-8" />
          <Outlet />
        </main>
      </div>
    </div>
  )
}
