import * as React from "react"
import { useParams } from "react-router-dom"
import {
  Award,
  Download,
  FileUp,
  Filter,
  Loader2,
  RefreshCw,
  Search,
  X,
} from "lucide-react"
import { toast } from "sonner"
import { api, API_BASE, exportUrl } from "@/lib/api"
import type { UsvoFilters, UsvoListItem } from "@/lib/types"
import { useApp } from "@/hooks/use-app"
import { PageActions } from "@/components/page-actions"
import { EmptyState } from "@/components/empty-state"
import { UsvoCardSheet } from "@/components/usvo-card-sheet"
import { UsvoStatusBadge } from "@/components/usvo-status-badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
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
import { cn, plural } from "@/lib/utils"

const EMPTY_FILTERS: UsvoFilters = {
  query: "",
  status: "",
  vbd: "",
  employment: "",
  contact: "",
  org: "",
  awards: "",
  directive: "",
  source: "",
}

const TRI = [
  ["", "Все"],
  ["yes", "да"],
  ["no", "нет"],
] as const

function buildQuery(filters: UsvoFilters) {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(filters)) {
    if (v) p.set(k, v)
  }
  const qs = p.toString()
  return qs ? `?${qs}` : ""
}

export function CardsPage() {
  const { id: routeId } = useParams<{ id?: string }>()
  const { meta, refreshMeta } = useApp()
  const [filters, setFilters] = React.useState<UsvoFilters>(EMPTY_FILTERS)
  const [searchInput, setSearchInput] = React.useState("")
  const fileInputRef = React.useRef<HTMLInputElement>(null)
  const [filterModalOpen, setFilterModalOpen] = React.useState(false)
  const [items, setItems] = React.useState<UsvoListItem[]>([])
  const [loading, setLoading] = React.useState(true)
  const [selectedId, setSelectedId] = React.useState<number | null>(null)
  const [sheetOpen, setSheetOpen] = React.useState(false)
  const [uploadOpen, setUploadOpen] = React.useState(false)
  const [uploadFile, setUploadFile] = React.useState<File | null>(null)
  const [uploadReplace, setUploadReplace] = React.useState(false)
  const [uploading, setUploading] = React.useState(false)
  const [importResult, setImportResult] = React.useState<{
    saved: number
    skipped: number
    total_uploaded?: number
  } | null>(null)

  const loadList = React.useCallback(async () => {
    setLoading(true)
    try {
      const { items: list } = await api<{ items: UsvoListItem[] }>(`/usvo${buildQuery(filters)}`)
      setItems(list)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка загрузки")
    } finally {
      setLoading(false)
    }
  }, [filters])

  React.useEffect(() => {
    const t = window.setTimeout(() => {
      setFilters((f) => (f.query === searchInput ? f : { ...f, query: searchInput }))
    }, 250)
    return () => window.clearTimeout(t)
  }, [searchInput])

  React.useEffect(() => {
    void loadList()
  }, [loadList])

  function openCard(id: number) {
    setSelectedId(id)
    setSheetOpen(true)
  }

  React.useEffect(() => {
    if (routeId) {
      const n = parseInt(routeId, 10)
      if (Number.isFinite(n)) {
        setSelectedId(n)
        setSheetOpen(true)
      }
    }
  }, [routeId])

  const activeFilterCount = Object.entries(filters).filter(([k, v]) => k !== "query" && v).length

  const filterTags = React.useMemo(() => {
    const labels: Record<string, string> = {
      status: "Статус",
      vbd: "Ветеран БД",
      employment: "Нужна работа",
      contact: "Давно без связи",
      org: "В организациях",
      awards: "С наградами",
      directive: "Поручение Главы",
      source: "Источник",
    }
    const valueLabel = (key: string, v: string) => {
      if (key === "source") return v === "uploaded" ? "загружено" : v === "table" ? "таблица" : v
      if (v === "yes") return "да"
      if (v === "no") return "нет"
      return v
    }
    return (Object.entries(filters) as [keyof UsvoFilters, string][])
      .filter(([k, v]) => k !== "query" && v)
      .map(([k, v]) => ({
        key: k,
        label: `${labels[k] || k}: ${valueLabel(k, v)}`,
      }))
  }, [filters])

  function clearFilter(key: keyof UsvoFilters) {
    setFilters((f) => ({ ...f, [key]: "" }))
  }

  function resetFilters() {
    setSearchInput("")
    setFilters(EMPTY_FILTERS)
  }

  async function doUpload() {
    if (!uploadFile) return
    setUploading(true)
    try {
      const fd = new FormData()
      fd.append("file", uploadFile)
      const res = await fetch(`${API_BASE}/usvo/import?replace=${uploadReplace}`, {
        method: "POST",
        credentials: "same-origin",
        body: fd,
      })
      if (!res.ok) {
        let msg = `Ошибка ${res.status}`
        try {
          const data = (await res.json()) as { detail?: string }
          msg = data.detail || msg
        } catch {
          /* ignore */
        }
        throw new Error(msg)
      }
      const data = (await res.json()) as { saved: number; skipped: number; total_uploaded?: number }
      toast.success(`Загружено карточек: ${data.saved}`)
      setImportResult(data)
      setUploadOpen(false)
      setUploadFile(null)
      await refreshMeta()
      void loadList()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка импорта")
    } finally {
      setUploading(false)
    }
  }

  const statuses = meta?.usvo_statuses || []

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
            placeholder="Поиск по ФИО, телефону, статусу…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
        </div>
        <Button variant="outline" size="sm" className="shrink-0" onClick={() => setUploadOpen(true)}>
          Загрузить
          <FileUp className="h-4 w-4" />
        </Button>
        <Button variant="outline" size="sm" className="shrink-0" asChild>
          <a href={exportUrl(`/export/usvo${buildQuery(filters)}`)} download>
            Экспорт
            <Download className="h-4 w-4" />
          </a>
        </Button>
        <Button
          variant="outline"
          size="icon"
          className="h-8 w-8 shrink-0"
          title="Обновить"
          aria-label="Обновить"
          onClick={() => void loadList()}
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
                aria-label={`Сбросить: ${tag.label}`}
                onClick={() => clearFilter(tag.key)}
              >
                <X className="h-3 w-3" />
              </button>
            </Badge>
          ))}
          <span className="ml-auto text-sm text-muted-foreground">
            {items.length} {plural(items.length, "карточка", "карточки", "карточек")}
          </span>
          <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={resetFilters}>
            Сбросить
          </Button>
        </div>
      ) : (
        <div className="mb-4 text-right text-sm text-muted-foreground">
          {items.length} {plural(items.length, "карточка", "карточки", "карточек")}
        </div>
      )}

      <Dialog open={filterModalOpen} onOpenChange={setFilterModalOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Фильтры карточек</DialogTitle>
            <DialogDescription>Выберите параметры отбора. Изменения применяются сразу.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 sm:grid-cols-2">
            <FilterSelect
              label="Статус"
              value={filters.status}
              options={[["", "Все"], ...statuses.map((s) => [s, s])]}
              onChange={(v) => setFilters((f) => ({ ...f, status: v }))}
            />
            <FilterSelect label="Ветеран БД" value={filters.vbd} options={TRI} onChange={(v) => setFilters((f) => ({ ...f, vbd: v }))} />
            <FilterSelect label="Нужна работа" value={filters.employment} options={TRI} onChange={(v) => setFilters((f) => ({ ...f, employment: v }))} />
            <FilterSelect label="Давно без связи" value={filters.contact} options={TRI} onChange={(v) => setFilters((f) => ({ ...f, contact: v }))} />
            <FilterSelect label="В организациях" value={filters.org} options={TRI} onChange={(v) => setFilters((f) => ({ ...f, org: v }))} />
            <FilterSelect label="С наградами" value={filters.awards} options={TRI} onChange={(v) => setFilters((f) => ({ ...f, awards: v }))} />
            <FilterSelect label="Поручение Главы" value={filters.directive} options={TRI} onChange={(v) => setFilters((f) => ({ ...f, directive: v }))} />
            <FilterSelect
              label="Источник"
              value={filters.source}
              options={[
                ["", "Все"],
                ["uploaded", "загружено"],
                ["table", "таблица"],
              ]}
              onChange={(v) => setFilters((f) => ({ ...f, source: v }))}
            />
          </div>
          <DialogFooter className="gap-2 sm:justify-between">
            <Button
              variant="ghost"
              disabled={activeFilterCount === 0}
              onClick={() => {
                setFilters({ ...EMPTY_FILTERS, query: filters.query })
              }}
            >
              Сбросить
            </Button>
            <Button onClick={() => setFilterModalOpen(false)}>Готово</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {loading ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          title="Ничего не найдено"
          description="Уточните запрос или сбросьте фильтры — поиск идёт по ФИО, телефону, статусу и адресу."
        />
      ) : (
        <div className="rounded-xl border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Участник СВО</TableHead>
                <TableHead>Статус</TableHead>
                <TableHead>Телефон</TableHead>
                <TableHead>Обзвон</TableHead>
                <TableHead>Адрес</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((r) => (
                <TableRow
                  key={r.id}
                  className={cn("cursor-pointer", selectedId === r.id && "bg-muted/50")}
                  onClick={() => openCard(r.id)}
                >
                  <TableCell>
                    <div className="flex items-center gap-1.5">
                      <span className="font-medium">{r.name}</span>
                      {r.head_directive ? (
                        <span title="Поручение" className="inline-flex shrink-0 text-foreground">
                          <Award className="h-3.5 w-3.5" />
                        </span>
                      ) : null}
                    </div>
                    {r.source === "uploaded" ? (
                      <Badge variant="secondary" className="mt-1 text-xs">
                        загружено
                      </Badge>
                    ) : null}
                  </TableCell>
                  <TableCell>
                    <UsvoStatusBadge status={r.status} />
                  </TableCell>
                  <TableCell>{r.phone || "—"}</TableCell>
                  <TableCell>{r.call_date || "—"}</TableCell>
                  <TableCell className="max-w-[200px] truncate">{r.address || "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <UsvoCardSheet
        cardId={selectedId}
        open={sheetOpen}
        onOpenChange={(open) => {
          setSheetOpen(open)
          if (!open) setSelectedId(null)
        }}
        onSaved={() => void loadList()}
        onDeleted={() => {
          setSelectedId(null)
          void loadList()
        }}
      />

      <Dialog open={uploadOpen} onOpenChange={setUploadOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Загрузка карточек УСВО</DialogTitle>
            <DialogDescription>
              Загрузите Excel-таблицу с карточками участников СВО. Столбец «История взаимодействия» ИИ
              нормализует автоматически.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <button
              type="button"
              className="flex w-full cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed p-6 hover:bg-muted/50"
              onClick={() => fileInputRef.current?.click()}
            >
              <FileUp className="h-8 w-8 text-muted-foreground" />
              <span className="text-sm">{uploadFile?.name || "Нажмите, чтобы выбрать .xlsx"}</span>
              <input
                ref={fileInputRef}
                type="file"
                accept=".xlsx,.xls"
                className="hidden"
                onChange={(e) => setUploadFile(e.target.files?.[0] || null)}
              />
            </button>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={uploadReplace} onChange={(e) => setUploadReplace(e.target.checked)} />
              Заменить ранее загруженные карточки
            </label>
          </div>
          <DialogFooter>
            <Button variant="outline" asChild>
              <a href={exportUrl("/usvo/template")} download>
                Скачать пример таблицы
              </a>
            </Button>
            <Button disabled={!uploadFile || uploading} onClick={() => void doUpload()}>
              {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Загрузить
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!importResult} onOpenChange={() => setImportResult(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Импорт завершён</DialogTitle>
          </DialogHeader>
          {importResult ? (
            <div className="grid grid-cols-3 gap-4 text-center">
              <div>
                <div className="text-2xl font-bold text-emerald-600">{importResult.saved}</div>
                <div className="text-xs text-muted-foreground">импортировано</div>
              </div>
              <div>
                <div className="text-2xl font-bold">{importResult.skipped}</div>
                <div className="text-xs text-muted-foreground">пропущено дублей</div>
              </div>
              <div>
                <div className="text-2xl font-bold">{importResult.total_uploaded ?? "—"}</div>
                <div className="text-xs text-muted-foreground">всего в базе</div>
              </div>
            </div>
          ) : null}
          <DialogFooter>
            <Button onClick={() => setImportResult(null)}>Готово</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: readonly (readonly [string, string])[] | string[][]
  onChange: (v: string) => void
}) {
  return (
    <FormField>
      <Label className="text-xs">{label}</Label>
      <Select value={value || " "} onValueChange={(v) => onChange(v === " " ? "" : v)}>
        <SelectTrigger className="h-9">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map(([v, t]) => (
            <SelectItem key={v || "_"} value={v || " "}>
              {t}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </FormField>
  )
}
