import * as React from "react"
import { Check, ChevronDown, Loader2, MapPin, Pencil, Plus, Trash2, Award } from "lucide-react"
import { toast } from "sonner"
import { api, exportUrl } from "@/lib/api"
import type { Field, HistoryEvent, UsvoCard, UsvoSuggestion } from "@/lib/types"
import { useApp } from "@/hooks/use-app"
import { ageFrom, yearsWord, cn } from "@/lib/utils"
import { resolveMedals } from "@/lib/medals"
import { groupUsvoFields, type EditableField } from "@/lib/usvo-field-groups"
import { Button } from "@/components/ui/button"
import { AwardsBlock } from "@/components/awards-block"
import { InteractionTimeline } from "@/components/interaction-timeline"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { UsvoStatusBadge } from "@/components/usvo-status-badge"
import { Label } from "@/components/ui/label"
import { FormField } from "@/components/ui/form-field"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { Skeleton } from "@/components/ui/skeleton"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Card, CardContent } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

function canonFields(card: UsvoCard): Field[] {
  const all = [...(card.primary || []), ...(card.secondary || []), ...(card.extra || [])]
  const seen = new Set<string>()
  const fields: Field[] = []
  for (const f of all) {
    const key = (f.label || "").toLowerCase().trim()
    if (!key || seen.has(key)) continue
    seen.add(key)
    fields.push({ label: f.label || "", value: f.value || "" })
  }
  return fields
}

function awardsCount(awards: unknown): number {
  return resolveMedals(awards).length
}

function StatCard({
  value,
  label,
  tone = "default",
}: {
  value: React.ReactNode
  label: string
  tone?: "default" | "success" | "warning"
}) {
  return (
    <Card className="h-full">
      <CardContent className="flex h-full flex-col pt-6">
        <div
          className={cn(
            "flex items-center text-3xl font-bold tracking-tight",
            tone === "default" && "text-primary",
            tone === "success" && "text-emerald-600 dark:text-emerald-400",
            tone === "warning" && "text-amber-600 dark:text-amber-400",
          )}
        >
          {value}
        </div>
        <div className="mt-1 flex-1 text-sm text-muted-foreground">{label}</div>
      </CardContent>
    </Card>
  )
}

function AddressMapCard({ address }: { address: string }) {
  const q = encodeURIComponent(address)
  const ya = `https://yandex.ru/maps/?text=${q}`
  const gis = `https://2gis.ru/search/${q}`

  return (
    <div>
      <a
        href={ya}
        target="_blank"
        rel="noopener noreferrer"
        className="block overflow-hidden rounded-xl border bg-card text-card-foreground transition-shadow hover:shadow-sm"
      >
        <div className="address-map-canvas relative h-[130px]">
          <span className="address-map-pin absolute left-1/2 top-[46%] -translate-x-1/2 -translate-y-full text-red-500">
            <MapPin className="h-8 w-8" fill="currentColor" strokeWidth={1.5} />
          </span>
        </div>
        <div className="flex items-center gap-2.5 bg-muted/40 px-3.5 py-2.5">
          <MapPin className="h-4 w-4 shrink-0 text-primary" />
          <span className="min-w-0 flex-1 truncate text-sm">{address}</span>
        </div>
      </a>
      <div className="mt-2.5 flex gap-2.5">
        <Button variant="outline" className="flex-1" asChild>
          <a href={ya} target="_blank" rel="noopener noreferrer">
            Яндекс.Карты
          </a>
        </Button>
        <Button variant="outline" className="flex-1" asChild>
          <a href={gis} target="_blank" rel="noopener noreferrer">
            2ГИС
          </a>
        </Button>
      </div>
    </div>
  )
}

type UsvoCardSheetProps = {
  cardId: number | null
  open: boolean
  onOpenChange: (open: boolean) => void
  nested?: boolean
  onSaved?: () => void
  onDeleted?: () => void
}

export function UsvoCardSheet({
  cardId,
  open,
  onOpenChange,
  nested = false,
  onSaved,
  onDeleted,
}: UsvoCardSheetProps) {
  const { refreshMeta } = useApp()
  const [card, setCard] = React.useState<UsvoCard | null>(null)
  const [loading, setLoading] = React.useState(false)
  const [editFields, setEditFields] = React.useState<EditableField[]>([])
  const [historyRaw, setHistoryRaw] = React.useState("")
  const [historyTouched, setHistoryTouched] = React.useState(false)
  const [historyEditOpen, setHistoryEditOpen] = React.useState(false)
  const [dirty, setDirty] = React.useState(false)
  const [saving, setSaving] = React.useState(false)
  const [deleting, setDeleting] = React.useState(false)
  const [confirmDelete, setConfirmDelete] = React.useState(false)
  const [openGroups, setOpenGroups] = React.useState<Record<string, boolean>>({})
  const [suggestions, setSuggestions] = React.useState<UsvoSuggestion[]>([])
  const [aiNote, setAiNote] = React.useState<string | null>(null)
  const [suggLoading, setSuggLoading] = React.useState(false)
  const [showAllSuggestions, setShowAllSuggestions] = React.useState(false)
  const [showAllHistory, setShowAllHistory] = React.useState(false)

  const fieldGroups = React.useMemo(() => groupUsvoFields(editFields), [editFields])

  const sortedSuggestions = React.useMemo(() => {
    const rank = (p?: string) => (p === "high" ? 0 : p === "medium" ? 1 : 2)
    return [...suggestions].sort((a, b) => rank(a.priority) - rank(b.priority))
  }, [suggestions])

  const visibleSuggestions = showAllSuggestions
    ? sortedSuggestions
    : sortedSuggestions.slice(0, 2)
  const hiddenSuggestionsCount = Math.max(0, sortedSuggestions.length - 2)

  const loadCard = React.useCallback(async (id: number) => {
    setLoading(true)
    setSuggestions([])
    setAiNote(null)
    setDirty(false)
    setOpenGroups({})
    setShowAllSuggestions(false)
    setShowAllHistory(false)
    setHistoryEditOpen(false)
    try {
      const r = await api<UsvoCard>(`/usvo/${id}`)
      setCard(r)
      const fields = canonFields(r)
      setEditFields(fields)
      const groups = groupUsvoFields(fields)
      if (groups[0]) setOpenGroups({ [groups[0].title]: true })
      setHistoryRaw((r.history_raw as string) || "")
      setHistoryTouched(false)
      setSuggLoading(true)
      try {
        const res = await api<{ items?: UsvoSuggestion[]; ai_note?: string }>(`/usvo/${id}/suggestions`)
        setSuggestions(res.items || [])
        setAiNote(res.ai_note || null)
      } catch {
        /* ignore */
      } finally {
        setSuggLoading(false)
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Карточка не найдена")
      onOpenChange(false)
      setCard(null)
    } finally {
      setLoading(false)
    }
  }, [onOpenChange])

  React.useEffect(() => {
    if (open && cardId != null) {
      void loadCard(cardId)
    }
    if (!open) {
      setCard(null)
      setConfirmDelete(false)
    }
  }, [open, cardId, loadCard])

  async function saveCard() {
    if (cardId == null) return
    const out = editFields
      .filter((f) => f.label.trim() && f.value.trim())
      .map(({ label, value }) => ({ label, value }))
    if (!out.length) {
      toast.error("Карточка не может быть пустой")
      return
    }
    setSaving(true)
    try {
      const body: { fields: Field[]; history_raw?: string } = { fields: out }
      if (historyTouched) body.history_raw = historyRaw
      await api(`/usvo/${cardId}`, { method: "PUT", body: JSON.stringify(body) })
      toast.success("Карточка сохранена")
      await refreshMeta()
      onSaved?.()
      void loadCard(cardId)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка сохранения")
    } finally {
      setSaving(false)
    }
  }

  async function deleteCard() {
    if (cardId == null) return
    setDeleting(true)
    try {
      await api(`/usvo/${cardId}`, { method: "DELETE" })
      toast.success("Карточка удалена")
      setConfirmDelete(false)
      onOpenChange(false)
      await refreshMeta()
      onDeleted?.()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка удаления")
    } finally {
      setDeleting(false)
    }
  }

  const age = card ? ageFrom((card.birth_date as string) || card.birth) : null
  const flags = (card?.flags || {}) as Record<string, boolean | string>
  const inOrgs = Boolean(flags.org_vremya || flags.org_geroi_mo || flags.org_assoc)
  const needsWork = Boolean(flags.unemployed)
  const hasVbd = Boolean(flags.vbd)
  const contactOk = !flags.stale_contact
  const awards = awardsCount(card?.awards)

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent nested={nested} className="w-full sm:max-w-2xl">
          {loading ? (
            <div className="flex flex-1 items-center justify-center p-8">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          ) : card ? (
            <>
              <SheetHeader>
                <SheetTitle className="flex items-center gap-2 pr-6">
                  <span className="min-w-0 truncate">{card.name}</span>
                  {card.head_directive ? (
                    <Badge variant="outline" className="shrink-0 font-normal">
                      <Award className="mr-1 h-3 w-3" />
                      Поручение
                    </Badge>
                  ) : null}
                </SheetTitle>
                <SheetDescription className="flex flex-wrap items-center gap-1.5">
                  <UsvoStatusBadge status={card.status} />
                  <Badge variant="secondary" className="font-normal">
                    обзвон {card.call_date || "—"}
                  </Badge>
                </SheetDescription>
              </SheetHeader>

              <ScrollArea className="min-h-0 min-w-0 flex-1">
                <div className="max-w-full min-w-0 space-y-4 overflow-x-hidden px-6 pb-6 pt-6">
                  {(card.phone || card.birth_date) ? (
                    <div className="grid gap-3 sm:grid-cols-2">
                      {card.phone ? (
                        <div>
                          <div className="mb-1 text-sm font-medium leading-none">Телефон</div>
                          <div className="text-sm">{card.phone}</div>
                        </div>
                      ) : null}
                      {card.birth_date ? (
                        <div>
                          <div className="mb-1 text-sm font-medium leading-none">Дата рождения</div>
                          <div className="text-sm">{String(card.birth_date)}</div>
                        </div>
                      ) : null}
                    </div>
                  ) : null}

                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                    <StatCard
                      value={age != null ? age : "—"}
                      label={age != null ? yearsWord(age) : "лет"}
                    />
                    <StatCard
                      value={awards}
                      label="наград"
                    />
                    <StatCard
                      value={hasVbd ? <Check className="h-8 w-8" strokeWidth={2.5} /> : "—"}
                      label={hasVbd ? "статус ВБД" : "ВБД не оформлен"}
                      tone={hasVbd ? "success" : "warning"}
                    />
                    <StatCard
                      value={needsWork ? "!" : <Check className="h-8 w-8" strokeWidth={2.5} />}
                      label={needsWork ? "нужна работа" : "трудоустроен"}
                      tone={needsWork ? "warning" : "success"}
                    />
                    <StatCard
                      value={inOrgs ? <Check className="h-8 w-8" strokeWidth={2.5} /> : "—"}
                      label={inOrgs ? "в организациях" : "вне организаций"}
                      tone={inOrgs ? "success" : "default"}
                    />
                    <StatCard
                      value={contactOk ? <Check className="h-8 w-8" strokeWidth={2.5} /> : "!"}
                      label={contactOk ? "связь актуальна" : "давно без связи"}
                      tone={contactOk ? "success" : "warning"}
                    />
                  </div>

                  <AwardsBlock awards={card.awards} />

                  <div>
                    <div className="mb-2">
                      <span className="text-sm font-medium leading-none">Предложения ИИ по направлениям</span>
                    </div>
                    {suggLoading ? (
                      <div className="space-y-2">
                        <Skeleton className="h-16 w-full" />
                        <Skeleton className="h-16 w-full" />
                      </div>
                    ) : suggestions.length ? (
                      <div className="space-y-2">
                        {visibleSuggestions.map((s, i) => (
                          <div key={`${s.title}-${i}`} className="min-w-0 rounded-lg border p-3 text-sm">
                            <div className="flex items-start justify-between gap-2">
                              <span className="min-w-0 break-words font-bold">{s.title}</span>
                              <Badge
                                variant="outline"
                                className={cn(
                                  "shrink-0",
                                  s.priority === "high" &&
                                    "border-transparent bg-primary text-primary-foreground",
                                  s.priority === "medium" &&
                                    "border-primary/25 bg-primary/20 text-primary",
                                  s.priority !== "high" &&
                                    s.priority !== "medium" &&
                                    "border-primary/10 bg-primary/5 text-primary/60",
                                )}
                              >
                                {s.priority === "high"
                                  ? "Высокий"
                                  : s.priority === "medium"
                                    ? "Средний"
                                    : "Низкий"}
                              </Badge>
                            </div>
                            {s.detail ? <p className="mt-1 break-words text-muted-foreground">{s.detail}</p> : null}
                            {s.action ? (
                              <p className="mt-1 break-words">
                                <b>Следующий шаг:</b> {s.action}
                              </p>
                            ) : null}
                          </div>
                        ))}
                        {hiddenSuggestionsCount > 0 ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="w-full"
                            onClick={() => setShowAllSuggestions((v) => !v)}
                          >
                            {showAllSuggestions
                              ? "Скрыть"
                              : "Показать все"}
                          </Button>
                        ) : null}
                        {aiNote ? (
                          <p className="rounded-lg bg-muted/50 p-2 text-xs text-muted-foreground">{aiNote}</p>
                        ) : null}
                      </div>
                    ) : (
                      <p className="text-sm text-muted-foreground">Нет предложений</p>
                    )}
                  </div>

                  {card.address ? <AddressMapCard address={card.address} /> : null}

                  <Separator />

                  <div>
                    <div className="mb-2 flex items-center justify-between gap-3">
                      <span className="text-sm font-medium leading-none">Данные участника</span>
                      <label className="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
                        <Switch
                          checked={fieldGroups.every((g) => openGroups[g.title])}
                          onCheckedChange={(checked) => {
                            const next: Record<string, boolean> = {}
                            for (const g of fieldGroups) next[g.title] = checked
                            setOpenGroups(next)
                          }}
                        />
                        <span>
                          {fieldGroups.every((g) => openGroups[g.title])
                            ? "скрыть все"
                            : "раскрыть все"}
                        </span>
                      </label>
                    </div>
                    <div className="space-y-2">
                      {fieldGroups.map((g) => {
                        const Icon = g.icon
                        const isOpen = Boolean(openGroups[g.title])
                        return (
                          <div key={g.title} className="overflow-hidden rounded-xl border bg-card">
                            <button
                              type="button"
                              className="flex w-full items-center gap-2.5 px-3.5 py-3 text-left transition-colors hover:bg-muted/40"
                              onClick={() =>
                                setOpenGroups((o) => ({ ...o, [g.title]: !o[g.title] }))
                              }
                            >
                              <Icon className="h-[18px] w-[18px] shrink-0 text-primary" />
                              <span className="min-w-0 flex-1 text-sm font-medium">{g.title}</span>
                              <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-bold tabular-nums text-muted-foreground">
                                {g.items.length}
                              </span>
                              <ChevronDown
                                className={cn(
                                  "h-4 w-4 shrink-0 text-muted-foreground transition-transform",
                                  isOpen && "rotate-180",
                                )}
                              />
                            </button>
                            {isOpen ? (
                              <div className="space-y-2 border-t px-3.5 py-3">
                                {g.items.length ? (
                                  g.items.map(({ field: f, index: i }) => (
                                    <div
                                      key={i}
                                      className="grid min-w-0 grid-cols-[1fr_1fr_auto] gap-2"
                                    >
                                      <Input
                                        className="min-w-0"
                                        placeholder="Название поля"
                                        value={f.label}
                                        onChange={(e) => {
                                          const next = [...editFields]
                                          next[i] = {
                                            ...next[i],
                                            label: e.target.value,
                                            pinnedGroup: e.target.value.trim()
                                              ? undefined
                                              : next[i].pinnedGroup,
                                          }
                                          setEditFields(next)
                                          setDirty(true)
                                        }}
                                      />
                                      <Input
                                        className="min-w-0"
                                        placeholder="Значение"
                                        value={f.value}
                                        onChange={(e) => {
                                          const next = [...editFields]
                                          next[i] = { ...next[i], value: e.target.value }
                                          setEditFields(next)
                                          setDirty(true)
                                        }}
                                      />
                                      <Button
                                        variant="ghost"
                                        size="icon"
                                        className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                                        onClick={() => {
                                          setEditFields(editFields.filter((_, j) => j !== i))
                                          setDirty(true)
                                        }}
                                      >
                                        <Trash2 className="h-4 w-4" />
                                      </Button>
                                    </div>
                                  ))
                                ) : (
                                  <p className="text-sm text-muted-foreground">Нет полей</p>
                                )}
                                <Button
                                  variant="outline"
                                  size="sm"
                                  className="w-full"
                                  onClick={() => {
                                    setEditFields((f) => [
                                      ...f,
                                      { label: "", value: "", pinnedGroup: g.title },
                                    ])
                                    setDirty(true)
                                  }}
                                >
                                  <Plus className="h-4 w-4" />
                                  Поле
                                </Button>
                              </div>
                            ) : null}
                          </div>
                        )
                      })}
                    </div>
                  </div>

                  <div>
                    <div className="mb-3 flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium leading-none">История взаимодействия</span>
                      {(card.history || []).length ? (
                        <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-bold tabular-nums text-muted-foreground">
                          {(card.history || []).length}
                        </span>
                      ) : null}
                      <Button
                        variant="ghost"
                        size="sm"
                        className="ml-auto"
                        onClick={() => setHistoryEditOpen((v) => !v)}
                      >
                        <Pencil className="h-3.5 w-3.5" />
                        {historyEditOpen ? "Скрыть текст" : "Изменить текст"}
                      </Button>
                    </div>
                    {historyEditOpen ? (
                      <FormField className="mb-3">
                        <Label>Свободный текст</Label>
                        <p className="mb-2 text-xs text-muted-foreground">
                          ИИ оформит текст в события ленты. Изменения применятся при сохранении карточки.
                        </p>
                        <Textarea
                          rows={4}
                          value={historyRaw}
                          onChange={(e) => {
                            setHistoryRaw(e.target.value)
                            setHistoryTouched(true)
                            setDirty(true)
                          }}
                          placeholder="Хронология взаимодействия…"
                        />
                      </FormField>
                    ) : null}
                    {(() => {
                      const historyEvents = (card.history as HistoryEvent[]) || []
                      const visibleHistory = showAllHistory
                        ? historyEvents
                        : historyEvents.slice(0, 3)
                      const hasMoreHistory = historyEvents.length > 3
                      return (
                        <div className="space-y-2">
                          <InteractionTimeline events={visibleHistory} />
                          {hasMoreHistory ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              className="w-full"
                              onClick={() => setShowAllHistory((v) => !v)}
                            >
                              {showAllHistory ? "Скрыть" : "Показать все"}
                            </Button>
                          ) : null}
                        </div>
                      )
                    })()}
                  </div>
                </div>
              </ScrollArea>

              <SheetFooter className="flex-col gap-2 sm:flex-row sm:justify-between">
                {card.source === "uploaded" ? (
                  <Button
                    variant="outline"
                    size="sm"
                    className="border-destructive text-destructive hover:bg-destructive/10 hover:text-destructive"
                    onClick={() => setConfirmDelete(true)}
                  >
                    <Trash2 className="h-4 w-4" />
                    Удалить
                  </Button>
                ) : (
                  <span />
                )}
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" asChild>
                    <a href={exportUrl(`/usvo/${cardId}/docx`)} download>
                      Экспорт в DOCX
                    </a>
                  </Button>
                  <Button size="sm" disabled={!dirty || saving} onClick={() => void saveCard()}>
                    {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                    Сохранить
                  </Button>
                </div>
              </SheetFooter>
            </>
          ) : null}
        </SheetContent>
      </Sheet>

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent className={nested ? "z-[70]" : undefined}>
          <DialogHeader>
            <DialogTitle>Удалить карточку?</DialogTitle>
            <DialogDescription>
              Карточка «{card?.name}» будет удалена. Это действие нельзя отменить.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              Отмена
            </Button>
            <Button variant="destructive" disabled={deleting} onClick={() => void deleteCard()}>
              {deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Удалить
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
