import * as React from "react"
import { Link } from "react-router-dom"
import {
  ArrowUp,
  Clock,
  Download,
  Loader2,
  Maximize2,
  Minimize2,
  MoreVertical,
  Pencil,
  Pin,
  PinOff,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
  X,
} from "lucide-react"
import { toast } from "sonner"
import { api, exportUrl } from "@/lib/api"
import type { AiAnswerType, AiChat, AiMessage } from "@/lib/types"
import { useApp } from "@/hooks/use-app"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { FormField } from "@/components/ui/form-field"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { cn } from "@/lib/utils"
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
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

function formatTime(ts?: number | string) {
  if (!ts) return ""
  try {
    const n = typeof ts === "number" ? ts : parseFloat(ts)
    return new Date(n * 1000).toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "Europe/Moscow",
    })
  } catch {
    return ""
  }
}

function chatTimestamp(ts?: number | string) {
  if (ts == null || ts === "") return 0
  const n = typeof ts === "number" ? ts : parseFloat(String(ts))
  return Number.isFinite(n) ? n : 0
}

function moscowDayKey(tsSeconds: number) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Moscow",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(tsSeconds * 1000))
}

type ChatPeriod = "today" | "yesterday" | "week" | "later"

const CHAT_PERIOD_LABELS: Record<ChatPeriod, string> = {
  today: "Сегодня",
  yesterday: "Вчера",
  week: "Неделю назад",
  later: "Позднее",
}

function chatPeriod(updatedAt?: number | string, nowSeconds = Date.now() / 1000): ChatPeriod {
  const ts = chatTimestamp(updatedAt) || nowSeconds
  const today = moscowDayKey(nowSeconds)
  const chatDay = moscowDayKey(ts)
  if (chatDay === today) return "today"
  const yesterdayMs = Date.parse(`${today}T12:00:00+03:00`) - 86_400_000
  if (chatDay === moscowDayKey(yesterdayMs / 1000)) return "yesterday"
  const todayMs = Date.parse(`${today}T12:00:00+03:00`)
  const chatMs = Date.parse(`${chatDay}T12:00:00+03:00`)
  const diffDays = Math.round((todayMs - chatMs) / 86_400_000)
  if (diffDays >= 2 && diffDays <= 7) return "week"
  return "later"
}

function renderMessageContent(text: string) {
  const parts: React.ReactNode[] = []
  const re = /\[([^\]]+)\]\(\/usvo\/cards\/(\d+)\)/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    parts.push(
      <Link key={`${m.index}-${m[2]}`} to={`/usvo/cards/${m[2]}`} className="text-primary underline underline-offset-2">
        {m[1]}
      </Link>,
    )
    last = m.index + m[0].length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts.length ? parts : text
}

export function AiChatPage() {
  const { isAdmin } = useApp()
  const [chats, setChats] = React.useState<AiChat[]>([])
  const [answerTypes, setAnswerTypes] = React.useState<AiAnswerType[]>([
    { value: "text", label: "Текст" },
    { value: "detailed_reference", label: "Развернутая справка" },
    { value: "brief_reference", label: "Краткая справка" },
  ])
  const [aiReady, setAiReady] = React.useState(true)
  const [activeId, setActiveId] = React.useState<string | null>(null)
  const [messages, setMessages] = React.useState<AiMessage[]>([])
  const [loadingChats, setLoadingChats] = React.useState(true)
  const [loadingMessages, setLoadingMessages] = React.useState(false)
  const [sending, setSending] = React.useState(false)
  const [input, setInput] = React.useState("")
  const [confirmDelete, setConfirmDelete] = React.useState(false)
  const [confirmRebuildKb, setConfirmRebuildKb] = React.useState(false)
  const [rebuildingKb, setRebuildingKb] = React.useState(false)
  const [chatToDelete, setChatToDelete] = React.useState<AiChat | null>(null)
  const [renameChat, setRenameChat] = React.useState<AiChat | null>(null)
  const [renameTitle, setRenameTitle] = React.useState("")
  const [renaming, setRenaming] = React.useState(false)
  const [historyOpen, setHistoryOpen] = React.useState(true)
  const [chatWide, setChatWide] = React.useState(false)
  const bottomRef = React.useRef<HTMLDivElement>(null)
  const textareaRef = React.useRef<HTMLTextAreaElement>(null)

  const activeChat = chats.find((c) => String(c.id) === String(activeId)) || null
  const pendingDelete = chatToDelete || (confirmDelete ? activeChat : null)

  const loadChats = React.useCallback(async (preferredId?: string | null) => {
    setLoadingChats(true)
    try {
      const data = await api<{ items: AiChat[]; answerTypes?: AiAnswerType[]; ready?: boolean }>("/ai-chats")
      setChats(data.items || [])
      if (data.answerTypes?.length) setAnswerTypes(data.answerTypes)
      setAiReady(data.ready !== false || (data.items?.length ?? 0) > 0)
      const pref = preferredId ?? activeId
      const next =
        pref && data.items?.some((c) => String(c.id) === String(pref))
          ? String(pref)
          : data.items?.[0]
            ? String(data.items[0].id)
            : null
      setActiveId(next)
      return next
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка загрузки")
      return null
    } finally {
      setLoadingChats(false)
    }
  }, [activeId])

  const loadMessages = React.useCallback(async (chatId: string) => {
    setLoadingMessages(true)
    try {
      const data = await api<{ items: AiMessage[] }>(`/ai-chats/${chatId}/messages`)
      setMessages(data.items || [])
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка загрузки сообщений")
      setMessages([])
    } finally {
      setLoadingMessages(false)
    }
  }, [])

  React.useEffect(() => {
    void (async () => {
      const id = await loadChats()
      if (!id) {
        try {
          const created = await api<{ chat: AiChat }>("/ai-chats", {
            method: "POST",
            body: JSON.stringify({ answerType: "text" }),
          })
          await loadChats(String(created.chat.id))
        } catch {
          /* ignore */
        }
      }
    })()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  React.useEffect(() => {
    if (activeId) void loadMessages(activeId)
  }, [activeId, loadMessages])

  React.useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, sending])

  React.useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = "0px"
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }, [input])

  async function createChat() {
    try {
      const data = await api<{ chat: AiChat }>("/ai-chats", {
        method: "POST",
        body: JSON.stringify({ answerType: "text" }),
      })
      await loadChats(String(data.chat.id))
      setMessages([])
      textareaRef.current?.focus()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка")
    }
  }

  async function deleteChat() {
    if (!pendingDelete) return
    const id = String(pendingDelete.id)
    try {
      await api(`/ai-chats/${id}`, { method: "DELETE" })
      toast.success("Диалог удалён")
      setConfirmDelete(false)
      setChatToDelete(null)
      if (String(activeId) === id) {
        setMessages([])
        const next = await loadChats(null)
        if (next) await loadMessages(next)
      } else {
        await loadChats(activeId)
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка")
    }
  }

  function openRename(chat: AiChat) {
    setRenameChat(chat)
    setRenameTitle(chat.title || "")
  }

  async function saveRename() {
    if (!renameChat) return
    const title = renameTitle.trim()
    if (!title) {
      toast.error("Введите название чата")
      return
    }
    setRenaming(true)
    try {
      const data = await api<{ chat: AiChat }>(`/ai-chats/${renameChat.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title }),
      })
      setChats((prev) =>
        prev.map((chat) => (String(chat.id) === String(renameChat.id) ? { ...chat, ...data.chat } : chat)),
      )
      toast.success("Чат переименован")
      setRenameChat(null)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка")
    } finally {
      setRenaming(false)
    }
  }

  async function togglePin(chat: AiChat) {
    const nextPinned = !chat.pinned
    try {
      const data = await api<{ chat: AiChat }>(`/ai-chats/${chat.id}`, {
        method: "PATCH",
        body: JSON.stringify({ pinned: nextPinned }),
      })
      setChats((prev) => {
        const updated = prev.map((item) =>
          String(item.id) === String(chat.id) ? { ...item, ...data.chat } : item,
        )
        return [...updated].sort((a, b) => {
          const pinDiff = Number(Boolean(b.pinned)) - Number(Boolean(a.pinned))
          if (pinDiff) return pinDiff
          return chatTimestamp(b.updatedAt) - chatTimestamp(a.updatedAt)
        })
      })
      toast.success(nextPinned ? "Чат закреплён" : "Чат откреплён")
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка")
    }
  }

  const chatGroups = React.useMemo(() => {
    const pinned = chats.filter((chat) => chat.pinned)
    const unpinned = chats.filter((chat) => !chat.pinned)
    const periods: ChatPeriod[] = ["today", "yesterday", "week", "later"]
    const groups: { key: string; label: string; items: AiChat[] }[] = []
    if (pinned.length) {
      groups.push({ key: "pinned", label: "Закреплённые", items: pinned })
    }
    for (const period of periods) {
      const items = unpinned.filter((chat) => chatPeriod(chat.updatedAt) === period)
      if (items.length) {
        groups.push({ key: period, label: CHAT_PERIOD_LABELS[period], items })
      }
    }
    return groups
  }, [chats])

  async function sendMessage(text?: string) {
    const content = (text ?? input).trim()
    if (!activeId || !content || sending) return
    setInput("")
    setSending(true)
    const optimistic: AiMessage = {
      id: `tmp-${Date.now()}`,
      chatId: activeId,
      role: "user",
      content,
      createdAt: String(Date.now() / 1000),
    }
    setMessages((m) => [...m, optimistic])
    try {
      const data = await api<{ message: AiMessage }>(`/ai-chats/${activeId}/messages`, {
        method: "POST",
        body: JSON.stringify({ content, answerType: activeChat?.answerType || "text" }),
      })
      setMessages((m) => [...m.filter((x) => x.id !== optimistic.id), data.message])
      await loadChats(activeId)
      if (activeId) await loadMessages(activeId)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка")
      setMessages((m) => m.filter((x) => x.id !== optimistic.id))
    } finally {
      setSending(false)
    }
  }

  async function onAnswerTypeChange(value: string) {
    if (!activeId) return
    try {
      const data = await api<{ chat: AiChat }>(`/ai-chats/${activeId}`, {
        method: "PATCH",
        body: JSON.stringify({ answerType: value }),
      })
      setChats((prev) => prev.map((c) => (String(c.id) === activeId ? { ...c, ...data.chat } : c)))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка")
    }
  }

  async function rebuildKb() {
    setRebuildingKb(true)
    try {
      const result = await api<{ count?: number }>("/ai-knowledge/usvo/rebuild", { method: "POST" })
      setConfirmRebuildKb(false)
      toast.success(`База знаний пересобрана: ${result.count ?? 0} карточек`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка")
    } finally {
      setRebuildingKb(false)
    }
  }

  return (
    <>
      <div className="relative -m-6 flex h-[calc(100svh-5rem)] min-h-0 flex-col overflow-hidden">
        <header className="flex items-center justify-between gap-3 border-b px-6 py-3">
          <div className="flex min-w-0 items-center gap-1">
            <h2 className="truncate text-sm font-medium">
              {activeChat?.title || "Новый диалог"}
            </h2>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 shrink-0"
                  title="Действия"
                  aria-label="Действия"
                  disabled={!activeChat}
                >
                  <MoreVertical className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuItem onClick={() => void createChat()}>
                  <Plus className="h-4 w-4" />
                  Новый чат
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setChatWide((wide) => !wide)}>
                  {chatWide ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
                  {chatWide ? "Сузить чат" : "Расширить чат"}
                </DropdownMenuItem>
                {activeChat ? (
                  <DropdownMenuItem onClick={() => openRename(activeChat)}>
                    <Pencil className="h-4 w-4" />
                    Переименовать
                  </DropdownMenuItem>
                ) : null}
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  className="text-destructive focus:text-destructive"
                  onClick={() => setConfirmDelete(true)}
                >
                  <Trash2 className="h-4 w-4" />
                  Удалить диалог
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          <div className="flex items-center gap-1">
            {isAdmin ? (
              <Button
                variant="outline"
                size="sm"
                className="h-8"
                onClick={() => setConfirmRebuildKb(true)}
              >
                Пересобрать базу знаний
                <RefreshCw className="h-4 w-4" />
              </Button>
            ) : null}
            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8"
              title="История чатов"
              aria-label="История чатов"
              onClick={() => setHistoryOpen((open) => !open)}
            >
              <Clock className="h-4 w-4" />
            </Button>
          </div>
        </header>

        {!aiReady ? (
          <div className="border-b border-primary/20 bg-primary/10 px-4 py-2 text-sm text-foreground">
            Dify-ассистент чата не настроен. Укажите dify.usvo_ai.app_key в config.yaml.
          </div>
        ) : null}

        <div className="relative flex min-h-0 flex-1">
          <div className="flex min-w-0 flex-1 flex-col">
            <ScrollArea className="flex-1">
              <div
                className={cn(
                  "mx-auto flex w-full flex-col gap-6 py-6 transition-[max-width,padding]",
                  chatWide ? "max-w-none px-6" : "max-w-3xl px-4",
                )}
              >
                {loadingMessages ? (
                  <div className="space-y-4">
                    <Skeleton className="ml-auto h-12 w-1/2 rounded-2xl" />
                    <Skeleton className="h-28 w-full rounded-2xl" />
                  </div>
                ) : messages.length === 0 && !sending ? (
                  <div className="flex flex-col items-center justify-center gap-3 py-20 text-center">
                    <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
                      <Sparkles className="h-5 w-5" />
                    </div>
                    <div>
                      <h3 className="font-semibold">Задайте вопрос по нормативке</h3>
                      <p className="mt-1 max-w-md text-sm text-muted-foreground">
                        Можно запросить данные по человеку, статистику по базе или сформировать справку.
                      </p>
                    </div>
                  </div>
                ) : (
                  <>
                    {messages.map((msg) =>
                      msg.role === "user" ? (
                        <div key={msg.id} className="flex justify-end">
                          <div className="max-w-[85%] rounded-2xl bg-muted px-4 py-3 text-sm leading-relaxed">
                            <div className="whitespace-pre-wrap">{msg.content}</div>
                            {msg.createdAt ? (
                              <time className="mt-1.5 block text-[11px] text-muted-foreground">
                                {formatTime(msg.createdAt)}
                              </time>
                            ) : null}
                          </div>
                        </div>
                      ) : (
                        <div key={msg.id} className="flex flex-col gap-3">
                          <div className="flex items-center gap-2.5">
                            <Avatar className="h-8 w-8">
                              <AvatarFallback className="bg-primary/15 text-primary">
                                <Sparkles className="h-3.5 w-3.5" />
                              </AvatarFallback>
                            </Avatar>
                            <div className="text-sm font-medium">Ассистент</div>
                          </div>
                          <div className="pl-[42px] text-sm leading-relaxed text-foreground">
                            <div className="whitespace-pre-wrap">{renderMessageContent(msg.content)}</div>
                            {msg.metadata?.hasDocx && msg.id ? (
                              <a
                                className="mt-3 inline-flex items-center gap-1.5 text-xs text-primary underline underline-offset-2"
                                href={exportUrl(`/ai-chats/${activeId}/messages/${msg.id}/docx`)}
                                download
                              >
                                <Download className="h-3.5 w-3.5" />
                                Скачать справку (.docx)
                              </a>
                            ) : null}
                            {msg.createdAt ? (
                              <time className="mt-2 block text-[11px] text-muted-foreground">
                                {formatTime(msg.createdAt)}
                              </time>
                            ) : null}
                          </div>
                        </div>
                      ),
                    )}
                    {sending ? (
                      <div className="flex flex-col gap-3">
                        <div className="flex items-center gap-2.5">
                          <Avatar className="h-8 w-8">
                            <AvatarFallback className="bg-primary/15 text-primary">
                              <Sparkles className="h-3.5 w-3.5" />
                            </AvatarFallback>
                          </Avatar>
                          <div className="text-sm font-medium">Ассистент</div>
                        </div>
                        <div className="pl-[42px]">
                          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                        </div>
                      </div>
                    ) : null}
                    <div ref={bottomRef} />
                  </>
                )}
              </div>
            </ScrollArea>

            <div className={cn("pb-4 pt-2", chatWide ? "px-6" : "px-4")}>
              <div
                className={cn(
                  "mx-auto w-full rounded-2xl border bg-background p-3 transition-[max-width]",
                  chatWide ? "max-w-none" : "max-w-3xl",
                )}
              >
                <Textarea
                  ref={textareaRef}
                  rows={1}
                  maxLength={12000}
                  placeholder="Начните любую задачу. Нажмите Shift+Enter, чтобы перенести строку"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault()
                      void sendMessage()
                    }
                  }}
                  disabled={!activeId || sending}
                  className="min-h-[44px] resize-none border-0 p-1 shadow-none focus-visible:ring-0"
                />
                <div className="mt-2 flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1">
                    <Select
                      value={activeChat?.answerType || "text"}
                      onValueChange={(v) => void onAnswerTypeChange(v)}
                      disabled={!activeChat}
                    >
                      <SelectTrigger className="h-8 w-auto gap-1 border-0 px-2 shadow-none focus:ring-0">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {answerTypes.map((t) => (
                          <SelectItem key={t.value} value={t.value}>
                            {t.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <Button
                    type="button"
                    size="icon"
                    className="h-9 w-9 rounded-full"
                    title="Отправить"
                    aria-label="Отправить"
                    disabled={!activeId || !input.trim() || sending}
                    onClick={() => void sendMessage()}
                  >
                    {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowUp className="h-4 w-4" />}
                  </Button>
                </div>
              </div>
            </div>
          </div>

          {historyOpen ? (
            <aside className="flex w-72 shrink-0 flex-col border-l bg-background">
              <div className="flex items-center justify-between border-b px-3 py-3">
                <div className="text-sm font-medium">История</div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  title="Закрыть"
                  aria-label="Закрыть"
                  onClick={() => setHistoryOpen(false)}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
              <ScrollArea className="flex-1 px-2 py-2">
                {loadingChats ? (
                  <div className="space-y-2 p-2">
                    <Skeleton className="h-10 w-full" />
                    <Skeleton className="h-10 w-full" />
                  </div>
                ) : chats.length === 0 ? (
                  <p className="px-2 py-6 text-center text-sm text-muted-foreground">Диалогов пока нет</p>
                ) : (
                  chatGroups.map((group) => (
                    <div key={group.key} className="mb-3">
                      <div className="px-2 py-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                        {group.label}
                      </div>
                      {group.items.map((chat) => (
                        <div
                          key={chat.id}
                          className={cn(
                            "group mb-1 flex w-full items-center gap-1 rounded-lg px-2 py-1.5 transition-colors hover:bg-muted",
                            String(chat.id) === activeId && "bg-muted",
                          )}
                        >
                          <button
                            type="button"
                            className="min-w-0 flex-1 px-1 py-0.5 text-left"
                            onClick={() => setActiveId(String(chat.id))}
                          >
                            <div className="flex min-w-0 items-center gap-1.5">
                              {chat.pinned ? (
                                <Pin className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
                              ) : null}
                              <span className="truncate text-sm">{chat.title}</span>
                            </div>
                            {chat.updatedAt ? (
                              <div className="text-xs text-muted-foreground">{formatTime(chat.updatedAt)}</div>
                            ) : null}
                          </button>
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7 shrink-0 opacity-0 transition-opacity group-hover:opacity-100 data-[state=open]:opacity-100"
                                title="Действия с чатом"
                                aria-label="Действия с чатом"
                                onClick={(e) => e.stopPropagation()}
                              >
                                <MoreVertical className="h-4 w-4" />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <DropdownMenuItem onClick={() => void togglePin(chat)}>
                                {chat.pinned ? <PinOff className="h-4 w-4" /> : <Pin className="h-4 w-4" />}
                                {chat.pinned ? "Открепить" : "Закрепить"}
                              </DropdownMenuItem>
                              <DropdownMenuItem onClick={() => openRename(chat)}>
                                <Pencil className="h-4 w-4" />
                                Переименовать
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                className="text-destructive focus:text-destructive"
                                onClick={() => {
                                  setChatToDelete(chat)
                                  setConfirmDelete(true)
                                }}
                              >
                                <Trash2 className="h-4 w-4" />
                                Удалить
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </div>
                      ))}
                    </div>
                  ))
                )}
              </ScrollArea>
              <div className="border-t p-3">
                <Button className="w-full" size="sm" onClick={() => void createChat()}>
                  <Plus className="h-4 w-4" />
                  Новый чат
                </Button>
              </div>
            </aside>
          ) : null}
        </div>
      </div>

      <Dialog open={confirmRebuildKb} onOpenChange={setConfirmRebuildKb}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Пересобрать базу знаний?</DialogTitle>
            <DialogDescription>
              База знаний ИИ будет пересобрана по актуальным карточкам УСВО. Это может занять некоторое время.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" disabled={rebuildingKb} onClick={() => setConfirmRebuildKb(false)}>
              Отмена
            </Button>
            <Button disabled={rebuildingKb} onClick={() => void rebuildKb()}>
              {rebuildingKb ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Пересобрать
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={confirmDelete}
        onOpenChange={(open) => {
          setConfirmDelete(open)
          if (!open) setChatToDelete(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Удалить диалог?</DialogTitle>
            <DialogDescription>
              Диалог «{pendingDelete?.title}» будет удалён вместе с историей переписки.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => {
                setConfirmDelete(false)
                setChatToDelete(null)
              }}
            >
              Отмена
            </Button>
            <Button variant="destructive" onClick={() => void deleteChat()}>
              Удалить
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={!!renameChat}
        onOpenChange={(open) => {
          if (!open) setRenameChat(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Переименовать чат</DialogTitle>
            <DialogDescription>Задайте новое название для диалога.</DialogDescription>
          </DialogHeader>
          <FormField>
            <Label htmlFor="chat-rename-title">Название</Label>
            <Input
              id="chat-rename-title"
              value={renameTitle}
              maxLength={120}
              autoFocus
              onChange={(e) => setRenameTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault()
                  void saveRename()
                }
              }}
            />
          </FormField>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRenameChat(null)}>
              Отмена
            </Button>
            <Button disabled={renaming || !renameTitle.trim()} onClick={() => void saveRename()}>
              {renaming ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Сохранить
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
