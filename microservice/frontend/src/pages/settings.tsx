import * as React from "react"
import { Link } from "react-router-dom"
import {
  Info,
  Loader2,
  Monitor,
  Moon,
  MoreVertical,
  Pencil,
  Plus,
  RefreshCw,
  Sun,
  Trash2,
  UserCircle,
} from "lucide-react"
import { useTheme } from "next-themes"
import { toast } from "sonner"
import { api } from "@/lib/api"
import type { Employee, SlaSettings, User, UserProfile } from "@/lib/types"
import { cn, initials } from "@/lib/utils"
import { useApp } from "@/hooks/use-app"
import { EmptyState } from "@/components/empty-state"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { FormField } from "@/components/ui/form-field"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
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

const THEME_OPTIONS = [
  { value: "light", label: "Светлая", icon: Sun },
  { value: "dark", label: "Тёмная", icon: Moon },
  { value: "system", label: "Авто", icon: Monitor },
] as const

type ProfileForm = {
  last_name: string
  first_name: string
  middle_name: string
  birth_date: string
  phone: string
}

const EMPTY_PROFILE: ProfileForm = {
  last_name: "",
  first_name: "",
  middle_name: "",
  birth_date: "",
  phone: "",
}

export function SettingsPage() {
  const { user, setUser, isAdmin, meta, operator, setOperator, refreshMeta } = useApp()
  const { theme, setTheme } = useTheme()
  const [mounted, setMounted] = React.useState(false)
  const [tab, setTab] = React.useState("general")
  const [employees, setEmployees] = React.useState<Employee[]>([])
  const [sla, setSla] = React.useState<SlaSettings | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [profileForm, setProfileForm] = React.useState<ProfileForm>(EMPTY_PROFILE)
  const [profileLogin, setProfileLogin] = React.useState("")
  const [profileSaving, setProfileSaving] = React.useState(false)
  const [slaDays, setSlaDays] = React.useState("3")
  const [slaSaving, setSlaSaving] = React.useState(false)
  const [slaError, setSlaError] = React.useState<string | null>(null)
  const [empDialog, setEmpDialog] = React.useState<Employee | null | "new">(null)
  const [empForm, setEmpForm] = React.useState({
    name: "",
    login: "",
    position: "",
    phone: "",
    password: "",
    active: true,
  })
  const [empSaving, setEmpSaving] = React.useState(false)
  const [confirmDelete, setConfirmDelete] = React.useState<Employee | null>(null)

  React.useEffect(() => {
    setMounted(true)
  }, [])

  React.useEffect(() => {
    if (!isAdmin && tab === "users") setTab("general")
  }, [isAdmin, tab])

  const load = React.useCallback(async () => {
    setLoading(true)
    try {
      const tasks: Promise<unknown>[] = [
        api<{ profile: UserProfile }>("/auth/profile")
          .then(({ profile }) => {
            setProfileForm({
              last_name: profile.last_name || "",
              first_name: profile.first_name || "",
              middle_name: profile.middle_name || "",
              birth_date: profile.birth_date || "",
              phone: profile.phone || "",
            })
            setProfileLogin(profile.login || "")
          })
          .catch(() => {
            /* ignore */
          }),
      ]

      if (isAdmin) {
        tasks.push(
          api<{ items: Employee[] }>("/settings/employees").then(({ items }) => setEmployees(items)),
          api<SlaSettings>("/settings/sla")
            .then((slaData) => {
              setSla(slaData)
              setSlaDays(String(slaData.sla_business_days ?? slaData.default_business_days ?? 3))
            })
            .catch(() => setSla(null)),
        )
      }

      await Promise.all(tasks)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка загрузки")
    } finally {
      setLoading(false)
    }
  }, [isAdmin])

  React.useEffect(() => {
    void load()
  }, [load])

  async function saveProfile(e: React.FormEvent) {
    e.preventDefault()
    if (!profileForm.last_name.trim() && !profileForm.first_name.trim()) {
      toast.error("Укажите фамилию или имя")
      return
    }
    setProfileSaving(true)
    try {
      const res = await api<{ profile: UserProfile; user: User }>("/auth/profile", {
        method: "PUT",
        body: JSON.stringify({
          last_name: profileForm.last_name.trim(),
          first_name: profileForm.first_name.trim(),
          middle_name: profileForm.middle_name.trim(),
          birth_date: profileForm.birth_date.trim(),
          phone: profileForm.phone.trim(),
        }),
      })
      if (res.user) setUser(res.user)
      toast.success("Профиль сохранён")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Ошибка сохранения")
    } finally {
      setProfileSaving(false)
    }
  }

  async function saveSla(e: React.FormEvent) {
    e.preventDefault()
    setSlaError(null)
    const val = parseInt(slaDays, 10)
    if (!Number.isFinite(val)) {
      setSlaError("Укажите число дней")
      return
    }
    setSlaSaving(true)
    try {
      const res = await api<SlaSettings>("/settings/sla", {
        method: "PUT",
        body: JSON.stringify({ sla_business_days: val }),
      })
      setSla(res)
      await refreshMeta()
      toast.success(`Регламент обновлён: ${res.sla_business_days} дн.`)
    } catch (err) {
      setSlaError(err instanceof Error ? err.message : "Ошибка")
    } finally {
      setSlaSaving(false)
    }
  }

  function openEmpForm(emp: Employee | null) {
    setEmpDialog(emp ? emp : "new")
    setEmpForm({
      name: emp?.name || "",
      login: emp?.login || "",
      position: emp?.position || "",
      phone: emp?.phone || "",
      password: "",
      active: emp?.active !== false,
    })
  }

  async function saveEmployee() {
    if (!empForm.name.trim() || !empForm.login.trim()) {
      toast.error("Укажите ФИО и логин")
      return
    }
    if (empDialog === "new" && !empForm.password) {
      toast.error("Задайте пароль")
      return
    }
    setEmpSaving(true)
    try {
      const payload: Record<string, unknown> = {
        name: empForm.name.trim(),
        login: empForm.login.trim(),
        position: empForm.position.trim(),
        phone: empForm.phone.trim(),
      }
      if (empForm.password) payload.password = empForm.password
      if (empDialog && empDialog !== "new") payload.active = empForm.active

      if (empDialog && empDialog !== "new") {
        await api(`/settings/employees/${empDialog.id}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        })
        toast.success("Сотрудник обновлён")
      } else {
        await api("/settings/employees", { method: "POST", body: JSON.stringify(payload) })
        toast.success("Сотрудник создан")
      }
      setEmpDialog(null)
      void load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка")
    } finally {
      setEmpSaving(false)
    }
  }

  async function deleteEmployee() {
    if (!confirmDelete) return
    try {
      await api(`/settings/employees/${confirmDelete.id}`, { method: "DELETE" })
      toast.success("Сотрудник удалён")
      setConfirmDelete(null)
      void load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка")
    }
  }

  const displayName =
    [profileForm.last_name, profileForm.first_name, profileForm.middle_name].filter(Boolean).join(" ") ||
    user?.name ||
    "Пользователь"
  const currentTheme = mounted ? theme || "system" : "system"
  const operatorOptions = [...new Set([operator, ...(meta?.operators || [])].filter(Boolean))] as string[]

  return (
    <>
      <Tabs value={tab} onValueChange={setTab}>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <TabsList>
            <TabsTrigger value="general">Основные</TabsTrigger>
            {isAdmin ? <TabsTrigger value="users">Пользователи</TabsTrigger> : null}
          </TabsList>
          {isAdmin && tab === "users" ? (
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="icon"
                className="h-8 w-8"
                title="Обновить"
                aria-label="Обновить"
                onClick={() => void load()}
              >
                <RefreshCw className="h-4 w-4" />
              </Button>
              <Button size="sm" className="h-8" onClick={() => openEmpForm(null)}>
                <Plus className="h-4 w-4" />
                Добавить сотрудника
              </Button>
            </div>
          ) : null}
        </div>

        <TabsContent value="general">
          <div className="space-y-6">
            {isAdmin ? (
              <Card className="border-primary/20 bg-primary/5">
                <CardContent className="flex items-start gap-3 pt-6">
                  <Info className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
                  <div className="text-sm">
                    <p>
                      <b>Меры поддержки</b> настраиваются в разделе{" "}
                      <Link to="/applications" className="text-primary underline">
                        Заявления → Доступные меры поддержки
                      </Link>
                      : создание мер, описаний для ИИ, шаблонов заявлений и синхронизация с базой знаний.
                    </p>
                  </div>
                </CardContent>
              </Card>
            ) : null}

            {loading ? (
              <Skeleton className="h-64 w-full" />
            ) : (
              <>
                <div className="flex items-center gap-4">
                  <Avatar className="h-14 w-14">
                    <AvatarFallback className="text-base">{initials(displayName)}</AvatarFallback>
                  </Avatar>
                  <div className="min-w-0">
                    <div className="truncate text-lg font-semibold tracking-tight">{displayName}</div>
                    <div className="truncate text-sm text-muted-foreground">
                      {profileLogin || user?.sub || user?.role || "—"}
                    </div>
                  </div>
                </div>

                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Данные аккаунта</CardTitle>
                    <CardDescription>Фамилия, имя, отчество и дата рождения для вашего профиля</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <form className="space-y-4" onSubmit={(e) => void saveProfile(e)}>
                      <div className="grid gap-4 sm:grid-cols-2">
                        <div className="field-group">
                          <Label htmlFor="last-name">Фамилия</Label>
                          <Input
                            id="last-name"
                            value={profileForm.last_name}
                            onChange={(e) => setProfileForm((f) => ({ ...f, last_name: e.target.value }))}
                            placeholder="Иванов"
                          />
                        </div>
                        <div className="field-group">
                          <Label htmlFor="first-name">Имя</Label>
                          <Input
                            id="first-name"
                            value={profileForm.first_name}
                            onChange={(e) => setProfileForm((f) => ({ ...f, first_name: e.target.value }))}
                            placeholder="Иван"
                          />
                        </div>
                        <div className="field-group">
                          <Label htmlFor="middle-name">Отчество</Label>
                          <Input
                            id="middle-name"
                            value={profileForm.middle_name}
                            onChange={(e) => setProfileForm((f) => ({ ...f, middle_name: e.target.value }))}
                            placeholder="Иванович"
                          />
                        </div>
                        <div className="field-group">
                          <Label htmlFor="birth-date">Дата рождения</Label>
                          <Input
                            id="birth-date"
                            type="date"
                            value={profileForm.birth_date}
                            onChange={(e) => setProfileForm((f) => ({ ...f, birth_date: e.target.value }))}
                          />
                        </div>
                        <div className="field-group sm:col-span-2">
                          <Label htmlFor="phone">Телефон</Label>
                          <Input
                            id="phone"
                            value={profileForm.phone}
                            onChange={(e) => setProfileForm((f) => ({ ...f, phone: e.target.value }))}
                            placeholder="+7 (900) 000-00-00"
                          />
                        </div>
                      </div>
                      <div className="flex justify-end">
                        <Button type="submit" disabled={profileSaving}>
                          {profileSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                          Сохранить
                        </Button>
                      </div>
                    </form>
                  </CardContent>
                </Card>

                {isAdmin ? (
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-base">Отвечает</CardTitle>
                      <CardDescription>
                        От чьего имени подписываются ответы на обращения и заявления
                      </CardDescription>
                    </CardHeader>
                    <CardContent>
                      <FormField className="max-w-sm">
                        <Label htmlFor="operator-select">Сотрудник</Label>
                        <Select
                          value={operator}
                          onValueChange={(value) => {
                            setOperator(value)
                            toast.message(`Отвечаем от имени: ${value}`)
                          }}
                        >
                          <SelectTrigger id="operator-select">
                            <SelectValue placeholder="Выберите сотрудника" />
                          </SelectTrigger>
                          <SelectContent>
                            {operatorOptions.map((option) => (
                              <SelectItem key={option} value={option}>
                                {option}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </FormField>
                    </CardContent>
                  </Card>
                ) : null}

                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Внешний вид</CardTitle>
                    <CardDescription>Светлая, тёмная или системная тема интерфейса</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="grid gap-3 sm:grid-cols-3">
                      {THEME_OPTIONS.map((option) => {
                        const Icon = option.icon
                        const active = currentTheme === option.value
                        return (
                          <button
                            key={option.value}
                            type="button"
                            onClick={() => setTheme(option.value)}
                            className={cn(
                              "flex items-center gap-3 rounded-lg border px-4 py-3 text-left transition-colors",
                              active
                                ? "border-primary bg-primary/10 text-foreground"
                                : "hover:bg-muted/50",
                            )}
                          >
                            <Icon className="h-4 w-4 shrink-0" />
                            <span className="text-sm font-medium">{option.label}</span>
                          </button>
                        )
                      })}
                    </div>
                  </CardContent>
                </Card>

                {isAdmin && sla ? (
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-base">Регламент времени ответа</CardTitle>
                      <CardDescription>
                        Обращение считается просроченным, если ответ не дан за указанное число календарных дней с
                        момента поступления. Влияет на бейдж «Просрочено», сортировку и напоминания операторам.
                      </CardDescription>
                    </CardHeader>
                    <CardContent>
                      <form className="space-y-4" onSubmit={(e) => void saveSla(e)}>
                        <div className="grid gap-4 sm:grid-cols-2">
                          <div className="field-group">
                            <Label htmlFor="sla-days">Срок ответа, дн.</Label>
                            <Input
                              id="sla-days"
                              type="number"
                              min={sla.min ?? 1}
                              max={sla.max ?? 30}
                              value={slaDays}
                              onChange={(e) => setSlaDays(e.target.value)}
                            />
                            {sla.default_business_days != null ? (
                              <p className="text-xs text-muted-foreground">
                                По умолчанию — {sla.default_business_days} дн. Допустимо от {sla.min ?? 1} до{" "}
                                {sla.max ?? 30}.
                              </p>
                            ) : null}
                          </div>
                        </div>
                        {slaError ? <p className="text-sm text-destructive">{slaError}</p> : null}
                        <div className="flex justify-end">
                          <Button type="submit" disabled={slaSaving}>
                            {slaSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                            Сохранить
                          </Button>
                        </div>
                      </form>
                    </CardContent>
                  </Card>
                ) : null}
              </>
            )}
          </div>
        </TabsContent>

        {isAdmin ? (
          <TabsContent value="users">
            <div className="space-y-4">
              <Card className="border-primary/20 bg-primary/5">
                <CardContent className="flex items-start gap-3 pt-6">
                  <Info className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
                  <p className="text-sm">
                    Сотрудник входит в кабинет по своему логину и паролю и отвечает на обращения только под своей
                    учётной записью. Администратор может назначить ответственным любого сотрудника.
                  </p>
                </CardContent>
              </Card>

              {loading ? (
                <Skeleton className="h-48 w-full" />
              ) : employees.length === 0 ? (
                <EmptyState
                  title="Сотрудников пока нет"
                  description="Создайте первую учётную запись — сотрудник сможет войти и отвечать на обращения."
                  icon={UserCircle}
                />
              ) : (
                <div className="w-full rounded-xl border bg-card">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Сотрудник</TableHead>
                        <TableHead>Логин</TableHead>
                        <TableHead>Телефон</TableHead>
                        <TableHead>Статус</TableHead>
                        <TableHead className="w-12 text-right" />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {employees.map((e) => (
                        <TableRow key={e.id}>
                          <TableCell>
                            <div className="font-medium">{e.name}</div>
                            <div className="text-xs text-muted-foreground">{e.position || "—"}</div>
                          </TableCell>
                          <TableCell className="truncate">{e.login}</TableCell>
                          <TableCell>{e.phone || "—"}</TableCell>
                          <TableCell>
                            <Badge variant={e.active !== false ? "default" : "secondary"}>
                              {e.active !== false ? "Активен" : "Отключён"}
                            </Badge>
                          </TableCell>
                          <TableCell className="w-12 text-right">
                            <div className="flex justify-end">
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
                                  <DropdownMenuItem onClick={() => openEmpForm(e)}>
                                    <Pencil className="h-4 w-4" />
                                    Редактировать
                                  </DropdownMenuItem>
                                  <DropdownMenuItem
                                    className="text-destructive focus:text-destructive"
                                    onClick={() => setConfirmDelete(e)}
                                  >
                                    <Trash2 className="h-4 w-4" />
                                    Удалить
                                  </DropdownMenuItem>
                                </DropdownMenuContent>
                              </DropdownMenu>
                            </div>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </div>
          </TabsContent>
        ) : null}
      </Tabs>

      <Dialog open={!!empDialog} onOpenChange={() => setEmpDialog(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{empDialog && empDialog !== "new" ? "Изменить сотрудника" : "Новый сотрудник"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="field-group">
              <Label>ФИО *</Label>
              <Input value={empForm.name} onChange={(e) => setEmpForm((f) => ({ ...f, name: e.target.value }))} />
            </div>
            <div className="field-group">
              <Label>Должность</Label>
              <Input
                value={empForm.position}
                onChange={(e) => setEmpForm((f) => ({ ...f, position: e.target.value }))}
              />
            </div>
            <div className="field-group">
              <Label>Логин *</Label>
              <Input value={empForm.login} onChange={(e) => setEmpForm((f) => ({ ...f, login: e.target.value }))} />
            </div>
            <div className="field-group">
              <Label>Телефон</Label>
              <Input value={empForm.phone} onChange={(e) => setEmpForm((f) => ({ ...f, phone: e.target.value }))} />
            </div>
            <div className="field-group">
              <Label>Пароль {empDialog && empDialog !== "new" ? "(оставьте пустым, чтобы не менять)" : "*"}</Label>
              <Input
                type="password"
                value={empForm.password}
                onChange={(e) => setEmpForm((f) => ({ ...f, password: e.target.value }))}
              />
            </div>
            {empDialog && empDialog !== "new" ? (
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={empForm.active}
                  onChange={(e) => setEmpForm((f) => ({ ...f, active: e.target.checked }))}
                />
                Активна (может входить)
              </label>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setEmpDialog(null)}>
              Отмена
            </Button>
            <Button disabled={empSaving} onClick={() => void saveEmployee()}>
              {empSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              {empDialog && empDialog !== "new" ? "Сохранить" : "Создать"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!confirmDelete} onOpenChange={() => setConfirmDelete(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Удалить учётную запись?</DialogTitle>
            <DialogDescription>
              Учётная запись «{confirmDelete?.name}» будет удалена — сотрудник больше не сможет войти в кабинет.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmDelete(null)}>
              Отмена
            </Button>
            <Button variant="destructive" onClick={() => void deleteEmployee()}>
              Удалить
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
