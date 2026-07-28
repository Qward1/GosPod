import * as React from "react"
import { Filter, RefreshCw, Search, X } from "lucide-react"
import { toast } from "sonner"
import { api } from "@/lib/api"
import type { AuditItem } from "@/lib/types"
import { PageActions } from "@/components/page-actions"
import { EmptyState } from "@/components/empty-state"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Label } from "@/components/ui/label"
import { FormField } from "@/components/ui/form-field"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

const AUDIT_ACTIONS: Record<string, string> = {
  answer_appeal: "Ответ на обращение",
  delete_appeal: "Удаление обращения",
  update_usvo: "Правка карточки УСВО",
  delete_usvo: "Удаление карточки УСВО",
  clear_usvo: "Очистка карточек УСВО",
  import_usvo: "Импорт карточек УСВО",
  decide_application: "Решение по заявлению",
  delete_application: "Удаление заявления",
  broadcast: "Массовая рассылка",
  sla_update: "Изменение регламента",
}

const AUDIT_ENTITIES: Record<string, string> = {
  appeal: "Обращение",
  usvo_card: "Карточка УСВО",
  application: "Заявление",
  broadcast: "Рассылка",
  settings: "Настройки",
}

type AuditFilters = {
  action: string
  entity: string
}

const EMPTY_FILTERS: AuditFilters = { action: "", entity: "" }

function actionLabel(action: string) {
  return AUDIT_ACTIONS[action] || action
}

function entityLabel(entity?: string) {
  if (!entity) return "—"
  return AUDIT_ENTITIES[entity] || entity
}

function DateCell({ value }: { value?: string }) {
  const raw = (value || "").trim()
  if (!raw) return <>{"—"}</>
  const [date, ...rest] = raw.split(/\s+/)
  const time = rest.join(" ")
  return (
    <div className="leading-tight">
      <div>{date}</div>
      {time ? <div className="text-xs text-muted-foreground">{time}</div> : null}
    </div>
  )
}

export function AuditPage() {
  const [items, setItems] = React.useState<AuditItem[]>([])
  const [loading, setLoading] = React.useState(true)
  const [filters, setFilters] = React.useState<AuditFilters>(EMPTY_FILTERS)
  const [searchInput, setSearchInput] = React.useState("")
  const [search, setSearch] = React.useState("")
  const [filterModalOpen, setFilterModalOpen] = React.useState(false)

  React.useEffect(() => {
    const t = window.setTimeout(() => setSearch(searchInput.trim().toLowerCase()), 250)
    return () => window.clearTimeout(t)
  }, [searchInput])

  const load = React.useCallback(async () => {
    setLoading(true)
    try {
      const p = new URLSearchParams()
      if (filters.action) p.set("action", filters.action)
      if (filters.entity) p.set("entity", filters.entity)
      const qs = p.toString()
      const { items: list } = await api<{ items: AuditItem[] }>(`/audit${qs ? `?${qs}` : ""}`)
      setItems(list)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка загрузки")
    } finally {
      setLoading(false)
    }
  }, [filters])

  React.useEffect(() => {
    void load()
  }, [load])

  const filtered = React.useMemo(() => {
    if (!search) return items
    return items.filter((x) => {
      const hay = [
        x.at_human,
        x.at,
        x.user_name,
        x.user_sub,
        actionLabel(x.action),
        x.action,
        entityLabel(x.entity),
        x.entity,
        x.entity_id,
        x.details,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
      return hay.includes(search)
    })
  }, [items, search])

  const filterTags = React.useMemo(() => {
    const tags: { key: keyof AuditFilters; label: string }[] = []
    if (filters.action) tags.push({ key: "action", label: `Действие: ${actionLabel(filters.action)}` })
    if (filters.entity) tags.push({ key: "entity", label: `Объект: ${entityLabel(filters.entity)}` })
    return tags
  }, [filters])

  const activeFilterCount = filterTags.length

  function clearFilter(key: keyof AuditFilters) {
    setFilters((f) => ({ ...f, [key]: "" }))
  }

  function resetFilters() {
    setFilters(EMPTY_FILTERS)
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
            placeholder="Поиск по сотруднику, действию, объекту, деталям…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
        </div>
        <Button
          variant="outline"
          size="icon"
          className="h-8 w-8 shrink-0"
          title="Обновить"
          aria-label="Обновить"
          onClick={() => void load()}
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
          <Button variant="ghost" size="sm" className="ml-auto h-7 px-2 text-xs" onClick={resetFilters}>
            Сбросить
          </Button>
        </div>
      ) : null}

      <Dialog open={filterModalOpen} onOpenChange={setFilterModalOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Фильтры журнала</DialogTitle>
            <DialogDescription>Выберите параметры отбора. Изменения применяются сразу.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <FilterSelect
              label="Действие"
              value={filters.action}
              options={[
                ["", "Все"],
                ...Object.entries(AUDIT_ACTIONS).map(([v, t]) => [v, t] as const),
              ]}
              onChange={(v) => setFilters((f) => ({ ...f, action: v }))}
            />
            <FilterSelect
              label="Тип объекта"
              value={filters.entity}
              options={[
                ["", "Все"],
                ...Object.entries(AUDIT_ENTITIES).map(([v, t]) => [v, t] as const),
              ]}
              onChange={(v) => setFilters((f) => ({ ...f, entity: v }))}
            />
          </div>
          <DialogFooter className="gap-2 sm:justify-between">
            <Button variant="ghost" disabled={activeFilterCount === 0} onClick={resetFilters}>
              Сбросить
            </Button>
            <Button onClick={() => setFilterModalOpen(false)}>Готово</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {loading ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState
          title={items.length === 0 ? "Журнал пуст" : "Ничего не найдено"}
          description={
            items.length === 0
              ? "Действия операторов (ответы, правки, удаления, рассылки) появятся здесь."
              : "Измените поиск или сбросьте фильтры."
          }
        />
      ) : (
        <div className="w-full rounded-xl border bg-card">
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[14%]">Дата</TableHead>
                <TableHead className="w-[18%]">Пользователь</TableHead>
                <TableHead className="w-[24%]">Действие</TableHead>
                <TableHead className="w-[18%]">Объект</TableHead>
                <TableHead className="w-[26%]">Детали</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((x) => (
                <TableRow key={x.id}>
                  <TableCell>
                    <DateCell value={x.at_human || x.at} />
                  </TableCell>
                  <TableCell className="truncate" title={x.user_name || x.user_sub || undefined}>
                    {x.user_name || x.user_sub || "—"}
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary" className="max-w-full truncate font-normal">
                      {actionLabel(x.action)}
                    </Badge>
                  </TableCell>
                  <TableCell className="truncate" title={`${entityLabel(x.entity)}${x.entity_id ? ` #${x.entity_id}` : ""}`}>
                    {entityLabel(x.entity)}
                    {x.entity_id ? ` #${x.entity_id}` : ""}
                  </TableCell>
                  <TableCell className="truncate text-muted-foreground" title={x.details || undefined}>
                    {x.details || "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
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
  options: readonly (readonly [string, string])[]
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
