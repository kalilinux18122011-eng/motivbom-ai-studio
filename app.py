"""Standalone Telegram Mini App for @motivbom.ai. It has no dependency on motivbom-bot."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
from aiohttp import web

ROOT = Path(__file__).parent
TOKEN = os.getenv("TG_BOT_TOKEN", "")
LLM_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE = os.getenv("LLM_BASE_URL", "https://api.x.ai/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "grok-4.7")
APP_URL = os.getenv("MINI_APP_URL", "").rstrip("/")


def telegram_ok(raw: str) -> bool:
    if not raw or not TOKEN:
        return False
    values = dict(parse_qsl(raw, keep_blank_values=True))
    received = values.pop("hash", "")
    try:
        auth_date = int(values["auth_date"])
    except (KeyError, ValueError):
        return False
    if auth_date < time.time() - 86400 or auth_date > time.time() + 300:
        return False
    check = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(TOKEN.encode(), b"WebAppData", hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


async def ask(prompt: str) -> str:
    if not LLM_KEY:
        raise RuntimeError("LLM_API_KEY ещё не добавлен в новый сервис")
    body = {"model": LLM_MODEL, "temperature": 0.7, "max_tokens": 1800, "messages": [
        {"role": "system", "content": "Ты работаешь только с Instagram @motivbom.ai. Не выдумывай статистику, тренды или содержание ролика. Не давай медицинских обещаний."},
        {"role": "user", "content": prompt},
    ]}
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(f"{LLM_BASE}/chat/completions", headers={"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"}, json=body)
    if response.status_code >= 400:
        raise RuntimeError(f"AI API вернул {response.status_code}")
    return response.json()["choices"][0]["message"]["content"].strip()


PROMPTS = {
    "analyst": """Ты Аналитик Reels. Входные реальные данные: {context}\n\nСоставь отчёт: наблюдаемые сильные стороны, косяки, гипотезы (явно пометь как гипотезы), что улучшить в первых 2 секундах и какие показатели нужно собрать по 5–10 роликам. Если ролик не приложен или нет retention — прямо напиши ограничение.""",
    "scriptwriter": """Ты Сценарист Reels для @motivbom.ai. Входной отчёт или задача: {context}\n\nНапиши 3 разных сценария 15–25 секунд. Для каждого: hook 0–2с, последовательность кадров, текст озвучки, CTA и caption. Не повторяй утверждения без данных и не давай медицинских обещаний.""",
    "director": """Ты Контент-директор @motivbom.ai. Входной материал: {context}\n\nВыбери сильнейшую идею и подготовь: первый кадр/обложку, улучшенный hook, CTA, caption, 5–7 хэштегов, A/B-версии и конкретную таблицу метрик на 24 часа. Не обещай охватов.""",
}


async def home(_: web.Request) -> web.FileResponse:
    return web.FileResponse(ROOT / "static" / "index.html")


async def run_agent(request: web.Request) -> web.Response:
    form = await request.post()
    if not telegram_ok(str(form.get("init_data", ""))):
        return web.json_response({"error": "Открой приложение кнопкой внутри Telegram."}, status=401)
    name = request.match_info["name"]
    if name not in PROMPTS:
        return web.json_response({"error": "Неизвестный агент."}, status=404)
    context = str(form.get("context", "")).strip()
    if name == "analyst":
        stats = ", ".join(f"{label}: {str(form.get(key, '')).strip()}" for key, label in [("views", "просмотры"), ("likes", "лайки"), ("comments", "комментарии"), ("shares", "репосты"), ("saves", "сохранения")] if str(form.get(key, "")).strip())
        context = f"{context}\nМетрики: {stats or 'не переданы'}"
    if not context:
        return web.json_response({"error": "Добавь описание, отчёт или метрики."}, status=400)
    try:
        answer = await ask(PROMPTS[name].format(context=context))
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=502)
    return web.json_response({"answer": answer})


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True, "project": "motivbom.ai-studio", "llm_configured": bool(LLM_KEY)})


async def menu() -> None:
    if not TOKEN or not APP_URL:
        return
    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(f"https://api.telegram.org/bot{TOKEN}/setChatMenuButton", json={"menu_button": {"type": "web_app", "text": "MotivBom AI Studio", "web_app": {"url": APP_URL}}})


async def startup(app: web.Application) -> None:
    app["menu"] = asyncio.create_task(menu())


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", home)
    app.router.add_get("/health", health)
    app.router.add_post("/api/{name}", run_agent)
    app.on_startup.append(startup)
    return app


web.run_app(make_app(), host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
