import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Client routes under the React SPA (not the reverse-proxy mount prefix). */
const SPA_ROUTE_RE =
  /\/(login|cards|ai-chat|applications|analytics|broadcast|audit|settings)(\/.*)?$/

/**
 * Mount prefix for API/assets when the app is behind a reverse proxy
 * (e.g. `/application`). Strips SPA routes so `/cards` → `` and
 * `/application/login` → `/application`.
 */
export function appBaseFromPath(pathname: string) {
  let base = pathname.replace(/\/+$/, "")
  base = base.replace(/\/(index|login)\.html$/, "")
  base = base.replace(/\/usvo\/cards\/\d+$/, "")
  base = base.replace(SPA_ROUTE_RE, "")
  base = base.replace(/(\/application)+$/, "/application")
  return base
}

export function initials(name?: string | null) {
  const parts = (name || "").split(/\s+/).filter(Boolean)
  return ((parts[0] || "")[0] || "") + ((parts[1] || "")[0] || "") || "—"
}

export function yearsWord(n: number) {
  const a = Math.abs(n) % 100
  const b = n % 10
  if (a > 10 && a < 20) return "лет"
  if (b === 1) return "год"
  if (b >= 2 && b <= 4) return "года"
  return "лет"
}

export function ageFrom(birth?: string | null) {
  const m = /(\d{1,2})\.(\d{1,2})\.(\d{4})/.exec(birth || "")
  if (!m) return null
  const b = new Date(+m[3], +m[2] - 1, +m[1])
  if (Number.isNaN(b.getTime())) return null
  const now = new Date()
  let a = now.getFullYear() - b.getFullYear()
  const md = now.getMonth() - b.getMonth()
  if (md < 0 || (md === 0 && now.getDate() < b.getDate())) a--
  return a >= 0 && a < 120 ? a : null
}

export function plural(n: number, one: string, few: string, many: string) {
  const a = Math.abs(n) % 100
  const b = n % 10
  if (a > 10 && a < 20) return many
  if (b === 1) return one
  if (b >= 2 && b <= 4) return few
  return many
}
