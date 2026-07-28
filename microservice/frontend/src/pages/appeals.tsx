import * as React from "react"
import { AlertTriangle, ArrowDown, ArrowUp, ArrowUpRight, CheckCircle2, ChevronDown, ChevronRight, Clock, Download, Filter, Loader2, RefreshCw, Search, Sparkles, Trash2, Wand2, IdCard, X } from "lucide-react"
import { toast } from "sonner"
import { api, exportUrl } from "@/lib/api"
import type { Appeal, AppealHistory } from "@/lib/types"
import { useApp } from "@/hooks/use-app"
import { PageActions } from "@/components/page-actions"
import { EmptyState } from "@/components/empty-state"
import { UsvoCardSheet } from "@/components/usvo-card-sheet"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { Label } from "@/components/ui/label"
import { FormField } from "@/components/ui/form-field"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"

type AppealFilters = {
  tone: string
  dateFrom: string
  dateTo: string
  topic: string
  assignee: string
  status: string
  deadline: string
}

const EMPTY_FILTERS: AppealFilters = {
  tone: "",
  dateFrom: "",
  dateTo: "",
  topic: "",
  assignee: "",
  status: "",
  deadline: "",
}

function normalized(value: unknown) {
  return String(value ?? "").trim().toLocaleLowerCase("ru")
}

function appealDate(value?: string) {
  if (!value) return null
  const numeric = Number(value)
  const date = Number.isFinite(numeric)
    ? new Date(numeric < 1_000_000_000_000 ? numeric * 1000 : numeric)
    : new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

function confLabel(c: number) {
  if (c >= 0.75) return "высокая"
  if (c >= 0.5) return "средняя"
  if (c >= 0.3) return "ниже средней"
  return "низкая — проверьте вручную"
}

function StatusBadge({ status, isOverdue }: { status: string; isOverdue?: boolean }) {
  if (isOverdue) {
    return <Badge variant="destructive">Просрочено</Badge>
  }
  if (status === "open") {
    return (
      <Badge variant="outline" className="border-primary/50 bg-primary/10 text-primary">
        На рассмотрении
      </Badge>
    )
  }
  return (
    <Badge variant="outline" className="border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400">
      Отвечено
    </Badge>
  )
}

function SentimentCell({ sentiment }: { sentiment: Appeal["sentiment"] }) {
  if (!sentiment) return <span className="text-muted-foreground">—</span>
  return (
    <span className="inline-flex items-center gap-1 text-xs" title={sentiment.label}>
      {sentiment.emoji ? <span>{sentiment.emoji}</span> : null}
      <span className="text-muted-foreground">{sentiment.label}</span>
    </span>
  )
}

export function AppealsPage() {
  const { meta, isAdmin, operator, setOpenAppeals } = useApp()
  const [appeals, setAppeals] = React.useState<Appeal[]>([])
  const [loading, setLoading] = React.useState(true)
  const [search, setSearch] = React.useState("")
  const [dateSort, setDateSort] = React.useState<"desc" | "asc">("desc")
  const [filters, setFilters] = React.useState<AppealFilters>(EMPTY_FILTERS)
  const [filterModalOpen, setFilterModalOpen] = React.useState(false)
  const [selected, setSelected] = React.useState<Appeal | null>(null)
  const [sheetOpen, setSheetOpen] = React.useState(false)
  const [usvoCardId, setUsvoCardId] = React.useState<number | null>(null)
  const [usvoSheetOpen, setUsvoSheetOpen] = React.useState(false)
  const [answer, setAnswer] = React.useState("")
  const [confidence, setConfidence] = React.useState<number | null>(null)
  const [history, setHistory] = React.useState<AppealHistory | null>(null)
  const [historyLoading, setHistoryLoading] = React.useState(false)
  const [drafting, setDrafting] = React.useState(false)
  const [sending, setSending] = React.useState(false)
  const [assignee, setAssignee] = React.useState("")
  const [confirmDelete, setConfirmDelete] = React.useState(false)
  const [deleting, setDeleting] = React.useState(false)
  const [collapsedGroups, setCollapsedGroups] = React.useState<Record<string, boolean>>({})

  const loadAppeals = React.useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const { items } = await api<{ items: Appeal[] }>("/appeals")
      setAppeals(items)
      const open = items.filter((a) => a.status === "open").length
      setOpenAppeals(open)
    } catch (e) {
      if (!silent) toast.error(e instanceof Error ? e.message : "Ошибка загрузки")
    } finally {
      if (!silent) setLoading(false)
    }
  }, [setOpenAppeals])

  React.useEffect(() => {
    void loadAppeals()
  }, [loadAppeals])

  React.useEffect(() => {
    const id = window.setInterval(() => {
      if (!sheetOpen) void loadAppeals(true)
    }, 15000)
    return () => window.clearInterval(id)
  }, [sheetOpen, loadAppeals])

  const openAppeal = React.useCallback(
    async (appeal: Appeal) => {
      setSelected(appeal)
      setAnswer(appeal.answer || "")
      setConfidence(null)
      setHistory(null)
      const ops = isAdmin
        ? [...new Set([appeal.assignee, operator, ...(meta?.operators || [])].filter(Boolean))] as string[]
        : [operator]
      setAssignee(isAdmin ? appeal.assignee || operator : operator)
      setSheetOpen(true)
      setHistoryLoading(true)
      try {
        const res = await api<AppealHistory>(`/appeals/${encodeURIComponent(appeal.id)}/history`)
        setHistory(res)
      } catch {
        /* ignore */
      } finally {
        setHistoryLoading(false)
      }
      void ops
    },
    [isAdmin, operator, meta?.operators],
  )

  const filterOptions = React.useMemo(() => {
    const unique = (values: (string | null | undefined)[]) =>
      [...new Set(values.map((value) => value?.trim()).filter(Boolean) as string[])].sort((a, b) =>
        a.localeCompare(b, "ru"),
      )
    return {
      tones: unique(appeals.map((appeal) => appeal.sentiment?.label)),
      topics: unique(appeals.map((appeal) => appeal.topic)),
      assignees: unique(appeals.map((appeal) => appeal.assignee)),
    }
  }, [appeals])

  const filtered = React.useMemo(() => {
    const query = normalized(search)
    const from = filters.dateFrom ? new Date(`${filters.dateFrom}T00:00:00`) : null
    const to = filters.dateTo ? new Date(`${filters.dateTo}T23:59:59.999`) : null
    return appeals.filter((appeal) => {
      const created = appealDate(appeal.created_at)
      if (
        query &&
        !normalized([
          appeal.id,
          appeal.question,
          appeal.summary,
          appeal.topic,
          appeal.assignee,
          appeal.citizen?.name,
          appeal.citizen?.phone,
          appeal.sentiment?.label,
          appeal.created_human,
          appeal.answer,
        ].join(" ")).includes(query)
      ) return false
      if (filters.tone && normalized(appeal.sentiment?.label) !== normalized(filters.tone)) return false
      if (from && (!created || created < from)) return false
      if (to && (!created || created > to)) return false
      if (filters.topic && normalized(appeal.topic) !== normalized(filters.topic)) return false
      if (filters.assignee === "__unassigned__" && appeal.assignee) return false
      if (
        filters.assignee &&
        filters.assignee !== "__unassigned__" &&
        normalized(appeal.assignee) !== normalized(filters.assignee)
      ) return false
      if (filters.status && appeal.status !== filters.status) return false
      if (filters.deadline === "overdue" && !appeal.is_overdue) return false
      if (filters.deadline === "current" && appeal.is_overdue) return false
      return true
    })
  }, [appeals, filters, search])

  const sorted = React.useMemo(() => {
    const dir = dateSort === "desc" ? 1 : -1
    return [...filtered].sort(
      (a, b) => dir * ((Number(b.created_at) || 0) - (Number(a.created_at) || 0)),
    )
  }, [filtered, dateSort])
  const groupedAppeals = React.useMemo(
    () => ({
      overdue: sorted.filter((appeal) => Boolean(appeal.is_overdue) && appeal.status !== "answered"),
      review: sorted.filter((appeal) => appeal.status === "open" && !appeal.is_overdue),
      answered: sorted.filter((appeal) => appeal.status === "answered"),
    }),
    [sorted],
  )

  function toggleGroup(key: string) {
    setCollapsedGroups((current) => ({ ...current, [key]: !current[key] }))
  }
  const activeFilterCount = Object.values(filters).filter(Boolean).length
  const filterTags = React.useMemo(() => {
    const labels: Record<keyof AppealFilters, string> = {
      tone: "Тон",
      dateFrom: "Дата с",
      dateTo: "Дата по",
      topic: "Тематика",
      assignee: "Ответственный",
      status: "Статус",
      deadline: "Срок",
    }
    const valueLabels: Record<string, string> = {
      __unassigned__: "Не назначен",
      open: "На рассмотрении",
      answered: "Отвечено",
      overdue: "Просроченные",
      current: "Не просроченные",
    }
    const dateLabel = (value: string) => {
      const [year, month, day] = value.split("-")
      return day && month && year ? `${day}.${month}.${year}` : value
    }
    return (Object.entries(filters) as [keyof AppealFilters, string][])
      .filter(([, value]) => value)
      .map(([key, value]) => ({
        key,
        label: `${labels[key]}: ${
          key === "dateFrom" || key === "dateTo" ? dateLabel(value) : valueLabels[value] || value
        }`,
      }))
  }, [filters])

  function clearFilter(key: keyof AppealFilters) {
    setFilters((current) => ({ ...current, [key]: "" }))
  }

  const assigneeOptions = React.useMemo(() => {
    if (!selected) return [operator]
    return isAdmin
      ? [...new Set([selected.assignee, operator, ...(meta?.operators || [])].filter(Boolean))] as string[]
      : [operator]
  }, [selected, isAdmin, operator, meta?.operators])

  async function onAssigneeChange(value: string) {
    if (!selected) return
    setAssignee(value)
    try {
      const updated = await api<Appeal>(`/appeals/${selected.id}/assignee`, {
        method: "POST",
        body: JSON.stringify({ assignee: value }),
      })
      setSelected(updated)
      setAppeals((prev) => prev.map((a) => (a.id === updated.id ? updated : a)))
      toast.success("Ответственный обновлён")
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка")
    }
  }

  async function onDraft() {
    if (!selected) return
    setDrafting(true)
    try {
      const res = await api<{ draft: string; confidence?: number; source?: string }>(
        `/appeals/${selected.id}/draft`,
        { method: "POST", body: JSON.stringify({ operator: assignee || operator }) },
      )
      setAnswer(res.draft)
      setConfidence(res.confidence ?? null)
      const sourceLabel =
        res.source === "dify" ? "Dify" : res.source === "model" ? "модель" : "шаблон"
      toast.success(`Черновик сформирован (${sourceLabel})`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка")
    } finally {
      setDrafting(false)
    }
  }

  async function onSend() {
    if (!selected) return
    const text = answer.trim()
    if (!text) {
      toast.error("Введите текст ответа")
      return
    }
    setSending(true)
    try {
      const res = await api<{ delivered_to_citizen?: boolean; saved_to_kb?: boolean }>(
        `/appeals/${selected.id}/answer`,
        { method: "POST", body: JSON.stringify({ answer: text, assignee }) },
      )
      let msg = "Ответ сохранён"
      if (res.delivered_to_citizen) msg += ", отправлен гражданину"
      if (res.saved_to_kb) msg += ", записан в базу знаний"
      toast.success(msg)
      setSheetOpen(false)
      void loadAppeals(true)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка")
    } finally {
      setSending(false)
    }
  }

  async function onDelete() {
    if (!selected) return
    setDeleting(true)
    try {
      await api(`/appeals/${selected.id}`, { method: "DELETE" })
      toast.success("Обращение удалено")
      setConfirmDelete(false)
      setSheetOpen(false)
      void loadAppeals(true)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка")
    } finally {
      setDeleting(false)
    }
  }

  function openUsvoCard(id: number) {
    setUsvoCardId(id)
    setUsvoSheetOpen(true)
  }

  return (
    <>
      <PageActions>
        <Button
          variant="outline"
          size="icon"
          className="h-8 w-8 shrink-0"
          title="Фильтры"
          aria-label="Фильтры"
          onClick={() => setFilterModalOpen(true)}
        >
          <Filter className="h-4 w-4" />
        </Button>
        <div className="relative min-w-0 flex-1">
          <Search className="absolute left-2.5 top-2 h-4 w-4 text-muted-foreground" />
          <Input
            className="h-8 pl-9"
            placeholder="Поиск по вопросу, ФИО, теме, ответственному…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <Button variant="outline" size="sm" asChild>
          <a href={exportUrl("/export/appeals")} download>
            Экспорт
            <Download className="h-4 w-4" />
          </a>
        </Button>
        <Button
          variant="outline"
          size="icon"
          className="h-8 w-8"
          title="Обновить"
          aria-label="Обновить"
          onClick={() => void loadAppeals()}
        >
          <RefreshCw className="h-4 w-4" />
        </Button>
      </PageActions>

      {filterTags.length > 0 ? (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          {filterTags.map((tag) => (
            <Badge key={tag.key} variant="secondary" className="gap-1 pr-1 font-normal">
              {tag.label}
              <button
                type="button"
                className="rounded-sm p-0.5 hover:bg-muted-foreground/20"
                aria-label={`Убрать фильтр: ${tag.label}`}
                onClick={() => clearFilter(tag.key)}
              >
                <X className="h-3 w-3" />
              </button>
            </Badge>
          ))}
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto h-7 px-2 text-xs"
            onClick={() => setFilters(EMPTY_FILTERS)}
          >
            Сбросить
          </Button>
        </div>
      ) : null}

      <Dialog open={filterModalOpen} onOpenChange={setFilterModalOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Фильтры обращений</DialogTitle>
            <DialogDescription>
              Отфильтруйте обращения по значениям любой колонки.
            </DialogDescription>
          </DialogHeader>
          <div className="grid max-h-[65vh] gap-4 overflow-y-auto p-1 sm:grid-cols-2">
            <AppealFilterSelect
              label="Тон"
              value={filters.tone}
              options={[["", "Все"], ...filterOptions.tones.map((tone) => [tone, tone])]}
              onChange={(tone) => setFilters((current) => ({ ...current, tone }))}
            />
            <AppealFilterSelect
              label="Тематика"
              value={filters.topic}
              options={[["", "Все"], ...filterOptions.topics.map((topic) => [topic, topic])]}
              onChange={(topic) => setFilters((current) => ({ ...current, topic }))}
            />
            <FormField>
              <Label htmlFor="appeal-date-from" className="text-xs">Дата обращения — с</Label>
              <Input
                id="appeal-date-from"
                className="h-9"
                type="date"
                value={filters.dateFrom}
                onChange={(event) => setFilters((current) => ({ ...current, dateFrom: event.target.value }))}
              />
            </FormField>
            <FormField>
              <Label htmlFor="appeal-date-to" className="text-xs">Дата обращения — по</Label>
              <Input
                id="appeal-date-to"
                className="h-9"
                type="date"
                value={filters.dateTo}
                onChange={(event) => setFilters((current) => ({ ...current, dateTo: event.target.value }))}
              />
            </FormField>
            <AppealFilterSelect
              label="Ответственный"
              value={filters.assignee}
              options={[
                ["", "Все"],
                ["__unassigned__", "Не назначен"],
                ...filterOptions.assignees.map((assignee) => [assignee, assignee]),
              ]}
              onChange={(assignee) => setFilters((current) => ({ ...current, assignee }))}
            />
            <AppealFilterSelect
              label="Статус"
              value={filters.status}
              options={[
                ["", "Все"],
                ["open", "На рассмотрении"],
                ["answered", "Отвечено"],
              ]}
              onChange={(status) => setFilters((current) => ({ ...current, status }))}
            />
            <AppealFilterSelect
              label="Срок"
              value={filters.deadline}
              options={[
                ["", "Все"],
                ["overdue", "Просроченные"],
                ["current", "Не просроченные"],
              ]}
              onChange={(deadline) => setFilters((current) => ({ ...current, deadline }))}
            />
          </div>
          <DialogFooter className="gap-2 sm:justify-between">
            <Button
              variant="ghost"
              disabled={activeFilterCount === 0}
              onClick={() => setFilters(EMPTY_FILTERS)}
            >
              Сбросить
            </Button>
            <Button onClick={() => setFilterModalOpen(false)}>
              Показать {filtered.length}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {loading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : sorted.length === 0 ? (
        <EmptyState
          title={appeals.length === 0 ? "Все обращения обработаны, ИИ отдыхает" : "Обращения не найдены"}
          description={
            appeals.length === 0
              ? "Новые вопросы граждан из бота MAX появятся здесь автоматически."
              : "Измените поисковый запрос или сбросьте фильтры."
          }
        />
      ) : (
        <div className="rounded-xl border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[100px]">Тон</TableHead>
                <TableHead className="h-auto max-w-[280px] w-[280px] py-2">
                  <div className="leading-tight">
                    <div>Вопрос</div>
                    <div className="text-xs font-normal text-muted-foreground/80">Суть</div>
                  </div>
                </TableHead>
                <TableHead className="h-auto w-[140px] py-2">
                  <button
                    type="button"
                    className="-mx-1 flex w-full items-center justify-between gap-1 rounded-md px-1 py-0.5 text-left hover:bg-muted/60"
                    title={dateSort === "desc" ? "Сначала новые — нажмите для старых" : "Сначала старые — нажмите для новых"}
                    aria-label={dateSort === "desc" ? "Сортировка: сначала новые" : "Сортировка: сначала старые"}
                    onClick={() => setDateSort((current) => (current === "desc" ? "asc" : "desc"))}
                  >
                    <div className="leading-tight">
                      <div>Дата</div>
                      <div className="text-xs font-normal text-muted-foreground/80">Срок</div>
                    </div>
                    {dateSort === "desc" ? (
                      <ArrowDown className="h-3.5 w-3.5 shrink-0 text-foreground" />
                    ) : (
                      <ArrowUp className="h-3.5 w-3.5 shrink-0 text-foreground" />
                    )}
                  </button>
                </TableHead>
                <TableHead className="w-[140px]">Тематика</TableHead>
                <TableHead className="w-[160px]">Ответственный</TableHead>
                <TableHead className="w-[160px]">Статус</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {[
                {
                  key: "overdue",
                  title: "Просроченные",
                  items: groupedAppeals.overdue,
                  icon: AlertTriangle,
                  headerClass: "border-destructive/20 bg-destructive/10 text-destructive hover:bg-destructive/10",
                  rowClass: "bg-destructive/5",
                  empty: "Просроченных обращений нет",
                },
                {
                  key: "review",
                  title: "На рассмотрении",
                  items: groupedAppeals.review,
                  icon: Clock,
                  headerClass: "bg-primary/10 text-primary hover:bg-primary/10",
                  rowClass: "",
                  empty: "Обращений на рассмотрении нет",
                },
                {
                  key: "answered",
                  title: "Отвеченные",
                  items: groupedAppeals.answered,
                  icon: CheckCircle2,
                  headerClass: "border-emerald-500/20 bg-emerald-500/10 text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-400",
                  rowClass: "bg-emerald-500/5",
                  empty: "Отвеченных обращений нет",
                },
              ].map((group) => {
                const collapsed = Boolean(collapsedGroups[group.key])
                return (
                  <React.Fragment key={group.key}>
                    <TableRow
                      className={cn(group.headerClass, "cursor-pointer")}
                      onClick={() => toggleGroup(group.key)}
                    >
                      <TableCell colSpan={6} className="h-10 py-2">
                        <div className="flex items-center gap-2 font-semibold">
                          {collapsed ? (
                            <ChevronRight className="h-4 w-4 shrink-0" />
                          ) : (
                            <ChevronDown className="h-4 w-4 shrink-0" />
                          )}
                          <group.icon className="h-4 w-4" />
                          <span>{group.title}</span>
                          <Badge variant="outline" className="ml-1 h-5 bg-background/60 px-1.5">
                            {group.items.length}
                          </Badge>
                          {group.key === "overdue" ? (
                            <span className="ml-auto text-xs font-normal">
                              Регламент: {meta?.sla_business_days ?? 3} дн.
                            </span>
                          ) : null}
                        </div>
                      </TableCell>
                    </TableRow>
                    {collapsed ? null : group.items.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={6} className="h-14 text-center text-sm text-muted-foreground">
                          {group.empty}
                        </TableCell>
                      </TableRow>
                    ) : (
                      group.items.map((a) => (
                        <TableRow
                          key={a.id}
                          className={cn("cursor-pointer", group.rowClass)}
                          onClick={() => void openAppeal(a)}
                        >
                          <TableCell>
                            <SentimentCell sentiment={a.sentiment} />
                          </TableCell>
                          <TableCell className="max-w-[280px] py-2">
                            <div className="min-w-0 space-y-0.5">
                              <div className="truncate text-sm font-medium leading-5" title={a.question}>
                                {a.question}
                              </div>
                              <div
                                className="truncate text-xs leading-4 text-muted-foreground"
                                title={a.summary || a.citizen?.name || "Гражданин"}
                              >
                                {a.summary || a.citizen?.name || "Гражданин"}
                              </div>
                            </div>
                          </TableCell>
                          <TableCell>
                            <div>{a.created_human}</div>
                            {a.age ? <div className="text-xs text-muted-foreground">{a.age}</div> : null}
                          </TableCell>
                          <TableCell>
                            <Badge variant="secondary" className="whitespace-nowrap">{a.topic}</Badge>
                          </TableCell>
                          <TableCell>
                            {a.assignee ? (
                              <span className="text-sm">{a.assignee}</span>
                            ) : (
                              <span className="text-sm text-muted-foreground">не назначен</span>
                            )}
                          </TableCell>
                          <TableCell>
                            <StatusBadge status={a.status} isOverdue={a.is_overdue} />
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </React.Fragment>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}

      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent className="w-full sm:max-w-xl">
          {selected ? (
            <>
              <SheetHeader>
                <SheetTitle className="flex items-center gap-2 pr-8 text-xl">
                  Обращение №{selected.id.replace(/^(esc|seed)-/, "")}
                  <StatusBadge status={selected.status} isOverdue={selected.is_overdue} />
                </SheetTitle>
                <SheetDescription asChild>
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-base font-medium leading-8 text-foreground">
                        {selected.citizen?.name || "Гражданин"}
                      </p>
                      {selected.usvo_id ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 shrink-0 px-2"
                          onClick={() => openUsvoCard(selected.usvo_id!)}
                        >
                          Карточка УСВО
                          <ArrowUpRight className="h-4 w-4" />
                        </Button>
                      ) : null}
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {selected.created_human ? (
                        <Badge variant="secondary" className="font-normal">
                          {selected.created_human}
                        </Badge>
                      ) : null}
                      {selected.topic ? (
                        <Badge variant="secondary" className="font-normal">
                          {selected.topic}
                        </Badge>
                      ) : null}
                      {selected.sentiment ? (
                        <Badge variant="secondary" className="font-normal">
                          {selected.sentiment.emoji ? `${selected.sentiment.emoji} ` : ""}
                          {selected.sentiment.label}
                        </Badge>
                      ) : null}
                    </div>
                  </div>
                </SheetDescription>
              </SheetHeader>

              <ScrollArea className="flex-1">
                <div className="space-y-4 px-6 pb-6 pt-6">
                  {selected.summary ? (
                    <div className="flex gap-3 rounded-lg border bg-muted/40 p-3">
                      <Sparkles className="h-5 w-5 shrink-0 text-primary" />
                      <div>
                        <div className="text-xs font-medium text-muted-foreground">Суть кратко · ИИ</div>
                        <p className="text-sm">{selected.summary}</p>
                      </div>
                    </div>
                  ) : null}

                  <div>
                    <Label className="mb-2">Вопрос гражданина</Label>
                    <div className="rounded-lg border bg-muted/30 p-3 text-sm">{selected.question}</div>
                  </div>

                  {selected.usvo_ambiguous && (selected.usvo_matches?.length ?? 0) > 1 ? (
                    <div className="rounded-lg border border-primary/40 bg-primary/10 p-3 text-sm">
                      <p className="mb-2 font-medium">Несколько карточек УСВО с этим номером телефона</p>
                      <div className="flex flex-wrap gap-2">
                        {selected.usvo_matches?.map((m) => (
                          <Button key={m.id} variant="outline" size="sm" onClick={() => openUsvoCard(m.id)}>
                            <IdCard className="h-4 w-4" />
                            {m.name || `Карточка #${m.id}`}
                          </Button>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  <FormField>
                    <Label>Ответственный за ответ</Label>
                    <Select
                      value={assignee}
                      onValueChange={(v) => void onAssigneeChange(v)}
                      disabled={!isAdmin}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {assigneeOptions.map((o) => (
                          <SelectItem key={o} value={o}>
                            {o}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </FormField>

                  <FormField>
                    <div className="flex items-center justify-between gap-2">
                      <Label>Ответ гражданину</Label>
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-8 shrink-0"
                        disabled={drafting}
                        onClick={() => void onDraft()}
                      >
                        {drafting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
                        Сформировать черновик
                      </Button>
                    </div>
                    <Textarea
                      rows={6}
                      value={answer}
                      onChange={(e) => setAnswer(e.target.value)}
                      placeholder="Нажмите «Сформировать черновик», чтобы ИИ подставил ответ из базы знаний…"
                    />
                    {confidence !== null ? (
                      <div className="space-y-2 rounded-lg border p-3">
                        <div className="flex items-center justify-between text-sm">
                          <span>Уверенность ИИ в черновике</span>
                          <Badge variant="secondary">{confLabel(confidence)}</Badge>
                        </div>
                        <div className="h-2 overflow-hidden rounded-full bg-muted">
                          <div
                            className="h-full rounded-full bg-primary transition-all"
                            style={{ width: `${Math.round(confidence * 100)}%` }}
                          />
                        </div>
                        <div className="flex justify-between text-xs text-muted-foreground">
                          <span>0%</span>
                          <span>{Math.round(confidence * 100)}%</span>
                          <span>100%</span>
                        </div>
                      </div>
                    ) : null}
                  </FormField>

                  <div className="space-y-3 border-t pt-4">
                    <div className="text-sm font-medium">История обращений гражданина</div>
                    {historyLoading ? (
                      <Skeleton className="h-24 w-full" />
                    ) : history ? (
                      <>
                        {(() => {
                          const items = history.items || []
                          if (!items.length) {
                            return (
                              <p className="text-sm text-muted-foreground">Предыдущих обращений нет</p>
                            )
                          }
                          return (
                            <div className="flex flex-col gap-3 rounded-lg border bg-muted/20 p-3">
                              {items.map((item) => (
                                <div key={item.id} className="flex flex-col gap-2">
                                  {item.question ? (
                                    <div className="flex flex-col items-start gap-1">
                                      <span className="text-[11px] text-muted-foreground">
                                        Гражданин
                                        {item.created_human ? ` · ${item.created_human}` : ""}
                                        {item.is_current ? " · текущее" : ""}
                                      </span>
                                      <div className="max-w-[90%] rounded-2xl rounded-tl-md border bg-background px-3 py-2 text-sm leading-relaxed">
                                        {item.question}
                                      </div>
                                    </div>
                                  ) : null}
                                  {item.answer ? (
                                    <div className="flex flex-col items-end gap-1">
                                      <span className="text-[11px] text-muted-foreground">Сотрудник</span>
                                      <div className="max-w-[90%] rounded-2xl rounded-tr-md bg-primary px-3 py-2 text-sm leading-relaxed text-primary-foreground">
                                        {item.answer}
                                      </div>
                                    </div>
                                  ) : item.is_current ? (
                                    <div className="flex flex-col items-end gap-1">
                                      <span className="text-[11px] text-muted-foreground">Сотрудник</span>
                                      <div className="max-w-[90%] rounded-2xl rounded-tr-md border border-dashed px-3 py-2 text-sm text-muted-foreground">
                                        Ответ ещё не отправлен
                                      </div>
                                    </div>
                                  ) : null}
                                </div>
                              ))}
                            </div>
                          )
                        })()}
                        {history.profile ? (
                          <p className="text-xs text-muted-foreground">
                            {[
                              history.profile.user_id ? `MAX ID: ${history.profile.user_id}` : "",
                              history.profile.username ? `@${history.profile.username}` : "",
                              history.profile.phone,
                            ]
                              .filter(Boolean)
                              .join(" · ")}
                          </p>
                        ) : null}
                      </>
                    ) : (
                      <p className="text-sm text-muted-foreground">Не удалось загрузить историю</p>
                    )}
                  </div>
                </div>
              </ScrollArea>

              <SheetFooter className="flex-row flex-nowrap items-center justify-end gap-2">
                <Button
                  variant="outline"
                  size="icon"
                  className="mr-auto h-8 w-8 shrink-0 border-destructive text-destructive hover:bg-destructive/10 hover:text-destructive"
                  title="Удалить"
                  aria-label="Удалить"
                  onClick={() => setConfirmDelete(true)}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
                <Button size="sm" className="shrink-0" disabled={sending} onClick={() => void onSend()}>
                  {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                  Отправить ответ
                </Button>
              </SheetFooter>
            </>
          ) : null}
        </SheetContent>
      </Sheet>

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Удалить обращение?</DialogTitle>
            <DialogDescription>
              Обращение будет удалено из списка без возможности восстановления.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              Отмена
            </Button>
            <Button variant="destructive" disabled={deleting} onClick={() => void onDelete()}>
              {deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Удалить
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <UsvoCardSheet
        nested
        cardId={usvoCardId}
        open={usvoSheetOpen}
        onOpenChange={(open) => {
          setUsvoSheetOpen(open)
          if (!open) setUsvoCardId(null)
        }}
      />
    </>
  )
}

function AppealFilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: readonly (readonly [string, string])[] | string[][]
  onChange: (value: string) => void
}) {
  return (
    <FormField>
      <Label className="text-xs">{label}</Label>
      <Select value={value || " "} onValueChange={(next) => onChange(next === " " ? "" : next)}>
        <SelectTrigger className="h-9">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map(([optionValue, optionLabel]) => (
            <SelectItem key={optionValue || "_"} value={optionValue || " "}>
              {optionLabel}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </FormField>
  )
}
