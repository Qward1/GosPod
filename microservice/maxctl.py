"""Утилита настройки и диагностики MAX-бота.

Запуск (из папки microservice, с заполненным config.yaml):

    python maxctl.py me                      # проверить токен: инфо о боте
    python maxctl.py subscriptions           # показать текущие подписки на вебхук
    python maxctl.py subscribe https://ваш-домен/max/webhook
    python maxctl.py unsubscribe https://ваш-домен/max/webhook
    python maxctl.py poll                     # long polling: принимать события без вебхука
                                              # (для локальной отладки; Ctrl+C — стоп)

Почему «бот не отвечает»: MAX сам по себе НЕ шлёт события в сервис, пока вы не
зарегистрируете вебхук (subscribe) ИЛИ не запустите poll. Вебхук требует публичного
HTTPS с доверенным сертификатом. Для проверки на localhost удобнее `poll`.
"""
from __future__ import annotations

import asyncio
import json
import sys

from app.config import load_config
from app.max.bot_logic import MaxBot
from app.max.client import MaxClient
from app.max.dify_client import DifyClient
from app.max.polling import poll_loop
from app.max.store import Store


def _pretty(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


async def cmd_me(client: MaxClient) -> None:
    print(_pretty(await client.get_me()))


async def cmd_subscriptions(client: MaxClient) -> None:
    print(_pretty(await client.get_subscriptions()))


async def cmd_subscribe(client: MaxClient, url: str) -> None:
    print(_pretty(await client.subscribe(url)))


async def cmd_unsubscribe(client: MaxClient, url: str) -> None:
    print(_pretty(await client.unsubscribe(url)))


async def cmd_poll(cfg, client: MaxClient) -> None:
    """Long polling: получаем апдейты и прогоняем через ту же логику бота."""
    store = Store(cfg.storage.sqlite_path)
    dify = DifyClient(
        base_url=cfg.dify.base_url,
        app_key=cfg.dify.max_app_key,
        dataset_api_key=cfg.dify.dataset.api_key,
        dataset_id=cfg.dify.dataset.dataset_id,
    )
    bot = MaxBot(cfg, store, client, dify)

    print("Long polling запущен. Напишите боту в MAX. Ctrl+C — стоп.")
    await poll_loop(bot, client)


async def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cfg = load_config()
    client = MaxClient(cfg.max.api_base_url, cfg.max.bot_token)
    cmd = sys.argv[1]

    if cmd == "me":
        await cmd_me(client)
    elif cmd == "subscriptions":
        await cmd_subscriptions(client)
    elif cmd == "subscribe" and len(sys.argv) >= 3:
        await cmd_subscribe(client, sys.argv[2])
    elif cmd == "unsubscribe" and len(sys.argv) >= 3:
        await cmd_unsubscribe(client, sys.argv[2])
    elif cmd == "poll":
        await cmd_poll(cfg, client)
    else:
        print(__doc__)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nОстановлено.")
