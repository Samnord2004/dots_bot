import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Any

import httpx
from sqlalchemy.orm import Session

from config import config
from database import SessionLocal, engine
from models import Base, Tariff, Meeting, Document, WorkPlan, Appeal, AppealFile
from utils import send_to_user, send_to_chat

# Создаём папку для загруженных файлов, если её нет
os.makedirs("uploads", exist_ok=True)

# Создаём таблицы в БД
Base.metadata.create_all(bind=engine)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_API_URL = "https://platform-api.max.ru"
HEADERS = {"Authorization": config.MAX_BOT_TOKEN, "Content-Type": "application/json"}

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

async def download_file(url: str, file_path: str):
    """Скачивает файл по URL и сохраняет в указанный путь."""
    logger.info(f"Скачивание файла: {url} -> {file_path}")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            with open(file_path, "wb") as f:
                f.write(response.content)
            logger.info(f"Файл сохранён: {file_path}")
            return True
        except Exception as e:
            logger.exception(f"Ошибка скачивания файла {url}: {e}")
            return False

async def handle_start(user_id: int):
    msg = (
        "🏠 *Добро пожаловать в бот ДТСН Пенсионер!*\n\n"
        "Доступные команды (напишите слово):\n"
        "📊 *Тарифы*\n🗓 *Собрание*\n📄 *Протоколы и смета*\n"
        "📁 *Облако*\n📝 *План работ*\n✉️ *Обратиться*\n🏆 *Рейтинг*\n\n"
        "Просто напишите нужную команду."
    )
    await send_to_user(user_id, msg)

async def handle_tariffs(user_id: int):
    with next(get_db()) as db:
        tariffs = db.query(Tariff).all()
        if tariffs:
            text = "📋 *Тарифы:*\n" + "\n".join(f"• {t.service_name}: {t.price:.2f} руб./{t.unit}" for t in tariffs)
        else:
            text = "Тарифы не установлены."
    await send_to_user(user_id, text)

async def handle_meeting(user_id: int):
    with next(get_db()) as db:
        meeting = db.query(Meeting).order_by(Meeting.date.desc()).first()
        if meeting and meeting.date > datetime.now():
            text = f"🗓 *Собрание:* {meeting.date.strftime('%d.%m.%Y %H:%M')}\n{meeting.description or ''}"
            if meeting.agenda_items:
                text += "\n\n*Повестка дня:*"
                for idx, item in enumerate(meeting.agenda_items, 1):
                    text += f"\n{idx}. {item.get('text')}"
                    if item.get('files'):
                        text += f"\n   Файлы: {', '.join(item['files'])}"
        else:
            text = "Нет запланированных собраний."
    await send_to_user(user_id, text)

async def handle_docs(user_id: int):
    with next(get_db()) as db:
        docs = db.query(Document).all()
        if docs:
            text = "📂 *Документы:*\n" + "\n".join(f"• {d.title}: [Скачать]({d.file_url})" for d in docs)
        else:
            text = "Документы не загружены."
    await send_to_user(user_id, text)

async def handle_cloud(user_id: int):
    cloud_url = getattr(config, 'YANDEX_DISK_SHARE_URL', None)
    if not cloud_url:
        cloud_url = "https://disk.yandex.ru/d/Sv_nzO6hWTHshA"
    await send_to_user(user_id, f"📁 *Облачное хранилище документов:*\n{cloud_url}")

async def handle_workplan(user_id: int):
    with next(get_db()) as db:
        tasks = db.query(WorkPlan).all()
        if tasks:
            lines = ["📋 *План работ:*"]
            for t in tasks:
                emoji = {"planned":"⏳","in_progress":"🔄","completed":"✅"}.get(t.status,"❓")
                period = f" ({t.start_date.strftime('%d.%m.%Y')} - {t.end_date.strftime('%d.%m.%Y')})" if t.start_date and t.end_date else ""
                lines.append(f"{emoji} {t.task_name}{period} — {t.planned_budget} руб. ({t.completion_percent}%)")
            text = "\n".join(lines)
        else:
            text = "План работ пуст."
    await send_to_user(user_id, text)

async def handle_rating(user_id: int):
    avg = 0
    month_ago = datetime.now() - timedelta(days=30)
    with next(get_db()) as db:
        appeals = db.query(Appeal).filter(
            Appeal.answered_at >= month_ago,
            Appeal.satisfaction_score.isnot(None)
        ).all()
        if appeals:
            avg = sum(a.satisfaction_score for a in appeals) / len(appeals)
    stars = "⭐" * int(avg) + ("✨" if avg - int(avg) >= 0.5 else "")
    text = f"🏆 *Рейтинг правления* за 30 дней: {avg:.1f}/5\n{stars or '☆☆☆☆☆'}"
    await send_to_user(user_id, text)

# ---------- Обработка обращений с фото ----------
user_states: Dict[int, Dict[str, Any]] = {}

async def handle_appeal_start(user_id: int):
    user_states[user_id] = {"step": "fullname"}
    await send_to_user(user_id, "📝 *Обращение в правление*\nВведите ваши ФИО (полностью):")

async def finalize_appeal(user_id: int, state: dict):
    try:
        with next(get_db()) as db:
            appeal = Appeal(
                chat_id=str(user_id),
                full_name=state["fullname"],
                plot_number=state["plot"],
                question=state["question"],
                status="waiting",
                created_at=datetime.now(),
                reminder_sent=False
            )
            db.add(appeal)
            db.commit()
            appeal_id = appeal.id
            # Сохраняем файлы, если они есть
            if "file_paths" in state and state["file_paths"]:
                for file_path in state["file_paths"]:
                    appeal_file = AppealFile(appeal_id=appeal_id, file_path=file_path)
                    db.add(appeal_file)
                db.commit()
                logger.info(f"Сохранено {len(state['file_paths'])} файлов для обращения #{appeal_id}")
        # Формируем уведомление в чат правления
        board_chat_id = getattr(config, 'BOARD_CHAT_ID', None)
        board_msg = (
            f"📨 *Новое обращение #{appeal_id}*\n"
            f"ФИО: {state['fullname']}\n"
            f"Участок: {state['plot']}\n"
            f"Вопрос: {state['question']}"
        )
        if "file_paths" in state and state["file_paths"]:
            base_url = getattr(config, 'BASE_URL', 'http://127.0.0.1:8000')
            files_links = []
            for p in state["file_paths"]:
                file_url = f"{base_url}/uploads/{os.path.basename(p)}"
                files_links.append(file_url)
            board_msg += f"\n📎 Файлы:\n" + "\n".join(files_links)
        board_msg += f"\n\nДля ответа используйте команду: `/answer {appeal_id} текст ответа`"

        if board_chat_id:
            success = await send_to_chat(int(board_chat_id), board_msg)
            if not success:
                admin_id = getattr(config, 'ADMIN_USER_ID', None)
                if admin_id:
                    await send_to_user(int(admin_id), board_msg)
                    logger.warning(f"Уведомление об обращении #{appeal_id} отправлено админу в личку")
        else:
            logger.warning("BOARD_CHAT_ID не задан")
        await send_to_user(user_id, "✅ Ваше обращение принято. Ответ придёт в течение 48 часов. Спасибо!")
        logger.info(f"Обращение #{appeal_id} сохранено от user {user_id}")
    except Exception as e:
        logger.exception(f"Ошибка при сохранении обращения: {e}")
        await send_to_user(user_id, "❌ Произошла ошибка. Попробуйте позже.")

async def process_appeal_state(user_id: int, text: str = None, attachments: list = None):
    state = user_states.get(user_id)
    if not state:
        return
    step = state["step"]
    if step == "fullname":
        state["fullname"] = text
        state["step"] = "plot"
        await send_to_user(user_id, "🏷 Номер вашего участка (например, 42):")
    elif step == "plot":
        state["plot"] = text
        state["step"] = "question"
        await send_to_user(user_id, "✍️ Напишите ваш вопрос или предложение:")
    elif step == "question":
        state["question"] = text
        # Переход к шагу фото
        kb = {
            "keyboard": [[{"text": "📸 Пропустить"}]],
            "resize_keyboard": True,
            "one_time_keyboard": True
        }
        await send_to_user(
            user_id,
            "📸 Можете приложить фото (порыв трубы, показания счётчика и т.п.).\nОтправьте изображение или нажмите «📸 Пропустить»",
            reply_markup=kb
        )
        state["step"] = "photo"
    elif step == "photo":
        logger.info(f"Шаг photo: attachments={attachments}")
        if attachments:
            file_paths = []
            for att in attachments:
                if att.get("type") == "image":
                    payload = att.get("payload", {})
                    url = payload.get("url")
                    if url:
                        filename = f"uploads/{user_id}_{int(datetime.now().timestamp())}_{len(file_paths)}.jpg"
                        if await download_file(url, filename):
                            file_paths.append(filename)
            state["file_paths"] = file_paths
        # Завершаем обращение
        await finalize_appeal(user_id, state)
        await send_to_user(user_id, "✅ Обращение отправлено!", reply_markup={"remove_keyboard": True})
        if user_id in user_states:
            del user_states[user_id]

# ---------- Обработка команд в чате правления ----------
async def handle_board_command(chat_id: int, text: str):
    if text.startswith("/answer"):
        parts = text.split(maxsplit=2)
        if len(parts) >= 3:
            _, appeal_id_str, response_text = parts
            if appeal_id_str.isdigit():
                await process_answer(int(appeal_id_str), response_text)
                await send_to_chat(chat_id, f"✅ Ответ на обращение #{appeal_id_str} отправлен.")
            else:
                await send_to_chat(chat_id, "❌ Некорректный ID обращения.")
        else:
            await send_to_chat(chat_id, "❌ Формат: `/answer [ID] [текст ответа]`")
    else:
        pass

async def process_answer(appeal_id: int, response_text: str):
    db = SessionLocal()
    try:
        appeal = db.query(Appeal).filter(Appeal.id == appeal_id).first()
        if not appeal:
            logger.warning(f"Обращение #{appeal_id} не найдено")
            return
        appeal.board_response = response_text
        appeal.status = "answered"
        appeal.answered_at = datetime.now()
        appeal.reminder_sent = False
        db.commit()
        await send_to_user(
            int(appeal.chat_id),
            f"📩 **Ответ правления на обращение #{appeal.id}**\n\n{response_text}\n\nПожалуйста, оцените качество ответа по шкале от 1 до 5 (отправьте просто цифру)."
        )
        logger.info(f"Ответ на обращение #{appeal_id} отправлен")
    except Exception as e:
        logger.exception(f"Ошибка при ответе на обращение: {e}")
    finally:
        db.close()

# ---------- Основной цикл long polling ----------
async def process_updates():
    marker = None
    async with httpx.AsyncClient(timeout=35.0) as client:
        while True:
            try:
                params = {"limit": 100, "timeout": 30}
                if marker:
                    params["marker"] = marker
                resp = await client.get(f"{BASE_API_URL}/updates", params=params, headers={"Authorization": config.MAX_BOT_TOKEN})
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict):
                        updates = data.get("updates", [])
                        if updates:
                            marker = data.get("marker")
                            for upd in updates:
                                logger.debug(f"RAW update: {upd}")
                                if upd.get("update_type") == "message_created":
                                    msg = upd.get("message", {})
                                    chat_id = msg.get("recipient", {}).get("chat_id")
                                    user_id = msg.get("sender", {}).get("user_id")
                                    if msg.get("sender", {}).get("is_bot"):
                                        continue
                                    text = msg.get("body", {}).get("text", "").strip()
                                    attachments = msg.get("body", {}).get("attachments", [])
                                    if not user_id:
                                        continue
                                    # Если сообщение из чата правления
                                    if config.BOARD_CHAT_ID and str(chat_id) == str(config.BOARD_CHAT_ID):
                                        if text.startswith("/"):
                                            await handle_board_command(chat_id, text)
                                        continue
                                    # Личные сообщения
                                    if user_id in user_states:
                                        if text == "📸 Пропустить" and user_states[user_id].get("step") == "photo":
                                            await finalize_appeal(user_id, user_states[user_id])
                                            await send_to_user(user_id, "✅ Обращение отправлено без фото.", reply_markup={"remove_keyboard": True})
                                            del user_states[user_id]
                                        else:
                                            await process_appeal_state(user_id, text, attachments)
                                    else:
                                        # Оценка ответа
                                        with next(get_db()) as db:
                                            last_appeal = db.query(Appeal).filter(
                                                Appeal.chat_id == str(user_id),
                                                Appeal.status == "answered",
                                                Appeal.satisfaction_score.is_(None)
                                            ).order_by(Appeal.answered_at.desc()).first()
                                            if last_appeal and text.isdigit() and 1 <= int(text) <= 5:
                                                score = int(text)
                                                last_appeal.satisfaction_score = score
                                                db.commit()
                                                await send_to_user(user_id, f"🙏 Спасибо за вашу оценку: {score}/5!")
                                                continue
                                        # Обычные команды
                                        low = text.lower()
                                        if text == "/start":
                                            await handle_start(user_id)
                                        elif "тариф" in low:
                                            await handle_tariffs(user_id)
                                        elif "собрани" in low:
                                            await handle_meeting(user_id)
                                        elif "протокол" in low or "документ" in low or "смет" in low:
                                            await handle_docs(user_id)
                                        elif "облак" in low:
                                            await handle_cloud(user_id)
                                        elif "план" in low or "работ" in low:
                                            await handle_workplan(user_id)
                                        elif "рейтинг" in low or "оценк" in low:
                                            await handle_rating(user_id)
                                        elif "обрати" in low or "вопрос" in low:
                                            await handle_appeal_start(user_id)
                                        else:
                                            await send_to_user(user_id, "Неизвестная команда. Напишите /start для списка команд.")
                                elif upd.get("update_type") == "bot_started":
                                    user_id = upd.get("user", {}).get("id")
                                    if user_id:
                                        await handle_start(user_id)
                elif resp.status_code == 429:
                    logger.warning("Rate limit, waiting 5s")
                    await asyncio.sleep(5)
                else:
                    logger.error(f"Updates error {resp.status_code}: {resp.text}")
                    await asyncio.sleep(5)
            except httpx.ReadTimeout:
                continue
            except Exception as e:
                logger.exception(f"Loop error: {e}")
                await asyncio.sleep(5)

async def main():
    logger.info("🚀 Бот ДТСН Пенсионер запущен")
    from scheduler import start_scheduler
    scheduler = start_scheduler()
    await process_updates()

if __name__ == "__main__":
    asyncio.run(main())
