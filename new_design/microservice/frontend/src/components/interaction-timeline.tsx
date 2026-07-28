import {
  Briefcase,
  Building2,
  FileText,
  HeartPulse,
  Info,
  MessageSquare,
  Phone,
  Shield,
  type LucideIcon,
} from "lucide-react"
import type { HistoryEvent } from "@/lib/types"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"

const KIND_ICONS: Record<string, LucideIcon> = {
  status: Shield,
  contact: Phone,
  doc: FileText,
  appeal: MessageSquare,
  med: HeartPulse,
  health: HeartPulse,
  work: Briefcase,
  info: Info,
  visit: Building2,
}

function kindIcon(kind?: string): LucideIcon {
  if (!kind) return Info
  return KIND_ICONS[kind] || Info
}

function statusTone(status?: string): "done" | "planned" | "progress" {
  const s = (status || "").toLowerCase()
  if (s.includes("выполн")) return "done"
  if (s.includes("заплан")) return "planned"
  return "progress"
}

function styleTone(e: HistoryEvent): "done" | "planned" | "progress" | "danger" | "warn" {
  const style = (e.style || "").toLowerCase()
  if (style === "ok") return "done"
  if (style === "planned") return "planned"
  if (style === "danger") return "danger"
  if (style === "warn") return "warn"
  return statusTone(e.status)
}

export function InteractionTimeline({ events }: { events: HistoryEvent[] }) {
  if (!events.length) {
    return <p className="text-sm text-muted-foreground">История пока пуста</p>
  }

  return (
    <div className="relative space-y-0 pl-1">
      {events.map((e, i) => {
        const Icon = kindIcon(e.kind)
        const tone = styleTone(e)
        const status = statusTone(e.status)
        const detail = e.detail || e.text
        const isLast = i === events.length - 1

        return (
          <div key={`${e.title}-${e.date}-${i}`} className="relative flex gap-3 pb-4 last:pb-0">
            {!isLast ? (
              <span className="absolute left-[13px] top-7 bottom-0 w-0.5 bg-border" aria-hidden />
            ) : null}
            <div
              className={cn(
                "relative z-[1] flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-white shadow-sm",
                tone === "done" && "bg-emerald-500",
                tone === "planned" && "bg-amber-500",
                tone === "progress" && "bg-primary",
                tone === "warn" && "bg-amber-400",
                tone === "danger" && "bg-destructive",
              )}
            >
              <Icon className="h-3.5 w-3.5" strokeWidth={2.25} />
            </div>
            <div className="min-w-0 flex-1 rounded-xl border bg-card p-3 transition-shadow hover:shadow-sm">
              <div className="flex flex-wrap items-center gap-2">
                <h4 className="text-sm font-bold leading-snug">{e.title || "Событие"}</h4>
                {e.status ? (
                  <Badge
                    className={cn(
                      "uppercase tracking-wide",
                      status === "done" &&
                        "border-transparent bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
                      status === "planned" &&
                        "border-transparent bg-amber-500/15 text-amber-700 dark:text-amber-300",
                      status === "progress" &&
                        "border-transparent bg-primary/15 text-primary",
                    )}
                  >
                    {e.status}
                  </Badge>
                ) : null}
              </div>
              {detail ? (
                <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{detail}</p>
              ) : null}
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                {e.date ? <span className="font-bold text-foreground">{e.date}</span> : null}
                {e.org ? (
                  <span className="inline-flex items-center gap-1">
                    <Building2 className="h-3.5 w-3.5 text-primary" />
                    {e.org}
                  </span>
                ) : null}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
