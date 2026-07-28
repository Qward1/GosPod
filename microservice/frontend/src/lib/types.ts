export type User = {
  name: string
  role: string
  sub?: string
  is_admin?: boolean
}

export type UserProfile = {
  sub?: string
  login?: string
  last_name: string
  first_name: string
  middle_name: string
  birth_date: string
  phone?: string
  name?: string
  editable?: boolean
}

export type Meta = {
  title: string
  operators: string[]
  usvo_error?: string | null
  sla_business_days: number
  max_ready?: boolean
  dify_ready?: boolean
  web_ai_provider?: string
  web_ai_ready?: boolean
  seeded_appeals?: boolean
  uploaded_usvo?: number
  history_ai_ready?: boolean
  usvo_statuses?: string[]
  kb_ready?: boolean
  usvo_ai_ready?: boolean
  usvo_ai_kb_ready?: boolean
}

export type Sentiment = { tone?: string; label?: string; emoji?: string } | null

export type Appeal = {
  id: string
  real?: boolean
  question: string
  created_at?: string
  created_human?: string
  topic?: string
  assignee?: string | null
  status: "open" | "answered" | string
  answer?: string | null
  citizen?: { name?: string; phone?: string; username?: string; user_id?: string }
  usvo_id?: number | null
  usvo_matches?: { id: number; name?: string; phone?: string }[]
  usvo_ambiguous?: boolean
  link_by?: string
  sentiment?: Sentiment
  summary?: string
  age?: string
  age_days?: number
  deadline_at?: string
  deadline_human?: string
  is_overdue?: boolean
}

export type UsvoListItem = {
  id: number
  name?: string
  short_name?: string
  initials?: string
  status?: string
  phone?: string
  call_date?: string
  address?: string
  flags?: Record<string, boolean | string>
  head_directive?: string
  source?: string
}

export type Field = { label: string; value: string }

export type UsvoCard = UsvoListItem & {
  primary?: Field[]
  secondary?: Field[]
  extra?: Field[]
  history?: HistoryEvent[]
  history_raw?: string
  appeals?: Appeal[]
  awards?: string[]
  birth?: string
  [key: string]: unknown
}

export type HistoryEvent = {
  date?: string
  title?: string
  text?: string
  detail?: string
  org?: string
  status?: string
  style?: string
  kind?: string
}

export type Application = {
  id: string
  measure_title?: string
  status?: string
  status_label?: string
  citizen?: { name?: string; phone?: string }
  summary?: string
  created_human?: string
  downloadable?: boolean
  [key: string]: unknown
}

export type Measure = {
  id: string
  title: string
  description?: string
  /** С бэкенда приходят объекты {title, sort_order}; строки принимаются на вход. */
  documents?: (string | { title?: string; sort_order?: number })[]
  placeholders?: { key: string; label: string }[]
  llm_hint?: string
  category?: string
  active?: boolean
  has_template?: boolean
  [key: string]: unknown
}

export type AiChat = {
  id: string
  title: string
  answerType?: string
  pinned?: boolean
  createdAt?: number | string
  updatedAt?: number | string
  messageCount?: number
}

export type AiMessage = {
  id: string
  chatId: string
  role: "user" | "assistant" | string
  content: string
  createdAt?: string
  metadata?: Record<string, unknown>
}

export type Employee = {
  id: string
  name: string
  login: string
  position?: string
  phone?: string
  active?: boolean
  created_at?: string
}

export type AuditItem = {
  id: string
  user_sub?: string
  user_name?: string
  action: string
  entity?: string
  entity_id?: string
  details?: string
  at?: string
  at_human?: string
}

export type ViewId =
  | "appeals"
  | "cards"
  | "ai-chat"
  | "applications"
  | "analytics"
  | "broadcast"
  | "audit"
  | "settings"

export type AppealHistoryItem = {
  id: string
  question: string
  answer?: string | null
  status: string
  created_human?: string
  is_current?: boolean
}

export type AppealHistory = {
  profile?: { user_id?: string; username?: string; phone?: string }
  items?: AppealHistoryItem[]
  events?: { kind: string; detail?: string; created_human?: string }[]
}

export type UsvoSuggestion = {
  title: string
  detail?: string
  action?: string
  priority?: "high" | "medium" | "base" | string
  kind?: string
  docs?: string[]
  where?: string
}

export type UsvoFilters = {
  query: string
  status: string
  vbd: string
  employment: string
  contact: string
  org: string
  awards: string
  directive: string
  source: string
}

export type AnalyticsCount = { label: string; count: number }

export type AnalyticsData = {
  appeals: { day: number; week: number; month: number }
  in_person: number
  applications?: number
  total_usvo: number
  unemployed: number
  stale_contacts: number
  stale_days: number
  topics: AnalyticsCount[]
  support_measures: AnalyticsCount[]
  orgs: {
    vremya_geroev: number
    geroi_podmoskovya: number
    associaciya: number
    covered: number
    total: number
    coverage_pct: number
  }
  series?: {
    key: string
    label: string
    unit?: string
    unit_hourly?: string
    points: { date: string; count: number }[]
    hourly?: { date: string; count: number }[]
  }[]
  heatmap?: {
    area: string
    center?: { lat: number; lng: number; zoom?: number }
    ai_insight?: string
    hotspots: { name: string; lat: number; lng: number; count: number; intensity: number }[]
  }
}

export type AiAnswerType = { value: string; label: string }

export type SlaSettings = {
  sla_business_days: number
  default_business_days?: number
  min?: number
  max?: number
}

export type BroadcastAudience = {
  total: number
  subscribers: number
  max_ready: boolean
}

export type BroadcastResult = {
  sent: number
  total: number
  failed: number
  errors?: { user_id?: string; error: string }[]
}
