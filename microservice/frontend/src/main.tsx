import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { Toaster } from "sonner"
import { ThemeProvider } from "@/components/theme-provider"
import { installAutoFullscreen } from "@/lib/fullscreen"
import App from "./App"
import "./index.css"

// Кабинет разворачивается на весь экран по первому жесту пользователя —
// раньше браузер запрос отклоняет (см. lib/fullscreen.ts). Разметку не меняет.
installAutoFullscreen()

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <App />
      <Toaster richColors closeButton position="top-center" />
    </ThemeProvider>
  </StrictMode>,
)
