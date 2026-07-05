# Prod_v1 — снимок стабильной версии

Полный снимок проекта **до** редизайна (glassmorphism / mesh-gradient / новые ИИ-фичи).
Создан автоматически перед внесением улучшений.

- Архив: `Prod_v1.zip` (вся папка проекта, кроме `_backups/`).
- Дата снимка: см. время изменения файла `Prod_v1.zip`.

## Как откатиться к Prod_v1

1. Закрыть запущенный сервис (`uvicorn`), если он работает.
2. Распаковать `Prod_v1.zip` во временную папку.
3. Скопировать содержимое распакованной папки `Господдержка СВО` поверх рабочей
   директории проекта (заменив изменённые файлы). Либо просто заменить три файла
   фронтенда и затронутые модули — список изменённых файлов в коммите/описании правок.

PowerShell (полный откат поверх рабочей папки):

```powershell
$zip  = "$PSScriptRoot\Prod_v1.zip"
$tmp  = Join-Path $env:TEMP "prodv1_restore"
Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force
Copy-Item -Path (Join-Path $tmp 'Господдержка СВО\*') -Destination "$PSScriptRoot\.." -Recurse -Force
Remove-Item -LiteralPath $tmp -Recurse -Force
```

> После отката проверьте `microservice/config.yaml` — он входит в снимок и будет
> восстановлен до состояния Prod_v1.
