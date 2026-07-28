import { appBaseFromPath } from "@/lib/utils"

export const APP_BASE = appBaseFromPath(window.location.pathname)
export const API_BASE = `${APP_BASE}/api/web`

/** Главная кабинета — ВСЕГДА со слэшем на конце (`…/application/`, а не
 *  `…/application`). React Router для index-маршрута отдаёт голый basename, и за
 *  reverse-proxy такой адрес — это «файл application» в каталоге `/jnserver/8/`:
 *  относительные ассеты и `<base href>` считаются от родителя, а не от кабинета.
 *  Отсюда редиректим после входа и правим адрес главной (см. App.tsx). */
export const APP_HOME = `${APP_BASE}/`

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

type ApiOptions = RequestInit & { raw?: boolean }

function loginUrl() {
  const base = APP_BASE || ""
  if (!base) return "/login"
  return `${base}/login`
}

export async function api<T = unknown>(path: string, opts: ApiOptions = {}): Promise<T> {
  const { raw, headers, ...rest } = opts
  const res = await fetch(API_BASE + path, {
    credentials: "same-origin",
    headers: {
      ...(rest.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...headers,
    },
    ...rest,
  })

  if (res.status === 401) {
    if (!window.location.pathname.endsWith("/login")) {
      window.location.assign(loginUrl())
    }
    throw new ApiError(401, "Требуется авторизация")
  }

  if (!res.ok) {
    let msg = `Ошибка ${res.status}`
    try {
      const data = await res.json()
      const detail = data.detail
      if (typeof detail === "string") msg = detail
      else if (Array.isArray(detail)) {
        msg = detail.map((x: { msg?: string }) => x.msg || String(x)).join("; ")
      }
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, msg)
  }

  if (raw) return res as unknown as T
  if (res.status === 204) return undefined as T
  const ct = res.headers.get("content-type") || ""
  if (!ct.includes("application/json")) return undefined as T
  return res.json() as Promise<T>
}

export function exportUrl(path: string) {
  return API_BASE + path
}
