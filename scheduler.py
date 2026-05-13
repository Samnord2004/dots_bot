from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from database import SessionLocal
from models import Appeal
from config import config
from utils import send_to_user, send_to_chat
import httpx
import asyncio

BASE_API_URL = "https://platform-api.max.ru"
HEADERS = {"Authorization": config.MAX_BOT_TOKEN, "Content-Type": "application/json"}

async def send_inline_keyboard(chat_id: int, text: str, keyboard: dict):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "format": "markdown",
        "attachments": [{"type": "inline_keyboard", "payload": keyboard}]
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(f"{BASE_API_URL}/messages", json=payload, headers=HEADERS)
            if resp.status_code != 200:
                print(f"Send keyboard error: {resp.status_code} - {resp.text}")
        except Exception as e:
            print(f"Exception in send_inline_keyboard: {e}")

async def check_overdue_appeals():
    print("🕒 [Scheduler] Проверка просроченных обращений...")
    db = SessionLocal()
    try:
        deadline = datetime.now() - timedelta(hours=48)
        overdue = db.query(Appeal).filter(
            Appeal.status == "waiting",
            Appeal.created_at <= deadline,
            Appeal.reminder_sent == False
        ).all()
        
        for appeal in overdue:
            # Отправляем напоминание в чат правления
            if config.BOARD_CHAT_ID:
                await send_to_chat(
                    int(config.BOARD_CHAT_ID),
                    f"⚠️ **ВНИМАНИЕ!** Обращение #{appeal.id} от {appeal.full_name} (уч. {appeal.plot_number}) ожидает ответа более 48 часов!\nВопрос: {appeal.question}\nПожалуйста, ответьте через команду `/answer {appeal.id} текст`."
                )
            # Отправляем пользователю предложение повторно отправить или пожаловаться
            keyboard = {
                "inline_keyboard": [
                    [
                        {"type": "callback", "text": "🔄 Отправить заново", "payload": {"action": "resend", "appeal_id": appeal.id}},
                        {"type": "callback", "text": "⚠️ Пожаловаться", "payload": {"action": "complain", "appeal_id": appeal.id}}
                    ]
                ]
            }
            await send_inline_keyboard(
                int(appeal.chat_id),
                "⏰ **Вам не ответили в течение 48 часов.**\nВыберите действие:",
                keyboard
            )
            appeal.reminder_sent = True
            db.commit()
            print(f"📨 [Scheduler] Напоминание по обращению #{appeal.id} отправлено")
    finally:
        db.close()

def start_scheduler():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_overdue_appeals, 'interval', minutes=5)
    scheduler.start()
    return scheduler
