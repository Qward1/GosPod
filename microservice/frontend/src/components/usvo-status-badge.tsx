import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

const STATUS_STYLES: Record<string, string> = {
  контрактник: "border-primary/50 bg-primary/10 text-primary",
  мобилизованный: "border-slate-500/40 bg-slate-500/10 text-slate-700 dark:text-slate-300",
  доброволец: "border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  "ветеран боевых действий": "border-violet-500/50 bg-violet-500/10 text-violet-700 dark:text-violet-400",
}

type UsvoStatusBadgeProps = {
  status?: string | null
  className?: string
}

export function UsvoStatusBadge({ status, className }: UsvoStatusBadgeProps) {
  const label = (status || "").trim()
  if (!label || label === "—") {
    return <span className={cn("text-muted-foreground", className)}>—</span>
  }
  const style = STATUS_STYLES[label.toLowerCase()]
  return (
    <Badge variant="outline" className={cn("whitespace-nowrap font-normal", style, className)}>
      {label}
    </Badge>
  )
}
