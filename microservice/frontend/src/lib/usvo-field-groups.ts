import type { LucideIcon } from "lucide-react"
import {
  Activity,
  Briefcase,
  Handshake,
  IdCard,
  Info,
  Users,
} from "lucide-react"
import type { Field } from "@/lib/types"

export type EditableField = Field & {
  /** Пока название пустое — держим поле в этой группе. */
  pinnedGroup?: string
}

export type FieldGroupDef = {
  title: string
  icon: LucideIcon
  keys: string[]
}

export const USVO_FIELD_GROUPS: FieldGroupDef[] = [
  {
    title: "Личные данные",
    icon: IdCard,
    keys: [
      "фио",
      "ф.и.о",
      "участник",
      "дата рождения",
      "семейное положение",
      "телефон",
      "адрес регистрац",
      "адрес факт",
      "дата обзвона",
    ],
  },
  {
    title: "Семья",
    icon: Users,
    keys: ["родственник", "жена", "супруг", "дети", "контакты"],
  },
  {
    title: "Статус и здоровье",
    icon: Activity,
    keys: [
      "статус",
      "инвалид",
      "состояни",
      "здоров",
      "примечание",
      "ик/",
      "чвк",
      "сизо",
      "ветеран боевых",
    ],
  },
  {
    title: "Образование и работа",
    icon: Briefcase,
    keys: [
      "образование",
      "специальн",
      "профессия",
      "трудоустр",
      "место работы",
      "должност",
      "переподготов",
      "квалификац",
      "тер.",
      "прежнее место",
    ],
  },
  {
    title: "Организации и поддержка",
    icon: Handshake,
    keys: [
      "время героя",
      "герои подмосков",
      "ассоциация",
      "мерах поддержки",
      "ответственн",
      "должностное лицо",
    ],
  },
]

export const USVO_EXTRA_GROUP = "Дополнительно"

export type IndexedField = { field: EditableField; index: number }

export type UsvoFieldGroup = {
  title: string
  icon: LucideIcon
  items: IndexedField[]
}

function findBucket(
  buckets: Array<{ title: string; icon: LucideIcon; items: IndexedField[] }>,
  title: string,
) {
  return buckets.find((b) => b.title === title)
}

/** Группировка полей карточки УСВО по смыслу (как в legacy). */
export function groupUsvoFields(fields: EditableField[]): UsvoFieldGroup[] {
  const buckets = USVO_FIELD_GROUPS.map((g) => ({
    title: g.title,
    icon: g.icon,
    items: [] as IndexedField[],
  }))
  const other: IndexedField[] = []

  fields.forEach((field, index) => {
    const item = { field, index }
    const pinned = field.pinnedGroup?.trim()
    if (pinned) {
      if (pinned === USVO_EXTRA_GROUP) {
        other.push(item)
        return
      }
      const bucket = findBucket(buckets, pinned)
      if (bucket) {
        bucket.items.push(item)
        return
      }
      other.push(item)
      return
    }

    const ll = (field.label || "").toLowerCase().trim()
    if (!ll) {
      other.push(item)
      return
    }
    let placed = false
    for (const b of buckets) {
      const def = USVO_FIELD_GROUPS.find((g) => g.title === b.title)!
      if (def.keys.some((k) => ll.includes(k))) {
        b.items.push(item)
        placed = true
        break
      }
    }
    if (!placed) other.push(item)
  })

  const allNamed = USVO_FIELD_GROUPS.map((g) => {
    return buckets.find((b) => b.title === g.title)!
  })
  return [
    ...allNamed,
    { title: USVO_EXTRA_GROUP, icon: Info, items: other },
  ]
}
