# Авторизация контакт-центра — Ory Kratos (self-hosted)

Вход в веб-кабинет сделан в стиле ЕСИА/Госуслуг (экран `static/login.html`,
бренд «Контакт-центр»). Проверка учётных данных — через **Ory Kratos**, который
разворачивается рядом с микросервисом. Cookie-сессия самого кабинета — собственная
(HMAC, `auth.session_secret`), поэтому домены Kratos и кабинета пробрасывать не нужно.

## Два режима (config.yaml → `auth.mode`)

| mode | Когда | Что нужно |
|------|-------|-----------|
| `demo` (по умолчанию) | демо/первый запуск | ничего — вход по `auth.demo_users` |
| `kratos` | боевой self-hosted | поднятый Kratos (этот compose) |

В demo-режиме вход работает сразу:
- `operator@mosreg.ru` / `kontakt2026`
- `admin@mosreg.ru` / `admin2026`

## Запуск Kratos

```bash
cd microservice/deploy/kratos
docker compose up -d                 # поднимет миграции + kratos (4433 public, 4434 admin)

# создать сотрудников:
chmod +x create-user.sh
./create-user.sh operator@mosreg.ru "kontakt2026" "Иванова О. П." Оператор
./create-user.sh admin@mosreg.ru    "admin2026"   "Петров С. А."  Администратор
```

Затем в `config.yaml`:

```yaml
auth:
  mode: "kratos"
  kratos_public_url: "http://localhost:4433"   # или http://kratos:4433 в общей docker-сети
  session_secret: "<длинная-случайная-строка>"
```

Перезапустите микросервис — теперь логин на `/login.html` проверяется Kratos.

## Как это работает в коде

`app/auth/service.py::_authenticate_kratos` использует **API login flow**:
`GET {public}/self-service/login/api` → `POST {public}/self-service/login?flow=...`
с `{method: "password", identifier, password}`. Это не требует браузерных
redirect/CSRF — удобно вызывать из бэкенда. ФИО и роль берутся из `identity.traits`
(схема — `identity.schema.json`).

> На проде смените секреты в `kratos.yml` (`secrets.cookie`, `secrets.cipher`) и
> `auth.session_secret`, выключите флаг `--dev`, поставьте Postgres вместо SQLite.
