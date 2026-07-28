import path from "node:path"
import { defineConfig, type Plugin } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

/**
 * Сборка идёт с base "./", потому что кабинет может отдаваться и из корня, и за
 * reverse-proxy по подпути (/jnserver/1103/application/) — абсолютный base
 * времени сборки туда не подходит. Но относительный путь ломается на вложенных
 * SPA-маршрутах (/usvo/cards/42): "./assets/index.js" резолвится в
 * /usvo/cards/assets/index.js.
 *
 * <base href> в index.html это чинит, ОДНАКО preload-сканер браузера успевает
 * отправить запросы по статическим src/href ещё до того, как inline-скрипт
 * запишет <base> — в консоли появлялись 404 на каждый deep-link.
 *
 * Поэтому здесь теги ассетов, которые вставил Vite, выносятся в inline-скрипт:
 * он пишет их через document.write уже с вычисленным префиксом. Сканеру нечего
 * предзагружать — гонки нет. Значение префикса берётся из window.__APP_BASE__,
 * который выставляет первый скрипт в index.html (там же ставится <base> для
 * остальных относительных ссылок — favicon и т. п.).
 */
function runtimeBaseAssets(): Plugin {
  return {
    name: "runtime-base-assets",
    apply: "build",
    transformIndexHtml: {
      order: "post",
      handler(html: string) {
        const scripts: string[] = []
        const styles: string[] = []

        html = html.replace(
          /\s*<script\b[^>]*\bsrc="\.\/([^"]+)"[^>]*><\/script>/g,
          (_match, src: string) => {
            scripts.push(src)
            return ""
          },
        )
        html = html.replace(
          /\s*<link\b[^>]*\brel="stylesheet"[^>]*\bhref="\.\/([^"]+)"[^>]*\/?>/g,
          (_match, href: string) => {
            styles.push(href)
            return ""
          },
        )
        if (!scripts.length && !styles.length) return html

        const boot =
          `    <script>\n` +
          `      (function () {\n` +
          `        var b = window.__APP_BASE__ || "";\n` +
          `        var css = ${JSON.stringify(styles)};\n` +
          `        var js = ${JSON.stringify(scripts)};\n` +
          `        for (var i = 0; i < css.length; i++) {\n` +
          `          document.write('<link rel="stylesheet" crossorigin href="' + b + '/' + css[i] + '">');\n` +
          `        }\n` +
          `        for (var k = 0; k < js.length; k++) {\n` +
          `          document.write('<script type="module" crossorigin src="' + b + '/' + js[k] + '"><\\/script>');\n` +
          `        }\n` +
          `      })();\n` +
          `    </script>\n`
        return html.replace("</head>", `${boot}  </head>`)
      },
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), runtimeBaseAssets()],
  base: "./",
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8080",
      "/health": "http://127.0.0.1:8080",
    },
  },
  build: {
    outDir: "../app/web/static",
    emptyOutDir: true,
    assetsDir: "assets",
  },
})
