/**
 * Автоматический переход кабинета в полноэкранный режим браузера.
 *
 * ОГРАНИЧЕНИЕ БРАУЗЕРА: requestFullscreen() разрешён только внутри обработчика
 * пользовательского жеста. Вызов при загрузке страницы браузер отклоняет
 * (`Failed to execute 'requestFullscreen': API can only be initiated by a user
 * gesture`). Поэтому запрос вешается на ПЕРВЫЙ клик/нажатие клавиши в кабинете —
 * для оператора это выглядит как автоматическое разворачивание.
 *
 * Выбор пользователя уважается: если он вышел из полноэкранного режима (Esc или
 * кнопкой браузера), мы это запоминаем и больше не разворачиваем — иначе
 * приложение отбирало бы управление окном на каждом клике.
 *
 * Визуально ничего не добавляет: разметка и стили кабинета не меняются.
 */

const OPT_OUT_KEY = "fullscreen-auto-off"

function optedOut(): boolean {
  try {
    return localStorage.getItem(OPT_OUT_KEY) === "1"
  } catch {
    return false // приватный режим без localStorage — просто не запоминаем выбор
  }
}

function rememberOptOut(off: boolean) {
  try {
    if (off) localStorage.setItem(OPT_OUT_KEY, "1")
    else localStorage.removeItem(OPT_OUT_KEY)
  } catch {
    /* ignore */
  }
}

export function isFullscreen(): boolean {
  return Boolean(document.fullscreenElement)
}

export async function enterFullscreen(): Promise<void> {
  const el = document.documentElement
  if (!el.requestFullscreen || isFullscreen()) return
  try {
    await el.requestFullscreen({ navigationUI: "hide" })
  } catch {
    /* браузер отказал (нет жеста, iframe без allow="fullscreen", политика) */
  }
}

export function installAutoFullscreen(): void {
  if (!document.documentElement.requestFullscreen) return

  // Пользователь сам вышел из полноэкранного режима — второй раз не навязываем.
  document.addEventListener("fullscreenchange", () => {
    rememberOptOut(!isFullscreen())
  })

  if (optedOut()) return

  const onFirstGesture = () => {
    window.removeEventListener("pointerdown", onFirstGesture)
    window.removeEventListener("keydown", onFirstGesture)
    if (!optedOut()) void enterFullscreen()
  }

  // capture: сработать раньше, чем клик остановят обработчики внутри приложения.
  window.addEventListener("pointerdown", onFirstGesture, { capture: true })
  window.addEventListener("keydown", onFirstGesture, { capture: true })
}
