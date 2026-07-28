import * as React from "react"
import { Download, GripVertical, Settings2, Sparkles } from "lucide-react"
import { useTheme } from "next-themes"
import { toast } from "sonner"
import { api, exportUrl } from "@/lib/api"
import type { AnalyticsData } from "@/lib/types"
import { PageActions } from "@/components/page-actions"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Switch } from "@/components/ui/switch"
import { cn } from "@/lib/utils"

const AI_INSIGHT_COLLAPSE_AT = 180
const DASHBOARD_LAYOUT_KEY = "analytics-dashboard-layout"

const DASHBOARD_BLOCKS = [
  { id: "metrics", label: "Ключевые показатели" },
  { id: "dynamics", label: "Динамика обращений" },
  { id: "topics", label: "Тематики и меры поддержки" },
  { id: "orgs", label: "Охват организациями ветеранов" },
  { id: "heatmap", label: "Тепловая карта обращений" },
] as const

type BlockId = (typeof DASHBOARD_BLOCKS)[number]["id"]

type DashboardLayout = {
  order: BlockId[]
  hidden: BlockId[]
}

function defaultLayout(): DashboardLayout {
  return {
    order: DASHBOARD_BLOCKS.map((b) => b.id),
    hidden: [],
  }
}

function loadLayout(): DashboardLayout {
  try {
    const raw = localStorage.getItem(DASHBOARD_LAYOUT_KEY)
    if (!raw) return defaultLayout()
    const parsed = JSON.parse(raw) as Partial<DashboardLayout>
    const known = new Set(DASHBOARD_BLOCKS.map((b) => b.id))
    const order = (parsed.order || []).filter((id): id is BlockId => known.has(id as BlockId))
    for (const b of DASHBOARD_BLOCKS) {
      if (!order.includes(b.id)) order.push(b.id)
    }
    const hidden = (parsed.hidden || []).filter((id): id is BlockId => known.has(id as BlockId))
    return { order, hidden }
  } catch {
    return defaultLayout()
  }
}

function saveLayout(layout: DashboardLayout) {
  localStorage.setItem(DASHBOARD_LAYOUT_KEY, JSON.stringify(layout))
}

function reorderBlocks(order: BlockId[], fromId: BlockId, toId: BlockId): BlockId[] {
  if (fromId === toId) return order
  const next = [...order]
  const from = next.indexOf(fromId)
  const to = next.indexOf(toId)
  if (from < 0 || to < 0) return order
  next.splice(from, 1)
  next.splice(to, 0, fromId)
  return next
}

type SeriesItem = NonNullable<AnalyticsData["series"]>[number]
type SeriesPoint = SeriesItem["points"][number]

function AiInsightNote({
  text,
  expanded,
  onExpandedChange,
}: {
  text: string
  expanded: boolean
  onExpandedChange: (expanded: boolean) => void
}) {
  const collapsible = text.length > AI_INSIGHT_COLLAPSE_AT

  return (
    <div
      className={cn(
        "flex min-h-0 flex-col rounded-lg border border-primary/30 bg-primary/10 p-3 text-sm",
        expanded ? "shrink-0" : "flex-1 overflow-hidden",
      )}
    >
      <Badge variant="outline" className="mb-2 w-fit shrink-0">
        Заметка ИИ
      </Badge>
      <p
        className={cn(
          "min-h-0 leading-relaxed text-foreground/90",
          expanded ? "shrink-0" : "flex-1 overflow-hidden",
        )}
      >
        {text}
      </p>
      {collapsible ? (
        <Button
          type="button"
          variant="link"
          className="mt-2 h-auto shrink-0 self-start p-0 text-xs"
          onClick={() => onExpandedChange(!expanded)}
        >
          {expanded ? "Свернуть" : "Развернуть"}
        </Button>
      ) : null}
    </div>
  )
}

function MetricCard({
  value,
  label,
  className,
}: {
  value: React.ReactNode
  label: string
  className?: string
}) {
  return (
    <Card className={cn("h-full", className)}>
      <CardContent className="flex h-full flex-col pt-6">
        <div className="text-3xl font-bold tracking-tight text-primary">{value}</div>
        <div className="mt-1 flex-1 text-sm text-muted-foreground">{label}</div>
      </CardContent>
    </Card>
  )
}

function BarChart({ items, title }: { items: { label: string; count: number }[]; title: string }) {
  const max = Math.max(1, ...items.map((t) => t.count))
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">Нет данных</p>
        ) : (
          items.map((t) => (
            <div key={t.label} className="grid grid-cols-[minmax(0,1fr)_1fr_auto] items-center gap-3 text-sm">
              <span className="truncate" title={t.label}>
                {t.label}
              </span>
              <div className="h-2 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary transition-all"
                  style={{ width: `${Math.round((t.count / max) * 100)}%` }}
                />
              </div>
              <span className="w-8 text-right tabular-nums">{t.count}</span>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  )
}

function niceMax(value: number): number {
  if (value <= 0) return 80
  const step = value <= 100 ? 20 : value <= 400 ? 50 : 100
  return Math.ceil(value / step) * step
}

function buildSmoothPath(coords: { x: number; y: number }[]): string {
  if (coords.length === 0) return ""
  if (coords.length === 1) return `M ${coords[0].x} ${coords[0].y}`
  let d = `M ${coords[0].x} ${coords[0].y}`
  for (let i = 0; i < coords.length - 1; i++) {
    const p0 = coords[i === 0 ? i : i - 1]
    const p1 = coords[i]
    const p2 = coords[i + 1]
    const p3 = coords[i + 2] ?? p2
    const cp1x = p1.x + (p2.x - p0.x) / 6
    const cp1y = p1.y + (p2.y - p0.y) / 6
    const cp2x = p2.x - (p3.x - p1.x) / 6
    const cp2y = p2.y - (p3.y - p1.y) / 6
    d += ` C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${p2.x} ${p2.y}`
  }
  return d
}

function DynamicsAreaChart({
  points,
  unit,
  dark,
}: {
  points: SeriesPoint[]
  unit?: string
  dark: boolean
}) {
  const gradientId = React.useId().replace(/:/g, "")
  const containerRef = React.useRef<HTMLDivElement>(null)
  const [hover, setHover] = React.useState<number | null>(null)
  const [width, setWidth] = React.useState(0)
  const height = 260
  const pad = { top: 28, right: 12, bottom: 32, left: 40 }

  React.useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const update = () => setWidth(Math.max(280, Math.floor(el.clientWidth)))
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const plotW = Math.max(1, width - pad.left - pad.right)
  const plotH = height - pad.top - pad.bottom
  const maxY = niceMax(Math.max(...points.map((p) => p.count), 0))
  const ticks = 4
  const coords = width
    ? points.map((p, i) => ({
        x: pad.left + (points.length <= 1 ? plotW / 2 : (i / (points.length - 1)) * plotW),
        y: pad.top + plotH - (p.count / maxY) * plotH,
        label: p.date,
        count: p.count,
      }))
    : []
  const linePath = buildSmoothPath(coords)
  const areaPath =
    coords.length === 0
      ? ""
      : `${linePath} L ${coords[coords.length - 1].x} ${pad.top + plotH} L ${coords[0].x} ${pad.top + plotH} Z`
  const xLabels = coords.filter((_, i) => {
    if (coords.length <= 8) return true
    if (coords.length >= 20) return i % 3 === 0 || i === coords.length - 1
    return i % 2 === 0 || i === coords.length - 1
  })
  const axis = dark ? "#94A3B8" : "#64748B"
  const grid = dark ? "rgba(148,163,184,0.25)" : "rgba(100,116,139,0.2)"
  const line = "#3B82F6"
  const active = hover != null ? coords[hover] : null

  const nearestIndex = (clientX: number, rect: DOMRect) => {
    if (!coords.length || !width) return null
    const x = ((clientX - rect.left) / rect.width) * width
    let best = 0
    let bestDist = Math.abs(coords[0].x - x)
    for (let i = 1; i < coords.length; i++) {
      const d = Math.abs(coords[i].x - x)
      if (d < bestDist) {
        best = i
        bestDist = d
      }
    }
    return best
  }

  return (
    <div
      ref={containerRef}
      className="relative w-full"
      onMouseLeave={() => setHover(null)}
      onMouseMove={(e) => {
        const rect = e.currentTarget.getBoundingClientRect()
        setHover(nearestIndex(e.clientX, rect))
      }}
    >
      {width > 0 ? (
        <svg
          viewBox={`0 0 ${width} ${height}`}
          width={width}
          height={height}
          className="block h-[260px] w-full max-w-full"
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label="Динамика обращений"
        >
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={line} stopOpacity={dark ? 0.45 : 0.28} />
              <stop offset="100%" stopColor={line} stopOpacity="0" />
            </linearGradient>
          </defs>
          {Array.from({ length: ticks + 1 }, (_, i) => {
            const value = Math.round((maxY / ticks) * (ticks - i))
            const y = pad.top + (plotH / ticks) * i
            return (
              <g key={value}>
                <line x1={pad.left} x2={width - pad.right} y1={y} y2={y} stroke={grid} strokeWidth={1} />
                <text x={pad.left - 8} y={y + 4} textAnchor="end" fill={axis} fontSize="12">
                  {value}
                </text>
              </g>
            )
          })}
          {areaPath ? <path d={areaPath} fill={`url(#${gradientId})`} /> : null}
          {linePath ? (
            <path
              d={linePath}
              fill="none"
              stroke={line}
              strokeWidth={2.5}
              strokeLinecap="round"
              strokeLinejoin="round"
              vectorEffect="non-scaling-stroke"
            />
          ) : null}
          {active ? (
            <line
              x1={active.x}
              x2={active.x}
              y1={pad.top}
              y2={pad.top + plotH}
              stroke={line}
              strokeWidth={1.5}
              strokeDasharray="5 5"
              opacity={0.85}
              vectorEffect="non-scaling-stroke"
            />
          ) : null}
          {coords.map((p, i) => {
            const isActive = hover === i
            return (
              <g key={`${p.label}-${i}`}>
                {isActive ? (
                  <circle cx={p.x} cy={p.y} r={14} fill="rgba(244, 63, 94, 0.28)" />
                ) : null}
                <circle
                  cx={p.x}
                  cy={p.y}
                  r={isActive ? 5.5 : 4}
                  fill={dark ? "#93C5FD" : "#60A5FA"}
                  stroke={dark ? "#0B1426" : "#FFFFFF"}
                  strokeWidth={2}
                />
              </g>
            )
          })}
          {xLabels.map((p) => (
            <text
              key={`x-${p.label}-${p.x}`}
              x={p.x}
              y={height - 8}
              textAnchor="middle"
              fill={axis}
              fontSize="12"
            >
              {p.label}
            </text>
          ))}
        </svg>
      ) : (
        <div className="h-[260px] w-full" />
      )}
      {active && width > 0 ? (
        <div
          className={cn(
            "pointer-events-none absolute z-10 min-w-[108px] -translate-x-1/2 -translate-y-[calc(100%+14px)] rounded-xl border px-3 py-2 shadow-lg",
            dark
              ? "border-slate-600 bg-[#151F33] text-white"
              : "border-border bg-white text-foreground",
          )}
          style={{
            left: `${(active.x / width) * 100}%`,
            top: `${(active.y / height) * 100}%`,
          }}
        >
          <div className={cn("text-right text-xs", dark ? "text-slate-400" : "text-muted-foreground")}>
            {active.label}
          </div>
          <div className="mt-0.5 text-right text-sm font-semibold tabular-nums">
            {active.count}
            {unit ? (
              <span className={cn("ml-1 text-xs font-normal", dark ? "text-slate-400" : "text-muted-foreground")}>
                {unit}
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  )
}

function formatIsoDate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, "0")
  const day = String(d.getDate()).padStart(2, "0")
  return `${y}-${m}-${day}`
}

function daysInMonth(year: number, monthIndex: number): number {
  return new Date(year, monthIndex + 1, 0).getDate()
}

function addDaysIso(iso: string, delta: number): string {
  const d = new Date(`${iso}T12:00:00`)
  d.setDate(d.getDate() + delta)
  return formatIsoDate(d)
}

function DynamicsChart({ series: initialSeries }: { series: SeriesItem[] }) {
  const { resolvedTheme } = useTheme()
  const [mounted, setMounted] = React.useState(false)
  const [active, setActive] = React.useState("day")
  const [range, setRange] = React.useState<1 | 7 | 30>(7)
  const todayIso = React.useMemo(() => formatIsoDate(new Date()), [])
  const [dayValue, setDayValue] = React.useState(todayIso)
  const [weekStart, setWeekStart] = React.useState(() => addDaysIso(todayIso, -6))
  const [weekEnd, setWeekEnd] = React.useState(todayIso)
  const [monthValue, setMonthValue] = React.useState(todayIso.slice(0, 7))
  const [series, setSeries] = React.useState(initialSeries)
  const [loadingSeries, setLoadingSeries] = React.useState(false)
  const dark = mounted && resolvedTheme === "dark"

  React.useEffect(() => setMounted(true), [])
  React.useEffect(() => setSeries(initialSeries), [initialSeries])

  const setWeekFromStart = React.useCallback(
    (start: string) => {
      const nextStart = start || addDaysIso(todayIso, -6)
      let end = addDaysIso(nextStart, 6)
      if (end > todayIso) {
        end = todayIso
        setWeekStart(addDaysIso(end, -6))
        setWeekEnd(end)
        return
      }
      setWeekStart(nextStart)
      setWeekEnd(end)
    },
    [todayIso],
  )

  const setWeekFromEnd = React.useCallback(
    (end: string) => {
      const nextEnd = end || todayIso
      setWeekEnd(nextEnd)
      setWeekStart(addDaysIso(nextEnd, -6))
    },
    [todayIso],
  )

  const query = React.useMemo(() => {
    if (range === 1) {
      return { days: 1, end: dayValue }
    }
    if (range === 7) {
      return { days: 7, end: weekEnd }
    }
    const [ys, ms] = monthValue.split("-").map(Number)
    const last = daysInMonth(ys, ms - 1)
    const end = `${monthValue}-${String(last).padStart(2, "0")}`
    return { days: last, end }
  }, [range, dayValue, weekEnd, monthValue])

  React.useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoadingSeries(true)
      try {
        const d = await api<AnalyticsData>(`/analytics?days=${query.days}&end=${query.end}`)
        if (!cancelled && d.series?.length) setSeries(d.series)
      } catch {
        /* keep previous series */
      } finally {
        if (!cancelled) setLoadingSeries(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [query.days, query.end])

  const tabs = React.useMemo(() => {
    const byKey = Object.fromEntries(series.map((s) => [s.key, s]))
    return [
      { key: "day", label: "Динамика обращений", series: byKey.day },
      { key: "in_person", label: "Очных обращений в администрацию", series: byKey.in_person },
      { key: "applications", label: "Заявления на меры поддержки", series: byKey.applications },
    ].filter((t) => t.series)
  }, [series])

  const currentTab = tabs.find((t) => t.key === active) ?? tabs[0]
  const current = currentTab?.series
  const points = React.useMemo(() => {
    if (!current) return []
    if (range === 1 && current.hourly?.length) return current.hourly
    return current.points ?? []
  }, [current, range])
  const unit = range === 1 ? current?.unit_hourly || current?.unit : current?.unit

  if (!current || tabs.length === 0) return null

  const rangeOptions: { value: 1 | 7 | 30; label: string }[] = [
    { value: 1, label: "1 день" },
    { value: 7, label: "7 дней" },
    { value: 30, label: "30 дней" },
  ]

  const pickerClass = cn(
    "h-8 w-auto min-w-[9.5rem]",
    dark && "border-slate-600 bg-transparent text-white",
  )

  return (
    <Card
      className={cn(
        "overflow-hidden shadow-none",
        dark ? "border-slate-800 bg-[#0B1426] text-white" : "border bg-card text-foreground",
      )}
    >
      <CardHeader className="space-y-3 px-6 pb-2">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Select value={currentTab.key} onValueChange={setActive}>
            <SelectTrigger
              className={cn(
                "h-9 w-auto min-w-[220px] max-w-full gap-2",
                dark && "border-slate-600 bg-transparent text-white [&>svg]:text-slate-300",
              )}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {tabs.map((tab) => (
                <SelectItem key={tab.key} value={tab.key}>
                  {tab.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <div className="flex flex-wrap items-center gap-2">
            {range === 1 ? (
              <Input
                type="date"
                value={dayValue}
                max={todayIso}
                onChange={(e) => setDayValue(e.target.value || todayIso)}
                className={pickerClass}
                aria-label="Выбрать день"
              />
            ) : null}
            {range === 7 ? (
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  type="date"
                  value={weekStart}
                  max={addDaysIso(todayIso, -6)}
                  onChange={(e) => setWeekFromStart(e.target.value)}
                  className={pickerClass}
                  aria-label="Начало 7-дневного периода"
                />
                <span className={cn("text-xs", dark ? "text-slate-400" : "text-muted-foreground")}>
                  —
                </span>
                <Input
                  type="date"
                  value={weekEnd}
                  max={todayIso}
                  min={weekStart}
                  onChange={(e) => setWeekFromEnd(e.target.value)}
                  className={pickerClass}
                  aria-label="Конец 7-дневного периода"
                />
              </div>
            ) : null}
            {range === 30 ? (
              <Input
                type="month"
                value={monthValue}
                max={todayIso.slice(0, 7)}
                onChange={(e) => setMonthValue(e.target.value || todayIso.slice(0, 7))}
                className={pickerClass}
                aria-label="Выбрать месяц"
              />
            ) : null}
            <div
              className={cn(
                "inline-flex rounded-lg border p-0.5",
                dark ? "border-slate-600" : "border-border",
              )}
            >
              {rangeOptions.map((opt) => {
                const selected = range === opt.value
                return (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setRange(opt.value)}
                    className={cn(
                      "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                      selected
                        ? "bg-primary text-primary-foreground"
                        : dark
                          ? "text-slate-300 hover:bg-white/5"
                          : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                    )}
                  >
                    {opt.label}
                  </button>
                )
              })}
            </div>
          </div>
        </div>
      </CardHeader>
      <CardContent className={cn("px-2 pb-4 pt-2 sm:px-3", loadingSeries && "opacity-70")}>
        <DynamicsAreaChart
          key={`${current.key}-${range}-${query.end}-${query.days}`}
          points={points}
          unit={unit}
          dark={dark}
        />
      </CardContent>
    </Card>
  )
}

function HeatmapSection({ heatmap }: { heatmap: NonNullable<AnalyticsData["heatmap"]> }) {
  const mapRef = React.useRef<HTMLDivElement>(null)
  const mapInstance = React.useRef<{ remove: () => void } | null>(null)
  const [insightExpanded, setInsightExpanded] = React.useState(false)

  React.useEffect(() => {
    let cancelled = false
    ;(async () => {
      if (!mapRef.current || !heatmap.hotspots?.length) return
      try {
        const L = await import("leaflet")
        await import("leaflet/dist/leaflet.css")
        if (cancelled || !mapRef.current) return

        if (mapInstance.current) {
          mapInstance.current.remove()
          mapInstance.current = null
        }

        const c = heatmap.center || { lat: 55.556, lng: 37.718, zoom: 12 }
        const map = L.map(mapRef.current, { scrollWheelZoom: true, attributionControl: false }).setView(
          [c.lat, c.lng],
          c.zoom || 12,
        )
        mapInstance.current = map

        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18 }).addTo(map)

        const bounds: [number, number][] = []

        heatmap.hotspots.forEach((p) => {
          bounds.push([p.lat, p.lng])
          const hue = 18 + Math.round((1 - p.intensity) * 36)
          L.circleMarker([p.lat, p.lng], {
            radius: 6 + Math.round(p.intensity * 14),
            color: `hsl(${hue},90%,45%)`,
            fillColor: `hsl(${hue},92%,52%)`,
            fillOpacity: 0.55,
            weight: 2,
          })
            .addTo(map)
            .bindPopup(`<b>${p.name}</b><br/>Обращений: <b>${p.count}</b>`)
        })

        if (bounds.length) map.fitBounds(bounds, { padding: [40, 40] })
        setTimeout(() => map.invalidateSize(), 120)
      } catch {
        /* leaflet load failed */
      }
    })()

    return () => {
      cancelled = true
      if (mapInstance.current) {
        mapInstance.current.remove()
        mapInstance.current = null
      }
    }
  }, [heatmap])

  const top = [...heatmap.hotspots].sort((a, b) => b.count - a.count).slice(0, 5)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          Тепловая карта обращений · {heatmap.area}
          <Badge variant="secondary" className="ml-auto">
            <Sparkles className="mr-1 h-3 w-3" />
            анализ ИИ
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid gap-4 lg:grid-cols-[1fr_280px] lg:items-start">
          <div>
            <div ref={mapRef} className="h-[320px] w-full rounded-lg border bg-muted/30" />
            <p className="mt-2 text-xs text-muted-foreground">
              Очаги: {heatmap.hotspots.length} · макс. {Math.max(...heatmap.hotspots.map((p) => p.count))} обращений
            </p>
          </div>
          <div
            className={cn(
              "flex flex-col gap-3",
              insightExpanded ? "min-h-[320px]" : "h-[320px] overflow-hidden",
            )}
          >
            <div className="shrink-0 space-y-1 text-sm">
              {top.map((p) => (
                <div key={p.name} className="flex justify-between gap-2">
                  <span className="truncate">{p.name}</span>
                  <b>{p.count}</b>
                </div>
              ))}
            </div>
            {heatmap.ai_insight ? (
              <AiInsightNote
                text={heatmap.ai_insight}
                expanded={insightExpanded}
                onExpandedChange={setInsightExpanded}
              />
            ) : null}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

export function AnalyticsPage() {
  const [data, setData] = React.useState<AnalyticsData | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [layout, setLayout] = React.useState<DashboardLayout>(defaultLayout)
  const [settingsOpen, setSettingsOpen] = React.useState(false)
  const [draft, setDraft] = React.useState<DashboardLayout>(defaultLayout)
  const dragId = React.useRef<BlockId | null>(null)

  React.useEffect(() => {
    setLayout(loadLayout())
  }, [])

  React.useEffect(() => {
    ;(async () => {
      try {
        const d = await api<AnalyticsData>("/analytics")
        setData(d)
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Ошибка загрузки")
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  const openSettings = () => {
    setDraft(layout)
    setSettingsOpen(true)
  }

  const applySettings = () => {
    setLayout(draft)
    saveLayout(draft)
    setSettingsOpen(false)
    toast.success("Макет дашборда сохранён")
  }

  const resetSettings = () => {
    const next = defaultLayout()
    setDraft(next)
  }

  if (loading) {
    return (
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 9 }).map((_, i) => (
          <Skeleton key={i} className="h-24 w-full" />
        ))}
      </div>
    )
  }

  if (!data) {
    return <p className="text-muted-foreground">Не удалось загрузить аналитику</p>
  }

  const blocks: Record<BlockId, React.ReactNode> = {
    metrics: (
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <MetricCard value={data.appeals.day} label="Обращений за день" />
        <MetricCard value={data.appeals.week} label="За неделю" />
        <MetricCard value={data.appeals.month} label="За месяц" />
        <MetricCard value={data.in_person} label="Очных обращений в администрацию" />
        <MetricCard value={data.applications ?? 0} label="Заявлений на меры поддержки" />
        <MetricCard value={data.total_usvo} label="Всего УСВО на учёте" />
        <MetricCard value={data.unemployed} label="Нуждаются в трудоустройстве" />
        <MetricCard
          value={data.stale_contacts}
          label={`Без контакта > ${Math.round(data.stale_days / 30)} мес.`}
        />
        <MetricCard value={`${data.orgs.coverage_pct}%`} label="Охват ветеранскими организациями" />
      </div>
    ),
    dynamics: data.series?.length ? <DynamicsChart series={data.series} /> : null,
    topics: (
      <div className="grid gap-4 lg:grid-cols-2">
        <BarChart items={data.topics} title="Тематики обращений" />
        <BarChart items={data.support_measures} title="Наиболее востребованные меры поддержки" />
      </div>
    ),
    orgs: (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Охват участием в организациях ветеранов</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              { label: "Охват", value: `${data.orgs.coverage_pct}%` },
              { label: "«Время Героя»", value: data.orgs.vremya_geroev },
              { label: "«Герои Подмосковья»", value: data.orgs.geroi_podmoskovya },
              { label: "Ассоциация ветеранов", value: data.orgs.associaciya },
            ].map((item) => (
              <div key={item.label} className="rounded-lg border bg-muted/30 px-4 py-3">
                <div className="text-2xl font-bold tabular-nums text-primary">{item.value}</div>
                <div className="mt-1 text-sm text-muted-foreground">{item.label}</div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    ),
    heatmap: data.heatmap?.hotspots?.length ? <HeatmapSection heatmap={data.heatmap} /> : null,
  }

  const visibleBlocks = layout.order.filter(
    (id) => !layout.hidden.includes(id) && blocks[id] != null,
  )

  return (
    <>
      <PageActions>
        <h2 className="min-w-0 flex-1 text-xl font-semibold tracking-tight">Общая сводка</h2>
        <Button variant="outline" size="sm" asChild>
          <a href={exportUrl("/export/analytics")} download>
            Экспорт
            <Download className="h-4 w-4" />
          </a>
        </Button>
        <Button variant="outline" size="sm" type="button" onClick={openSettings}>
          Настройка
          <Settings2 className="h-4 w-4" />
        </Button>
      </PageActions>

      <div className="space-y-6">
        {visibleBlocks.map((id) => (
          <div key={id}>{blocks[id]}</div>
        ))}
        {visibleBlocks.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Все блоки скрыты. Откройте «Настройка», чтобы показать нужные разделы.
          </p>
        ) : null}
      </div>

      <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Настройка дашборда</DialogTitle>
            <DialogDescription>
              Перетащите блоки, чтобы изменить порядок. Выключите переключатель, чтобы скрыть блок.
            </DialogDescription>
          </DialogHeader>
          <ul className="space-y-2">
            {draft.order.map((id) => {
              const meta = DASHBOARD_BLOCKS.find((b) => b.id === id)
              if (!meta) return null
              const visible = !draft.hidden.includes(id)
              const unavailable =
                (id === "dynamics" && !(data.series?.length)) ||
                (id === "heatmap" && !(data.heatmap?.hotspots?.length))
              return (
                <li
                  key={id}
                  draggable
                  onDragStart={() => {
                    dragId.current = id
                  }}
                  onDragOver={(e) => {
                    e.preventDefault()
                    const from = dragId.current
                    if (!from || from === id) return
                    setDraft((prev) => ({
                      ...prev,
                      order: reorderBlocks(prev.order, from, id),
                    }))
                  }}
                  onDragEnd={() => {
                    dragId.current = null
                  }}
                  className={cn(
                    "flex cursor-grab items-center gap-3 rounded-lg border bg-card px-3 py-2.5 active:cursor-grabbing",
                    !visible && "opacity-60",
                  )}
                >
                  <GripVertical className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{meta.label}</div>
                    {unavailable ? (
                      <div className="text-xs text-muted-foreground">Нет данных в текущей выгрузке</div>
                    ) : null}
                  </div>
                  <Switch
                    checked={visible}
                    onCheckedChange={(checked) => {
                      setDraft((prev) => ({
                        ...prev,
                        hidden: checked
                          ? prev.hidden.filter((x) => x !== id)
                          : [...prev.hidden.filter((x) => x !== id), id],
                      }))
                    }}
                    aria-label={`Показать ${meta.label}`}
                  />
                </li>
              )
            })}
          </ul>
          <DialogFooter className="gap-2 sm:justify-between">
            <Button type="button" variant="ghost" onClick={resetSettings}>
              Сбросить
            </Button>
            <div className="flex gap-2">
              <Button type="button" variant="outline" onClick={() => setSettingsOpen(false)}>
                Отмена
              </Button>
              <Button type="button" onClick={applySettings}>
                Сохранить
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
