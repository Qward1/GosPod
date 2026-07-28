import * as React from "react"
import { APP_BASE } from "@/lib/api"
import { medalSvgMarkup, resolveMedals, type MedalEntry } from "@/lib/medals"
import { cn } from "@/lib/utils"

function MedalVisual({ entry, index }: { entry: MedalEntry; index: number }) {
  const [usePhoto, setUsePhoto] = React.useState(Boolean(entry.img))
  const uid = React.useId().replace(/:/g, "") + index

  if (usePhoto && entry.img) {
    const base = APP_BASE || ""
    return (
      <img
        className="h-full w-full object-contain"
        src={`${base}/medals/${entry.img}`}
        alt={entry.name}
        loading="lazy"
        decoding="async"
        onError={() => setUsePhoto(false)}
      />
    )
  }

  return (
    <span
      className="block h-full w-full"
      dangerouslySetInnerHTML={{ __html: medalSvgMarkup(entry.cfg, `m${uid}`) }}
    />
  )
}

export function AwardsBlock({ awards, className }: { awards: unknown; className?: string }) {
  const list = React.useMemo(() => resolveMedals(awards), [awards])
  if (!list.length) return null

  return (
    <div className={cn(className)}>
      <div className="mb-3 text-sm font-medium leading-none">Награды</div>
      <div className="flex max-w-full flex-wrap gap-3.5">
        {list.map((m, i) => (
          <div key={`${m.name}-${i}`} className="w-[72px] max-w-full text-center">
            <div className="mx-auto h-[70px] w-14 overflow-hidden">
              <MedalVisual entry={m} index={i} />
            </div>
            <div className="mt-1.5 break-words text-[11px] font-semibold leading-tight text-muted-foreground">
              {m.name}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
