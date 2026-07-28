import * as React from "react"
import { useNavigate } from "react-router-dom"
import { Eye, EyeOff, Loader2 } from "lucide-react"
import { useTheme } from "next-themes"
import { toast } from "sonner"
import { APP_BASE } from "@/lib/api"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { FormField } from "@/components/ui/form-field"
import logoT1 from "@/assets/logo-t1.png"
import logoT1Dark from "@/assets/logo-t1-dark.png"

const SLIDES = [
  {
    title: "Контакт-центр",
    text: "Господдержка семей участников СВО в Московской области — обращения, меры и аналитика в одном контуре",
  },
  {
    title: "Обращения граждан",
    text: "Оперативная работа с вопросами жителей: распределение, ответы и контроль сроков",
  },
  {
    title: "Единый контур",
    text: "Карточки УСВО, заявления на меры поддержки, рассылки в MAX и база знаний для операторов",
  },
] as const

export function LoginPage() {
  const navigate = useNavigate()
  const { theme, resolvedTheme } = useTheme()
  const [identifier, setIdentifier] = React.useState("")
  const [password, setPassword] = React.useState("")
  const [showPassword, setShowPassword] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [loading, setLoading] = React.useState(false)
  const [demo, setDemo] = React.useState(false)
  const [slide, setSlide] = React.useState(0)
  const [mounted, setMounted] = React.useState(false)

  React.useEffect(() => {
    setMounted(true)
  }, [])

  const isDark = mounted && (theme === "system" ? resolvedTheme : theme) === "dark"
  const logoSrc = isDark ? logoT1Dark : logoT1

  React.useEffect(() => {
    ;(async () => {
      try {
        const who = await fetch(`${APP_BASE}/api/web/auth/whoami`, { credentials: "same-origin" })
        if (who.ok) {
          navigate("/", { replace: true })
          return
        }
      } catch {
        /* ignore */
      }
      try {
        const cfg = await (await fetch(`${APP_BASE}/api/web/auth/config`)).json()
        setDemo(cfg.mode === "demo")
      } catch {
        /* ignore */
      }
    })()
  }, [navigate])

  React.useEffect(() => {
    const id = window.setInterval(() => {
      setSlide((prev) => (prev + 1) % SLIDES.length)
    }, 6000)
    return () => window.clearInterval(id)
  }, [])

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const res = await fetch(`${APP_BASE}/api/web/auth/login`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifier: identifier.trim(), password }),
      })
      if (!res.ok) {
        let msg = res.status === 401 ? "Неверный логин или пароль" : `Ошибка авторизации ${res.status}`
        try {
          msg = (await res.json()).detail || msg
        } catch {
          /* ignore */
        }
        setError(String(msg))
        setLoading(false)
        return
      }
      toast.success("Вход выполнен")
      navigate("/", { replace: true })
      window.location.reload()
    } catch {
      setError("Сервис авторизации недоступен. Повторите попытку.")
      setLoading(false)
    }
  }

  const current = SLIDES[slide]

  return (
    /* body закрыт overflow:hidden, поэтому прокрутку экрана входа держит он сам:
       иначе на низком окне форма уходила бы под нижнюю границу без скролла. */
    <div className="flex h-full overflow-y-auto bg-background">
      <aside className="relative hidden w-[48%] shrink-0 p-4 lg:block xl:w-[52%]">
        <div className="login-hero relative flex h-full flex-col overflow-hidden rounded-[1.75rem] bg-[#070b14] text-white">
          <div className="login-hero-grain pointer-events-none absolute inset-0 opacity-[0.35]" />
          <div className="login-hero-beam login-hero-beam-a pointer-events-none absolute inset-0" />
          <div className="login-hero-beam login-hero-beam-b pointer-events-none absolute inset-0" />
          <div className="login-hero-beam login-hero-beam-c pointer-events-none absolute inset-0" />

          <div className="relative mt-auto space-y-5 p-8 pb-10 xl:p-10 xl:pb-12">
            <div key={slide} className="login-slide-enter space-y-3">
              <h2 className="text-4xl font-semibold tracking-tight xl:text-5xl">{current.title}</h2>
              <p className="max-w-md text-sm leading-relaxed text-white/70 xl:text-base">{current.text}</p>
            </div>
            <div className="flex items-center gap-2 pt-2">
              {SLIDES.map((_, i) => (
                <button
                  key={i}
                  type="button"
                  aria-label={`Слайд ${i + 1}`}
                  onClick={() => setSlide(i)}
                  className={cn(
                    "h-1.5 rounded-full transition-all",
                    i === slide ? "w-6 bg-white" : "w-1.5 bg-white/35 hover:bg-white/55",
                  )}
                />
              ))}
            </div>
          </div>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col justify-center px-6 py-10 sm:px-10 lg:px-16 xl:px-24">
        <div className="mx-auto w-full max-w-[380px]">
          <div className="mb-10 flex justify-center">
            <img src={logoSrc} alt="Логотип" className="h-9 w-auto" />
          </div>

          <h1 className="mb-8 text-3xl font-semibold tracking-tight">Вход</h1>

          <form className="space-y-5" onSubmit={onSubmit}>
            <FormField>
              <Label htmlFor="identifier">Почта</Label>
              <Input
                id="identifier"
                autoFocus
                className="h-11"
                value={identifier}
                onChange={(e) => setIdentifier(e.target.value)}
                placeholder="Введите почту"
                autoComplete="username"
                required
              />
            </FormField>

            <FormField>
              <Label htmlFor="password">Пароль</Label>
              <div className="relative">
                <Input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  className="h-11 pr-10"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Введите пароль"
                  autoComplete="current-password"
                  required
                />
                <button
                  type="button"
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded-md p-1 text-muted-foreground hover:text-foreground"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? "Скрыть пароль" : "Показать пароль"}
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </FormField>

            {error ? (
              <div className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</div>
            ) : null}

            <div className="flex flex-col gap-3 pt-1 sm:flex-row">
              <Button type="submit" className="h-11 flex-1" disabled={loading}>
                {loading ? <Loader2 className="animate-spin" /> : null}
                Войти
              </Button>
              <Button
                type="button"
                variant="outline"
                className="h-11 flex-1"
                onClick={() => toast.message("Вход через SSO пока не подключён")}
              >
                Войти через SSO
              </Button>
            </div>
          </form>

          {demo ? (
            <p className="mt-6 text-xs text-muted-foreground">
              Демо: <b>admin@mosreg.ru / admin2026</b>, <b>operator@mosreg.ru / kontakt2026</b>
            </p>
          ) : (
            <p className="mt-6 text-xs text-muted-foreground">Авторизация через защищённый сервис учётных записей</p>
          )}
        </div>
      </main>
    </div>
  )
}
