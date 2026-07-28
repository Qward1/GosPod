import * as React from "react"
import { createPortal } from "react-dom"

/** Рендерит кнопки в шапку AppShell (#page-actions). */
export function PageActions({ children }: { children: React.ReactNode }) {
  const [el, setEl] = React.useState<HTMLElement | null>(null)
  React.useEffect(() => {
    setEl(document.getElementById("page-actions"))
  }, [])
  if (!el) return null
  return createPortal(children, el)
}
