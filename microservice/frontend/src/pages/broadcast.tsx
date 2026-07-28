import * as React from "react"
import { Check, Info, Loader2 } from "lucide-react"
import { toast } from "sonner"
import { api } from "@/lib/api"
import type { BroadcastAudience, BroadcastResult } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { FormField } from "@/components/ui/form-field"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

export function BroadcastPage() {
  const [audience, setAudience] = React.useState<BroadcastAudience>({
    total: 0,
    subscribers: 0,
    max_ready: false,
  })
  const [loading, setLoading] = React.useState(true)
  const [text, setText] = React.useState("")
  const [target, setTarget] = React.useState<"all" | "subscribers">("all")
  const [sending, setSending] = React.useState(false)
  const [result, setResult] = React.useState<BroadcastResult | null>(null)
  const [confirmOpen, setConfirmOpen] = React.useState(false)

  const loadAudience = React.useCallback(async () => {
    setLoading(true)
    try {
      const a = await api<BroadcastAudience>("/broadcast/audience")
      setAudience(a)
    } catch {
      setAudience({ total: 0, subscribers: 0, max_ready: false })
    } finally {
      setLoading(false)
    }
  }, [])

  React.useEffect(() => {
    void loadAudience()
  }, [loadAudience])

  async function doSend() {
    const msg = text.trim()
    if (!msg) {
      toast.error("Введите текст сообщения")
      return
    }
    setConfirmOpen(false)
    setSending(true)
    setResult(null)
    try {
      const r = await api<BroadcastResult>("/broadcast", {
        method: "POST",
        body: JSON.stringify({ text: msg, target }),
      })
      setResult(r)
      toast.success(`Рассылка: доставлено ${r.sent}/${r.total}`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка рассылки")
    } finally {
      setSending(false)
    }
  }

  if (loading) {
    return <Skeleton className="h-64 w-full" />
  }

  return (
    <>
      <div className="w-full space-y-6">
        <div className="space-y-1.5">
          <h2 className="font-semibold leading-none tracking-tight">Рассылка в MAX</h2>
          <p className="text-sm text-muted-foreground">
            Сообщение придёт пользователям прямо в чат бота MAX. Ошибки доставки отдельным получателям не
            останавливают рассылку.
          </p>
        </div>

        <div className="space-y-4">
          {!audience.max_ready ? (
            <div className="flex items-center gap-2 rounded-md border border-input bg-transparent p-3 text-sm text-foreground">
              <Info className="h-4 w-4 shrink-0 text-primary" />
              <span>MAX-бот не настроен (max.bot_token в конфиге). Рассылка недоступна.</span>
            </div>
          ) : null}

          <div className="space-y-2">
            <label className="flex cursor-pointer items-center gap-3 rounded-lg border p-3 pr-6 has-[:checked]:border-primary has-[:checked]:bg-primary/10">
              <input
                type="radio"
                name="bc-target"
                checked={target === "all"}
                onChange={() => setTarget("all")}
                className="sr-only"
              />
              <span className="min-w-0 flex-1">
                <b>Все пользователи MAX</b>
                <small className="block text-muted-foreground">
                  {audience.total} чел. — все, кто когда-либо писал боту
                </small>
              </span>
              {target === "all" ? <Check className="h-4 w-4 shrink-0 text-primary" /> : null}
            </label>
            <label className="flex cursor-pointer items-center gap-3 rounded-lg border p-3 pr-6 has-[:checked]:border-primary has-[:checked]:bg-primary/10">
              <input
                type="radio"
                name="bc-target"
                checked={target === "subscribers"}
                onChange={() => setTarget("subscribers")}
                className="sr-only"
              />
              <span className="min-w-0 flex-1">
                <b>Только подписчики</b>
                <small className="block text-muted-foreground">
                  {audience.subscribers} чел. — подписаны на уведомления
                </small>
              </span>
              {target === "subscribers" ? <Check className="h-4 w-4 shrink-0 text-primary" /> : null}
            </label>
          </div>

          <FormField>
            <Label>Текст сообщения</Label>
            <Textarea
              rows={5}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Например: Открыт приём заявлений на новую меру поддержки для семей участников СВО…"
            />
          </FormField>

          <div className="flex justify-end">
            <Button disabled={!audience.max_ready || sending} onClick={() => setConfirmOpen(true)}>
              {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Отправить рассылку
            </Button>
          </div>

          {result ? (
            <div
              className={`rounded-lg border p-3 text-sm ${result.failed ? "border-primary/40 bg-primary/10" : "border-emerald-500/40 bg-emerald-500/10"}`}
            >
              Доставлено: <b>{result.sent}</b> из {result.total}. Ошибок: <b>{result.failed}</b>.
              {result.errors?.length ? (
                <div className="mt-2 max-h-32 overflow-auto text-xs">
                  {result.errors.slice(0, 12).map((e, i) => (
                    <div key={i}>
                      ID {e.user_id || "—"}: {e.error}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Отправить рассылку?</DialogTitle>
            <DialogDescription>
              {target === "subscribers"
                ? "Сообщение получат все подписанные пользователи бота MAX."
                : "Сообщение получат ВСЕ пользователи бота MAX."}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmOpen(false)}>
              Отмена
            </Button>
            <Button onClick={() => void doSend()}>Отправить</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
