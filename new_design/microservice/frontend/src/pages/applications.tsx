import * as React from "react"
import { ArrowDown, ArrowUp, BookOpen, Download, Filter, Loader2, MoreVertical, Pencil, Plus, RefreshCw, Search, Trash2, X } from "lucide-react"
import { toast } from "sonner"
import { api, exportUrl } from "@/lib/api"
import type { Application, Measure } from "@/lib/types"
import { cn } from "@/lib/utils"
import { useApp } from "@/hooks/use-app"
import { PageActions } from "@/components/page-actions"
import { EmptyState } from "@/components/empty-state"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { Label } from "@/components/ui/label"
import { FormField } from "@/components/ui/form-field"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

const APP_STATUS: Record<
  string,
  { label: string; variant: "default" | "secondary" | "destructive" | "outline"; className?: string }
> = {
  submitted: {
    label: "На рассмотрении",
    variant: "outline",
    className: "border-primary/50 bg-primary/10 text-primary",
  },
  approved: {
    label: "Одобрено",
    variant: "outline",
    className: "border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  },
  rejected: { label: "Отклонено", variant: "destructive" },
}

type ApplicationSortKey = "date"
type SortDir = "asc" | "desc"

type ApplicationFilters = {
  measure: string
  dateFrom: string
  dateTo: string
  basis: string
  documents: string
  status: string
}

const EMPTY_APPLICATION_FILTERS: ApplicationFilters = {
  measure: "",
  dateFrom: "",
  dateTo: "",
  basis: "",
  documents: "",
  status: "",
}

type MeasureFilters = {
  status: string
  template: string
  documents: string
}

const EMPTY_MEASURE_FILTERS: MeasureFilters = {
  status: "",
  template: "",
  documents: "",
}

type AppRecord = Application & {
  id: string | number
  measure_title?: string
  status?: string
  created_human?: string
  is_measure?: boolean
  applicant?: { fio?: string; birth_date?: string; passport_series?: string; passport_number?: string; passport_issued?: string; address?: string; phone?: string }
  citizen?: { name?: string; username?: string }
  category?: string
  category_code?: string
  ownership?: string
  rooms?: string | number
  missing?: string[]
  measure_fields?: { label: string; value: string }[]
  documents?: string[]
  user_files?: string[]
  family?: { fio?: string; relation?: string; birth_date?: string }[]
  providers?: { name?: string; account?: string }[]
  payment?: { method?: string; bank?: string; account?: string }
}

function normalized(value: unknown) {
  return String(value ?? "").trim().toLocaleLowerCase("ru")
}

function applicationDate(application: AppRecord) {
  const raw = application.created_at
  if (raw) {
    const numeric = Number(raw)
    const date = Number.isFinite(numeric)
      ? new Date(numeric < 1_000_000_000_000 ? numeric * 1000 : numeric)
      : new Date(String(raw))
    if (!Number.isNaN(date.getTime())) return date
  }
  const match = /(\d{1,2})\.(\d{1,2})\.(\d{4})/.exec(application.created_human || "")
  if (!match) return null
  return new Date(Number(match[3]), Number(match[2]) - 1, Number(match[1]))
}

export function ApplicationsPage() {
  const { isAdmin, operator, setOpenApps, meta } = useApp()
  const [tab, setTab] = React.useState("list")
  const [applications, setApplications] = React.useState<AppRecord[]>([])
  const [search, setSearch] = React.useState("")
  const [sortKey, setSortKey] = React.useState<ApplicationSortKey>("date")
  const [sortDir, setSortDir] = React.useState<SortDir>("desc")
  const [filters, setFilters] = React.useState<ApplicationFilters>(EMPTY_APPLICATION_FILTERS)
  const [filterModalOpen, setFilterModalOpen] = React.useState(false)
  const [measures, setMeasures] = React.useState<Measure[]>([])
  const [measureSearch, setMeasureSearch] = React.useState("")
  const [measureFilters, setMeasureFilters] = React.useState<MeasureFilters>(EMPTY_MEASURE_FILTERS)
  const [measureFilterModalOpen, setMeasureFilterModalOpen] = React.useState(false)
  const [loading, setLoading] = React.useState(true)
  const [selected, setSelected] = React.useState<AppRecord | null>(null)
  const [sheetOpen, setSheetOpen] = React.useState(false)
  const [deciding, setDeciding] = React.useState(false)
  const [confirmDelete, setConfirmDelete] = React.useState(false)
  const [measureDialog, setMeasureDialog] = React.useState<Measure | null | "new">(null)
  const [measureForm, setMeasureForm] = React.useState({
    title: "",
    description: "",
    llm_hint: "",
    active: true,
  })

  const loadApplications = React.useCallback(async () => {
    setLoading(true)
    try {
      const { items } = await api<{ items: AppRecord[] }>("/applications")
      setApplications(items)
      const pending = items.filter((a) => a.status === "submitted").length
      setOpenApps(pending)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка загрузки")
    } finally {
      setLoading(false)
    }
  }, [setOpenApps])

  const loadMeasures = React.useCallback(async () => {
    try {
      const { items } = await api<{ items: Measure[] }>("/settings/support-measures")
      setMeasures(items)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка загрузки мер")
    }
  }, [])

  React.useEffect(() => {
    void loadApplications()
  }, [loadApplications])

  React.useEffect(() => {
    if (tab === "measures" && isAdmin) void loadMeasures()
  }, [tab, isAdmin, loadMeasures])

  React.useEffect(() => {
    const id = window.setInterval(() => {
      if (!sheetOpen) void loadApplications()
    }, 15000)
    return () => window.clearInterval(id)
  }, [sheetOpen, loadApplications])

  function openApplication(app: AppRecord) {
    setSelected(app)
    setSheetOpen(true)
  }

  async function decide(decision: "approve" | "reject") {
    if (!selected) return
    setDeciding(true)
    try {
      await api(`/applications/${selected.id}/${decision}`, {
        method: "POST",
        body: JSON.stringify({ operator }),
      })
      toast.success(decision === "approve" ? "Заявление одобрено" : "Заявление отклонено")
      setSheetOpen(false)
      void loadApplications()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка")
    } finally {
      setDeciding(false)
    }
  }

  async function deleteApplication() {
    if (!selected) return
    try {
      await api(`/applications/${selected.id}`, { method: "DELETE" })
      toast.success("Заявление удалено")
      setConfirmDelete(false)
      setSheetOpen(false)
      void loadApplications()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка")
    }
  }

  function openMeasureForm(m: Measure | null) {
    setMeasureDialog(m ? m : "new")
    setMeasureForm({
      title: m?.title || "",
      description: (m?.description as string) || "",
      llm_hint: (m?.llm_hint as string) || "",
      active: m?.active !== false,
    })
  }

  async function saveMeasure() {
    if (!measureForm.title.trim()) {
      toast.error("Укажите название меры")
      return
    }
    const payload = {
      title: measureForm.title.trim(),
      description: measureForm.description.trim(),
      llm_hint: measureForm.llm_hint.trim(),
      documents: measureDialog && measureDialog !== "new" ? measureDialog.documents || [] : [],
      placeholders: measureDialog && measureDialog !== "new" ? measureDialog.placeholders || [] : [],
      active: measureForm.active,
    }
    try {
      if (measureDialog && measureDialog !== "new") {
        await api(`/settings/support-measures/${measureDialog.id}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        })
        toast.success("Мера обновлена")
      } else {
        await api("/settings/support-measures", { method: "POST", body: JSON.stringify(payload) })
        toast.success("Мера создана")
      }
      setMeasureDialog(null)
      void loadMeasures()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка")
    }
  }

  async function deleteMeasure(m: Measure) {
    if (!window.confirm(`Мера поддержки «${m.title}» будет удалена.`)) return
    try {
      await api(`/settings/support-measures/${m.id}`, { method: "DELETE" })
      toast.success("Мера удалена")
      void loadMeasures()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка")
    }
  }

  async function syncKb() {
    try {
      const r = await api<{ count?: number }>("/settings/support-measures/sync-kb", { method: "POST" })
      toast.success(`В базу знаний выгружено мер: ${r.count ?? 0}`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка")
    }
  }

  const filterOptions = React.useMemo(() => {
    const unique = (values: (string | null | undefined)[]) =>
      [...new Set(values.map((value) => value?.trim()).filter(Boolean) as string[])].sort((a, b) =>
        a.localeCompare(b, "ru"),
      )
    return {
      measures: unique(applications.map((application) => application.measure_title)),
      bases: unique(applications.map((application) => application.is_measure ? "Мера поддержки" : application.category || "—")),
    }
  }, [applications])

  const filteredApplications = React.useMemo(() => {
    const query = normalized(search)
    const from = filters.dateFrom ? new Date(`${filters.dateFrom}T00:00:00`) : null
    const to = filters.dateTo ? new Date(`${filters.dateTo}T23:59:59.999`) : null
    return applications.filter((application) => {
      const applicant = application.applicant?.fio || application.citizen?.name || "Заявитель"
      const basis = application.is_measure ? "Мера поддержки" : application.category || "—"
      const status = APP_STATUS[application.status || ""]?.label || application.status_label || application.status || "—"
      const documents = application.is_measure
        ? "fields"
        : application.missing?.length
          ? "incomplete"
          : "complete"
      const created = applicationDate(application)
      if (
        query &&
        !normalized([
          application.measure_title,
          applicant,
          application.created_human,
          basis,
          status,
        ].join(" ")).includes(query)
      ) return false
      if (filters.measure && application.measure_title !== filters.measure) return false
      if (from && (!created || created < from)) return false
      if (to && (!created || created > to)) return false
      if (filters.basis && basis !== filters.basis) return false
      if (filters.documents && documents !== filters.documents) return false
      if (filters.status && application.status !== filters.status) return false
      return true
    })
  }, [applications, filters, search])

  const sortedApplications = React.useMemo(() => {
    const dir = sortDir === "asc" ? 1 : -1
    return [...filteredApplications].sort((a, b) => {
      const aTime = applicationDate(a)?.getTime() ?? 0
      const bTime = applicationDate(b)?.getTime() ?? 0
      return (aTime - bTime) * dir
    })
  }, [filteredApplications, sortDir])

  function toggleSort(key: ApplicationSortKey) {
    if (sortKey === key) {
      setSortDir((current) => (current === "desc" ? "asc" : "desc"))
      return
    }
    setSortKey(key)
    setSortDir("desc")
  }

  const activeFilterCount = Object.values(filters).filter(Boolean).length
  const filterTags = React.useMemo(() => {
    const labels: Record<keyof ApplicationFilters, string> = {
      measure: "Мера",
      dateFrom: "Дата с",
      dateTo: "Дата по",
      basis: "Основание",
      documents: "Документы",
      status: "Статус",
    }
    const valueLabels: Record<string, string> = {
      complete: "Полный пакет",
      incomplete: "Требуется уточнение",
      fields: "Заполненные поля",
      submitted: "На рассмотрении",
      approved: "Одобрено",
      rejected: "Отклонено",
    }
    const dateLabel = (value: string) => {
      const [year, month, day] = value.split("-")
      return day && month && year ? `${day}.${month}.${year}` : value
    }
    return (Object.entries(filters) as [keyof ApplicationFilters, string][])
      .filter(([, value]) => value)
      .map(([key, value]) => ({
        key,
        label: `${labels[key]}: ${
          key === "dateFrom" || key === "dateTo" ? dateLabel(value) : valueLabels[value] || value
        }`,
      }))
  }, [filters])

  function clearApplicationFilter(key: keyof ApplicationFilters) {
    setFilters((current) => ({ ...current, [key]: "" }))
  }

  const filteredMeasures = React.useMemo(() => {
    const query = normalized(measureSearch)
    return measures.filter((measure) => {
      const docsCount = measure.documents?.length ?? 0
      const fieldsCount = measure.placeholders?.length ?? 0
      const statusLabel = measure.active !== false ? "Активна" : "Отключена"
      if (
        query &&
        !normalized([
          measure.title,
          measure.description,
          measure.category,
          statusLabel,
          `${docsCount} док`,
          `${fieldsCount} полей`,
        ].join(" ")).includes(query)
      ) return false
      if (measureFilters.status === "active" && measure.active === false) return false
      if (measureFilters.status === "inactive" && measure.active !== false) return false
      if (measureFilters.template === "yes" && !measure.has_template) return false
      if (measureFilters.template === "no" && measure.has_template) return false
      if (measureFilters.documents === "yes" && docsCount === 0) return false
      if (measureFilters.documents === "no" && docsCount > 0) return false
      return true
    })
  }, [measures, measureFilters, measureSearch])

  const activeMeasureFilterCount = Object.values(measureFilters).filter(Boolean).length
  const measureFilterTags = React.useMemo(() => {
    const labels: Record<keyof MeasureFilters, string> = {
      status: "Статус",
      template: "Шаблон",
      documents: "Документы",
    }
    const valueLabels: Record<string, string> = {
      active: "Активна",
      inactive: "Отключена",
      yes: "Есть",
      no: "Нет",
    }
    return (Object.entries(measureFilters) as [keyof MeasureFilters, string][])
      .filter(([, value]) => value)
      .map(([key, value]) => ({
        key,
        label: `${labels[key]}: ${valueLabels[value] || value}`,
      }))
  }, [measureFilters])

  function clearMeasureFilter(key: keyof MeasureFilters) {
    setMeasureFilters((current) => ({ ...current, [key]: "" }))
  }

  const decided = selected?.status !== "submitted"

  return (
    <>
      <Tabs value={tab} onValueChange={setTab}>
        {isAdmin ? (
          <TabsList className="mb-4">
            <TabsTrigger value="list">Поданные заявления</TabsTrigger>
            <TabsTrigger value="measures">Доступные меры поддержки</TabsTrigger>
          </TabsList>
        ) : null}

        <TabsContent value="list">
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
                placeholder="Поиск по мере, заявителю, основанию или статусу…"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <Button variant="outline" size="sm" asChild>
              <a href={exportUrl("/export/applications")} download>
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
              onClick={() => void loadApplications()}
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
                    onClick={() => clearApplicationFilter(tag.key)}
                  >
                    <X className="h-3 w-3" />
                  </button>
                </Badge>
              ))}
              <Button
                variant="ghost"
                size="sm"
                className="ml-auto h-7 px-2 text-xs"
                onClick={() => setFilters(EMPTY_APPLICATION_FILTERS)}
              >
                Сбросить
              </Button>
            </div>
          ) : null}

          <Dialog open={filterModalOpen} onOpenChange={setFilterModalOpen}>
            <DialogContent className="max-w-2xl">
              <DialogHeader>
                <DialogTitle>Фильтры заявлений</DialogTitle>
                <DialogDescription>
                  Отфильтруйте заявления по значениям колонок таблицы.
                </DialogDescription>
              </DialogHeader>
              <div className="grid gap-4 p-1 sm:grid-cols-2">
                <ApplicationFilterSelect
                  label="Мера поддержки"
                  value={filters.measure}
                  options={[["", "Все"], ...filterOptions.measures.map((measure) => [measure, measure])]}
                  onChange={(measure) => setFilters((current) => ({ ...current, measure }))}
                />
                <ApplicationFilterSelect
                  label="Основание"
                  value={filters.basis}
                  options={[["", "Все"], ...filterOptions.bases.map((basis) => [basis, basis])]}
                  onChange={(basis) => setFilters((current) => ({ ...current, basis }))}
                />
                <FormField>
                  <Label htmlFor="application-date-from" className="text-xs">Дата поступления — с</Label>
                  <Input
                    id="application-date-from"
                    className="h-9"
                    type="date"
                    value={filters.dateFrom}
                    onChange={(event) => setFilters((current) => ({ ...current, dateFrom: event.target.value }))}
                  />
                </FormField>
                <FormField>
                  <Label htmlFor="application-date-to" className="text-xs">Дата поступления — по</Label>
                  <Input
                    id="application-date-to"
                    className="h-9"
                    type="date"
                    value={filters.dateTo}
                    onChange={(event) => setFilters((current) => ({ ...current, dateTo: event.target.value }))}
                  />
                </FormField>
                <ApplicationFilterSelect
                  label="Документы"
                  value={filters.documents}
                  options={[
                    ["", "Все"],
                    ["complete", "Полный пакет"],
                    ["incomplete", "Требуется уточнение"],
                    ["fields", "Заполненные поля"],
                  ]}
                  onChange={(documents) => setFilters((current) => ({ ...current, documents }))}
                />
                <ApplicationFilterSelect
                  label="Статус"
                  value={filters.status}
                  options={[
                    ["", "Все"],
                    ["submitted", "На рассмотрении"],
                    ["approved", "Одобрено"],
                    ["rejected", "Отклонено"],
                  ]}
                  onChange={(status) => setFilters((current) => ({ ...current, status }))}
                />
              </div>
              <DialogFooter className="gap-2 sm:justify-between">
                <Button
                  variant="ghost"
                  disabled={activeFilterCount === 0}
                  onClick={() => setFilters(EMPTY_APPLICATION_FILTERS)}
                >
                  Сбросить
                </Button>
                <Button onClick={() => setFilterModalOpen(false)}>
                  Показать {filteredApplications.length}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>

          {loading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : filteredApplications.length === 0 ? (
            <EmptyState
              title={applications.length === 0 ? "Заявлений пока нет" : "Заявления не найдены"}
              description={
                applications.length === 0
                  ? "Гражданин оформляет меру поддержки в боте MAX — после подтверждения заявление появится здесь."
                  : "Измените поисковый запрос или параметры фильтра."
              }
            />
          ) : (
            <div className="rounded-xl border bg-card">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Мера поддержки</TableHead>
                    <TableHead>
                      <button
                        type="button"
                        className="-mx-1 flex w-full items-center justify-between gap-1 rounded-md px-1 py-0.5 text-left hover:bg-muted/60"
                        title={
                          sortKey === "date" && sortDir === "desc"
                            ? "Сначала новые — нажмите для старых"
                            : "Сначала старые — нажмите для новых"
                        }
                        aria-label={
                          sortKey === "date" && sortDir === "asc"
                            ? "Сортировка: сначала старые"
                            : "Сортировка: сначала новые"
                        }
                        onClick={() => toggleSort("date")}
                      >
                        <span>Поступило</span>
                        {sortKey === "date" && sortDir === "asc" ? (
                          <ArrowUp className="h-3.5 w-3.5 shrink-0 text-foreground" />
                        ) : (
                          <ArrowDown
                            className={cn(
                              "h-3.5 w-3.5 shrink-0",
                              sortKey === "date" ? "text-foreground" : "text-muted-foreground/50",
                            )}
                          />
                        )}
                      </button>
                    </TableHead>
                    <TableHead>Основание</TableHead>
                    <TableHead>Документы</TableHead>
                    <TableHead>Статус</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sortedApplications.map((a) => {
                    const st = APP_STATUS[a.status || ""] || { label: a.status_label || a.status || "—", variant: "outline" as const }
                    const who = a.applicant?.fio || a.citizen?.name || "Заявитель"
                    const basis = a.is_measure ? "Мера поддержки" : a.category || "—"
                    return (
                      <TableRow key={a.id} className="cursor-pointer" onClick={() => openApplication(a)}>
                        <TableCell>
                          <div className="font-medium">{a.measure_title}</div>
                          <div className="text-xs text-muted-foreground">{who}</div>
                        </TableCell>
                        <TableCell>{a.created_human}</TableCell>
                        <TableCell>
                          <Badge variant="secondary">{basis}</Badge>
                        </TableCell>
                        <TableCell>
                          {a.is_measure ? (
                            <Badge variant="secondary">полей: {(a.measure_fields as unknown[])?.length ?? 0}</Badge>
                          ) : a.missing?.length ? (
                            <Badge variant="outline">уточнить: {a.missing.length}</Badge>
                          ) : (
                            <span className="text-muted-foreground">полный пакет</span>
                          )}
                        </TableCell>
                        <TableCell>
                          <Badge variant={st.variant} className={st.className}>{st.label}</Badge>
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </TabsContent>

        {isAdmin ? (
          <TabsContent value="measures">
            <PageActions>
              <Button
                variant="outline"
                size="icon"
                className="h-8 w-8 shrink-0"
                title="Фильтры"
                aria-label="Фильтры"
                onClick={() => setMeasureFilterModalOpen(true)}
              >
                <Filter className="h-4 w-4" />
              </Button>
              <div className="relative min-w-0 flex-1">
                <Search className="absolute left-2.5 top-2 h-4 w-4 text-muted-foreground" />
                <Input
                  className="h-8 pl-9"
                  placeholder="Поиск по названию, описанию или статусу…"
                  value={measureSearch}
                  onChange={(event) => setMeasureSearch(event.target.value)}
                />
              </div>
              <Button
                variant="outline"
                size="icon"
                className="h-8 w-8"
                title="Обновить"
                aria-label="Обновить"
                onClick={() => void loadMeasures()}
              >
                <RefreshCw className="h-4 w-4" />
              </Button>
              {meta?.kb_ready ? (
                <Button variant="outline" size="sm" onClick={() => void syncKb()}>
                  <BookOpen className="h-4 w-4" />
                  Синхронизировать с ИИ
                </Button>
              ) : null}
              <Button size="sm" onClick={() => openMeasureForm(null)}>
                Новая мера
                <Plus className="h-4 w-4" />
              </Button>
            </PageActions>

            {measureFilterTags.length > 0 ? (
              <div className="mb-4 flex flex-wrap items-center gap-2">
                {measureFilterTags.map((tag) => (
                  <Badge key={tag.key} variant="secondary" className="gap-1 pr-1 font-normal">
                    {tag.label}
                    <button
                      type="button"
                      className="rounded-sm p-0.5 hover:bg-muted-foreground/20"
                      aria-label={`Убрать фильтр: ${tag.label}`}
                      onClick={() => clearMeasureFilter(tag.key)}
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </Badge>
                ))}
                <Button
                  variant="ghost"
                  size="sm"
                  className="ml-auto h-7 px-2 text-xs"
                  onClick={() => setMeasureFilters(EMPTY_MEASURE_FILTERS)}
                >
                  Сбросить
                </Button>
              </div>
            ) : null}

            <Dialog open={measureFilterModalOpen} onOpenChange={setMeasureFilterModalOpen}>
              <DialogContent className="max-w-2xl">
                <DialogHeader>
                  <DialogTitle>Фильтры мер поддержки</DialogTitle>
                  <DialogDescription>
                    Отфильтруйте меры по значениям колонок таблицы.
                  </DialogDescription>
                </DialogHeader>
                <div className="grid gap-4 p-1 sm:grid-cols-2">
                  <ApplicationFilterSelect
                    label="Статус"
                    value={measureFilters.status}
                    options={[
                      ["", "Все"],
                      ["active", "Активна"],
                      ["inactive", "Отключена"],
                    ]}
                    onChange={(status) => setMeasureFilters((current) => ({ ...current, status }))}
                  />
                  <ApplicationFilterSelect
                    label="Шаблон"
                    value={measureFilters.template}
                    options={[
                      ["", "Все"],
                      ["yes", "Есть"],
                      ["no", "Нет"],
                    ]}
                    onChange={(template) => setMeasureFilters((current) => ({ ...current, template }))}
                  />
                  <ApplicationFilterSelect
                    label="Документы"
                    value={measureFilters.documents}
                    options={[
                      ["", "Все"],
                      ["yes", "Есть"],
                      ["no", "Нет"],
                    ]}
                    onChange={(documents) => setMeasureFilters((current) => ({ ...current, documents }))}
                  />
                </div>
                <DialogFooter className="gap-2 sm:justify-between">
                  <Button
                    variant="ghost"
                    disabled={activeMeasureFilterCount === 0}
                    onClick={() => setMeasureFilters(EMPTY_MEASURE_FILTERS)}
                  >
                    Сбросить
                  </Button>
                  <Button onClick={() => setMeasureFilterModalOpen(false)}>
                    Показать {filteredMeasures.length}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>

            {measures.length === 0 ? (
              <EmptyState
                title="Мер поддержки пока нет"
                description="Создайте первую меру — она появится в боте MAX и станет доступна ИИ."
              />
            ) : filteredMeasures.length === 0 ? (
              <EmptyState
                title="Меры не найдены"
                description="Измените поисковый запрос или параметры фильтра."
              />
            ) : (
              <div className="rounded-xl border bg-card">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Мера поддержки</TableHead>
                      <TableHead>Описание</TableHead>
                      <TableHead>Статус</TableHead>
                      <TableHead className="w-12" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filteredMeasures.map((m) => (
                      <TableRow key={m.id}>
                        <TableCell>
                          <div className="font-medium">{m.title}</div>
                          <div className="text-xs text-muted-foreground">
                            {(m.documents?.length ?? 0)} док. · {(m.placeholders?.length ?? 0)} полей
                            {m.has_template ? " · шаблон ✓" : ""}
                          </div>
                        </TableCell>
                        <TableCell className="max-w-md truncate">{m.description || "—"}</TableCell>
                        <TableCell>
                          <Badge variant={m.active !== false ? "default" : "secondary"}>
                            {m.active !== false ? "Активна" : "Отключена"}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8"
                                title="Действия"
                                aria-label="Действия"
                              >
                                <MoreVertical className="h-4 w-4" />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <DropdownMenuItem onClick={() => openMeasureForm(m)}>
                                <Pencil className="h-4 w-4" />
                                Редактировать
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                className="text-destructive focus:text-destructive"
                                onClick={() => void deleteMeasure(m)}
                              >
                                <Trash2 className="h-4 w-4" />
                                Удалить
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </TabsContent>
        ) : null}
      </Tabs>

      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent className="w-full sm:max-w-xl">
          {selected ? (
            <>
              <SheetHeader>
                <SheetTitle className="flex items-center gap-2 pr-8">
                  Заявление #{selected.id}
                  <Badge
                    variant={APP_STATUS[selected.status || ""]?.variant || "outline"}
                    className={cn("font-normal", APP_STATUS[selected.status || ""]?.className)}
                  >
                    {APP_STATUS[selected.status || ""]?.label || selected.status_label || selected.status || "—"}
                  </Badge>
                </SheetTitle>
                <SheetDescription asChild>
                  <div className="flex flex-wrap gap-1.5">
                    {selected.measure_title ? (
                      <Badge variant="secondary" className="font-normal">
                        {selected.measure_title}
                      </Badge>
                    ) : null}
                    {selected.created_human ? (
                      <Badge variant="secondary" className="font-normal">
                        {selected.created_human}
                      </Badge>
                    ) : null}
                  </div>
                </SheetDescription>
              </SheetHeader>
              <ScrollArea className="min-h-0 min-w-0 flex-1">
                <div className="max-w-full min-w-0 space-y-8 overflow-x-hidden px-6 pb-6 pt-6">
                  {selected.is_measure ? (
                    <>
                      <AppSection title="Заявитель">
                        <CitizenCard
                          name={selected.citizen?.name || selected.applicant?.fio}
                          username={selected.citizen?.username}
                        />
                      </AppSection>

                      {selected.measure_fields?.length ? (
                        <AppSection title="Данные заявления">
                          <div className="grid gap-5 sm:grid-cols-2">
                            {selected.measure_fields.map((f, i) => (
                              <DataField key={i} label={f.label} value={f.value} />
                            ))}
                          </div>
                        </AppSection>
                      ) : null}
                    </>
                  ) : (
                    <>
                      {selected.missing?.length ? (
                        <div className="rounded-lg border border-primary/40 bg-primary/10 p-3 text-base">
                          Требуется уточнить: {selected.missing.join(", ")}
                        </div>
                      ) : null}

                      <AppSection title="Заявитель">
                        <CitizenCard name={selected.applicant?.fio || selected.citizen?.name} />
                      </AppSection>

                      <AppSection title="Данные заявления">
                        <div className="grid gap-5 sm:grid-cols-2">
                          <DataField label="ФИО" value={selected.applicant?.fio} />
                          <DataField label="Дата рождения" value={selected.applicant?.birth_date} />
                          <DataField label="Серия паспорта" value={selected.applicant?.passport_series} />
                          <DataField label="Номер паспорта" value={selected.applicant?.passport_number} />
                          <DataField
                            label="Паспорт выдан"
                            value={selected.applicant?.passport_issued}
                            className="sm:col-span-2"
                          />
                          {selected.category ? (
                            <DataField
                              label="Категория"
                              value={selected.category}
                              className="sm:col-span-2"
                            />
                          ) : null}
                          <DataField label="Телефон" value={selected.applicant?.phone} />
                          {selected.payment?.bank ? (
                            <DataField label="Банк" value={selected.payment.bank} />
                          ) : null}
                          {selected.payment?.account ? (
                            <DataField
                              label="Счёт"
                              value={selected.payment.account}
                              className="sm:col-span-2"
                            />
                          ) : null}
                        </div>
                      </AppSection>

                      {(selected.applicant?.address ||
                        selected.ownership ||
                        (selected.rooms != null && selected.rooms !== "")) ? (
                        <AppSection title="Жилое помещение">
                          <div className="grid gap-5 sm:grid-cols-2">
                            {selected.applicant?.address ? (
                              <DataField
                                label="Адрес регистрации"
                                value={selected.applicant.address}
                                className="sm:col-span-2"
                              />
                            ) : null}
                            {selected.ownership ? (
                              <DataField
                                label="Собственность"
                                value={
                                  selected.ownership.charAt(0).toUpperCase() +
                                  selected.ownership.slice(1)
                                }
                              />
                            ) : null}
                            {selected.rooms != null && selected.rooms !== "" ? (
                              <DataField label="Комнат" value={String(selected.rooms)} />
                            ) : null}
                          </div>
                        </AppSection>
                      ) : null}

                      {selected.family?.length ? (
                        <AppSection title="Семья">
                          <div className="grid gap-5">
                            {selected.family.map((m, i) => (
                              <DataField
                                key={i}
                                label={
                                  m.relation
                                    ? m.relation.charAt(0).toUpperCase() + m.relation.slice(1)
                                    : "Родственник"
                                }
                                value={[m.fio, m.birth_date].filter(Boolean).join(", ")}
                              />
                            ))}
                          </div>
                        </AppSection>
                      ) : null}

                      {selected.providers?.length ? (
                        <AppSection title="Поставщики">
                          <div className="grid gap-5 sm:grid-cols-2">
                            {selected.providers.map((p, i) => (
                              <DataField
                                key={i}
                                label={p.name || "Поставщик"}
                                value={p.account || "—"}
                              />
                            ))}
                          </div>
                        </AppSection>
                      ) : null}
                    </>
                  )}

                  {(selected.documents?.length || 0) > 0 ? (
                    <AppSection title="Требуемые документы">
                      <ul className="list-disc space-y-2 pl-5 text-base">
                        {selected.documents!.map((d, i) => (
                          <li key={i}>{d}</li>
                        ))}
                      </ul>
                    </AppSection>
                  ) : null}

                  {(selected.user_files?.length || 0) > 0 ? (
                    <AppSection title="Загруженные документы">
                      <ul className="space-y-2">
                        {selected.user_files!.map((url, i) => (
                          <li key={i}>
                            <FileAttachment href={url} name={fileLabel(url, i)} />
                          </li>
                        ))}
                      </ul>
                    </AppSection>
                  ) : null}
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
                <Button size="sm" className="shrink-0" asChild>
                  <a href={exportUrl(`/applications/${selected.id}/docx`)} download>
                    Скачать
                  </a>
                </Button>
                {!decided ? (
                  <>
                    <Button
                      variant="outline"
                      size="sm"
                      className="shrink-0"
                      disabled={deciding}
                      onClick={() => void decide("reject")}
                    >
                      Отклонить
                    </Button>
                    <Button size="sm" className="shrink-0" disabled={deciding} onClick={() => void decide("approve")}>
                      {deciding ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                      Одобрить
                    </Button>
                  </>
                ) : null}
              </SheetFooter>
            </>
          ) : null}
        </SheetContent>
      </Sheet>

      <Dialog open={!!measureDialog} onOpenChange={() => setMeasureDialog(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{measureDialog && measureDialog !== "new" ? "Изменить меру поддержки" : "Новая мера поддержки"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <FormField>
              <Label>Название меры *</Label>
              <Input
                value={measureForm.title}
                onChange={(e) => setMeasureForm((f) => ({ ...f, title: e.target.value }))}
                placeholder="Единовременная выплата"
              />
            </FormField>
            <FormField>
              <Label>Описание</Label>
              <Textarea
                rows={2}
                value={measureForm.description}
                onChange={(e) => setMeasureForm((f) => ({ ...f, description: e.target.value }))}
              />
            </FormField>
            <FormField>
              <Label>Подсказка для ИИ</Label>
              <Textarea
                rows={2}
                value={measureForm.llm_hint}
                onChange={(e) => setMeasureForm((f) => ({ ...f, llm_hint: e.target.value }))}
              />
            </FormField>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={measureForm.active}
                onChange={(e) => setMeasureForm((f) => ({ ...f, active: e.target.checked }))}
              />
              Активна (видна гражданам в боте)
            </label>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setMeasureDialog(null)}>
              Отмена
            </Button>
            <Button onClick={() => void saveMeasure()}>
              {measureDialog && measureDialog !== "new" ? "Сохранить" : "Создать"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Удалить заявление?</DialogTitle>
            <DialogDescription>Заявление будет удалено из списка.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              Отмена
            </Button>
            <Button variant="destructive" onClick={() => void deleteApplication()}>
              Удалить
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

function AppSection({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <div>
      <div className="mb-4 text-base font-semibold leading-none tracking-tight">{title}</div>
      <div className="rounded-xl border bg-card p-4">{children}</div>
    </div>
  )
}

function CitizenCard({ name, username }: { name?: string | null; username?: string | null }) {
  return (
    <div>
      <div className="mb-1.5 text-sm font-medium leading-none text-muted-foreground">
        Гражданин
      </div>
      <div className="mt-1.5 text-base font-semibold">{name || "—"}</div>
      {username ? <div className="mt-1 text-sm text-muted-foreground">@{username}</div> : null}
    </div>
  )
}

function DataField({
  label,
  value,
  className,
}: {
  label: string
  value?: string | null
  className?: string
}) {
  return (
    <div className={className}>
      <div className="mb-1.5 text-sm font-medium leading-none text-muted-foreground">{label}</div>
      <div className="break-words text-base leading-snug">{value?.trim() || "—"}</div>
    </div>
  )
}

function fileLabel(url: string, index: number): string {
  try {
    const path = decodeURIComponent(url.split("?")[0] || "")
    const name = path.split("/").filter(Boolean).pop()
    if (name) return name
  } catch {
    /* ignore */
  }
  return `Документ ${index + 1}`
}

function PdfBadge({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 40 48"
      className={cn("h-5 w-4 shrink-0", className)}
      aria-hidden
    >
      <path
        d="M4 2h22l10 10v32a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z"
        fill="#E53935"
      />
      <path d="M26 2v8a2 2 0 0 0 2 2h8" fill="#FF8A80" />
      <text
        x="20"
        y="32"
        textAnchor="middle"
        fill="white"
        fontSize="11"
        fontWeight="700"
        fontFamily="system-ui, sans-serif"
      >
        PDF
      </text>
    </svg>
  )
}

function FileAttachment({ href, name }: { href: string; name: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="flex h-8 w-full items-center gap-2 rounded-lg border border-border bg-white px-2.5 text-sm text-foreground transition-colors hover:bg-muted/40"
    >
      <PdfBadge />
      <span className="min-w-0 truncate">{name}</span>
    </a>
  )
}

function ApplicationFilterSelect({
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
