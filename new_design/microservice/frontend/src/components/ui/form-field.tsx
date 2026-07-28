import * as React from "react"
import { cn } from "@/lib/utils"

/** Обёртка label + input/textarea/select с отступом 8px между заголовком и полем. */
export function FormField({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("field-group", className)} {...props} />
}
