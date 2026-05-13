import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Any

from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager

from database import get_db, engine
from models import Base, Tariff, Meeting, Document, WorkPlan, Appeal, AppealFile
from config import config
from utils import send_to_user, send_to_chat
import httpx

# Создаём папки
os.makedirs("uploads", exist_ok=True)

# Создаём таблицы в БД
Base.metadata.create_all(bind=engine)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_API_URL = "https://platform-api.max.ru"
HEADERS = {"Authorization": config.MAX_BOT_TOKEN, "Content-Type": "application/json"}

async def download_file(url: str, file_path: str):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            with open(file_path, "wb") as f:
                f.write(response.content)
            logger.info(f"Файл сохранён: {file_path}")
            return True
        except Exception as e:
            logger.exception(f"Ошибка скачивания: {e}")
            return False

# ---------- Состояния пользователей ----------
user_states: Dict[int, Dict[str, Any]] = {}

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
            if "file_paths" in state and state["file_paths"]:
                for file_path in state["file_paths"]:
                    appeal_file = AppealFile(appeal_id=appeal_id, file_path=file_path)
                    db.add(appeal_file)
                db.commit()
        board_chat_id = getattr(config, 'BOARD_CHAT_ID', None)
        base_url = getattr(config, 'BASE_URL', None)
        if not base_url:
            logger.error("BASE_URL не задан! Укажите его в переменных окружения Bothost.")
            base_url = "https://ваш-бот.bothost.tech"  # заглушка, но лучше ошибку

        board_msg = (
            f"📨 *Новое обращение #{appeal_id}*\n"
            f"ФИО: {state['fullname']}\n"
            f"Участок: {state['plot']}\n"
            f"Вопрос: {state['question']}"
        )
        if "file_paths" in state and state["file_paths"]:
            files_links = "\n".join([f"{base_url}/uploads/{os.path.basename(p)}" for p in state["file_paths"]])
            board_msg += f"\n📎 Файлы:\n{files_links}"
        board_msg += f"\n\nДля ответа используйте команду: `/answer {appeal_id} текст ответа`"
        if board_chat_id:
            await send_to_chat(int(board_chat_id), board_msg)
        await send_to_user(user_id, "✅ Ваше обращение принято. Ответ придёт в течение 48 часов.")
        logger.info(f"Обращение #{appeal_id} сохранено")
    except Exception as e:
        logger.exception(f"Ошибка: {e}")
        await send_to_user(user_id, "❌ Ошибка. Попробуйте позже.")

async def process_appeal_state(user_id: int, text: str = None, attachments: list = None):
    state = user_states.get(user_id)
    if not state:
        return
    step = state["step"]
    if step == "fullname":
        state["fullname"] = text
        state["step"] = "plot"
        await send_to_user(user_id, "🏷 Номер участка:")
    elif step == "plot":
        state["plot"] = text
        state["step"] = "question"
        await send_to_user(user_id, "✍️ Ваш вопрос:")
    elif step == "question":
        state["question"] = text
        kb = {"keyboard": [[{"text": "📸 Пропустить"}]], "resize_keyboard": True, "one_time_keyboard": True}
        await send_to_user(user_id, "📸 Приложите фото или нажмите «Пропустить»", reply_markup=kb)
        state["step"] = "photo"
    elif step == "photo":
        if attachments:
            file_paths = []
            for att in attachments:
                if att.get("type") == "image":
                    url = att.get("payload", {}).get("url")
                    if url:
                        filename = f"uploads/{user_id}_{int(datetime.now().timestamp())}.jpg"
                        if await download_file(url, filename):
                            file_paths.append(filename)
            state["file_paths"] = file_paths
        await finalize_appeal(user_id, state)
        await send_to_user(user_id, "✅ Обращение отправлено!", reply_markup={"remove_keyboard": True})
        if user_id in user_states:
            del user_states[user_id]

async def handle_answer(chat_id: int, text: str):
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        await send_to_chat(chat_id, "❌ Формат: `/answer ID текст`")
        return
    _, appeal_id_str, response_text = parts
    if not appeal_id_str.isdigit():
        await send_to_chat(chat_id, "❌ Некорректный ID")
        return
    appeal_id = int(appeal_id_str)
    from database import SessionLocal
    db = SessionLocal()
    try:
        appeal = db.query(Appeal).filter(Appeal.id == appeal_id).first()
        if not appeal:
            await send_to_chat(chat_id, f"❌ Обращение #{appeal_id} не найдено")
            return
        appeal.board_response = response_text
        appeal.status = "answered"
        appeal.answered_at = datetime.now()
        appeal.reminder_sent = False
        db.commit()
        await send_to_user(
            int(appeal.chat_id),
            f"📩 **Ответ на обращение #{appeal.id}**\n\n{response_text}\n\nОцените ответ цифрой от 1 до 5."
        )
        await send_to_chat(chat_id, f"✅ Ответ на обращение #{appeal_id} отправлен.")
    except Exception as e:
        logger.exception(f"Ошибка ответа: {e}")
        await send_to_chat(chat_id, "❌ Ошибка")
    finally:
        db.close()

# ---------- Красивое главное меню (возвращаем как было) ----------
async def send_main_menu(user_id: int):
    msg = (
        "🏠 *Добро пожаловать в бот ДТСН Пенсионер!*\n\n"
        "Доступные команды (напишите слово):\n"
        "📊 *Тарифы*\n🗓 *Собрание*\n📄 *Протоколы и смета*\n"
        "📁 *Облако*\n📝 *План работ*\n✉️ *Обратиться*\n🏆 *Рейтинг*\n\n"
        "Просто напишите нужную команду."
    )
    await send_to_user(user_id, msg)

async def handle_message(user_id: int, text: str, attachments: list):
    if user_id in user_states:
        if text == "📸 Пропустить" and user_states[user_id].get("step") == "photo":
            await finalize_appeal(user_id, user_states[user_id])
            await send_to_user(user_id, "Обращение без фото.", reply_markup={"remove_keyboard": True})
            del user_states[user_id]
        else:
            await process_appeal_state(user_id, text, attachments)
    else:
        low = text.lower()
        if text == "/start":
            await send_main_menu(user_id)
        elif "тариф" in low:
            with next(get_db()) as db:
                tariffs = db.query(Tariff).all()
                if tariffs:
                    msg = "📋 *Тарифы:*\n" + "\n".join(f"• {t.service_name}: {t.price} руб./{t.unit}" for t in tariffs)
                else:
                    msg = "Тарифы не установлены"
            await send_to_user(user_id, msg)
        elif "собрани" in low:
            with next(get_db()) as db:
                meeting = db.query(Meeting).order_by(Meeting.date.desc()).first()
                if meeting and meeting.date > datetime.now():
                    msg = f"🗓 *Собрание:* {meeting.date.strftime('%d.%m.%Y %H:%M')}\n{meeting.description or ''}"
                    if meeting.agenda_items:
                        msg += "\n\n*Повестка дня:*"
                        for idx, item in enumerate(meeting.agenda_items, 1):
                            msg += f"\n{idx}. {item.get('text')}"
                            if item.get('files'):
                                msg += f"\n   Файлы: {', '.join(item['files'])}"
                else:
                    msg = "Нет собраний"
            await send_to_user(user_id, msg)
        elif "протокол" in low or "документ" in low:
            with next(get_db()) as db:
                docs = db.query(Document).all()
                if docs:
                    msg = "📂 *Документы:*\n" + "\n".join(f"• {d.title}: [Скачать]({d.file_url})" for d in docs)
                else:
                    msg = "Нет документов"
            await send_to_user(user_id, msg)
        elif "облак" in low:
            cloud_url = getattr(config, 'YANDEX_DISK_SHARE_URL', 'https://disk.yandex.ru/d/Sv_nzO6hWTHshA')
            await send_to_user(user_id, f"📁 *Облако:*\n{cloud_url}")
        elif "план" in low or "работ" in low:
            with next(get_db()) as db:
                tasks = db.query(WorkPlan).all()
                if tasks:
                    lines = ["📋 *План работ:*"]
                    for t in tasks:
                        emoji = {"planned":"⏳","in_progress":"🔄","completed":"✅"}.get(t.status,"❓")
                        period = f" ({t.start_date.strftime('%d.%m.%Y')} - {t.end_date.strftime('%d.%m.%Y')})" if t.start_date and t.end_date else ""
                        lines.append(f"{emoji} {t.task_name}{period} — {t.planned_budget} руб. ({t.completion_percent}%)")
                    msg = "\n".join(lines)
                else:
                    msg = "План работ пуст"
            await send_to_user(user_id, msg)
        elif "рейтинг" in low:
            month_ago = datetime.now() - timedelta(days=30)
            with next(get_db()) as db:
                appeals = db.query(Appeal).filter(
                    Appeal.answered_at >= month_ago,
                    Appeal.satisfaction_score.isnot(None)
                ).all()
                if appeals:
                    avg = sum(a.satisfaction_score for a in appeals) / len(appeals)
                    stars = "⭐" * int(avg) + ("✨" if avg - int(avg) >= 0.5 else "")
                    msg = f"🏆 Рейтинг за 30 дней: {avg:.1f}/5\n{stars or '☆☆☆☆☆'}"
                else:
                    msg = "Нет оценок за 30 дней"
            await send_to_user(user_id, msg)
        elif "обрати" in low or "вопрос" in low:
            user_states[user_id] = {"step": "fullname"}
            await send_to_user(user_id, "📝 Введите ваши ФИО:")
        else:
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
                    await send_to_user(user_id, f"🙏 Спасибо за оценку {score}/5!")
                else:
                    await send_to_user(user_id, "Неизвестная команда. Напишите /start")

# ---------- Веб-сервер FastAPI ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    from scheduler import start_scheduler
    scheduler = start_scheduler()
    yield
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

@app.post("/webhook")
async def webhook(request: Request):
    update = await request.json()
    logger.info(f"Webhook: {update}")
    if "message" in update:
        msg = update["message"]
        chat_id = msg.get("recipient", {}).get("chat_id")
        user_id = msg.get("sender", {}).get("user_id")
        if msg.get("sender", {}).get("is_bot"):
            return {"ok": True}
        text = msg.get("body", {}).get("text", "").strip()
        attachments = msg.get("body", {}).get("attachments", [])
        if config.BOARD_CHAT_ID and str(chat_id) == str(config.BOARD_CHAT_ID):
            if text.startswith("/answer"):
                await handle_answer(chat_id, text)
        else:
            await handle_message(user_id, text, attachments)
    return {"ok": True}

# ---------- Админ-панель (полная, с корректными ссылками на фото) ----------
def verify_token(token: str):
    return token == config.ADMIN_SECRET_KEY

@app.get("/", response_class=HTMLResponse)
async def admin_index(request: Request, token: str, db: Session = Depends(get_db)):
    if not verify_token(token):
        return HTMLResponse("<h1>403 Forbidden</h1>", status_code=403)
    base_url = getattr(config, 'BASE_URL', '')
    tariffs = db.query(Tariff).all()
    meetings = db.query(Meeting).order_by(Meeting.date.desc()).all()
    docs = db.query(Document).all()
    works = db.query(WorkPlan).all()
    waiting_appeals = db.query(Appeal).filter(Appeal.status == "waiting").order_by(Appeal.created_at.desc()).all()
    # Генерируем HTML
    appeals_html = ""
    for a in waiting_appeals:
        files = db.query(AppealFile).filter(AppealFile.appeal_id == a.id).all()
        files_html = ""
        for f in files:
            file_url = f"{base_url}/uploads/{os.path.basename(f.file_path)}"
            files_html += f'<div><strong>Файлы:</strong> <a href="{file_url}">{os.path.basename(f.file_path)}</a></div>'
        appeals_html += f'''
        <div class="appeal">
            <div><strong>#{a.id}</strong> от {a.created_at.strftime("%d.%m.%Y %H:%M")}</div>
            <div><strong>ФИО:</strong> {a.full_name}</div>
            <div><strong>Участок:</strong> {a.plot_number}</div>
            <div><strong>Вопрос:</strong> {a.question}</div>
            {files_html}
            <form method="post" action="/answer_appeal">
                <input type="hidden" name="token" value="{token}">
                <input type="hidden" name="appeal_id" value="{a.id}">
                <textarea name="response_text" rows="3" cols="50" placeholder="Введите ответ..."></textarea><br>
                <button type="submit">📤 Отправить ответ</button>
            </form>
        </div>
        '''
    if not appeals_html:
        appeals_html = "<p>Нет обращений</p>"
    tariffs_html = "<ul>"
    for t in tariffs:
        tariffs_html += f'<li>{t.service_name}: {t.price} руб./{t.unit} <a class="delete" href="/delete_tariff?id={t.id}&token={token}">[Удалить]</a></li>'
    tariffs_html += "</ul>"
    meetings_html = "<ul>"
    for m in meetings:
        meetings_html += f'<li><strong>{m.date.strftime("%d.%m.%Y %H:%M")}</strong>: {m.description or ""}<br>'
        if m.agenda_items:
            meetings_html += "<em>Повестка:</em><ul>"
            for item in m.agenda_items:
                meetings_html += f"<li>{item.get('text')}"
                if item.get('files'):
                    meetings_html += "<br><small>Файлы: " + ", ".join([f'<a href="{url}">ссылка</a>' for url in item['files']]) + "</small>"
                meetings_html += "</li>"
            meetings_html += "</ul>"
        meetings_html += f' <a class="delete" href="/delete_meeting?id={m.id}&token={token}">[Удалить]</a></li>'
    meetings_html += "</ul>"
    docs_html = "<ul>"
    for d in docs:
        docs_html += f'<li>{d.title} ({d.doc_type}): <a href="{d.file_url}">Скачать</a> <a class="delete" href="/delete_document?id={d.id}&token={token}">[Удалить]</a></li>'
    docs_html += "</ul>"
    works_html = "<ul>"
    for w in works:
        period = f', период: {w.start_date.strftime("%d.%m.%Y")} - {w.end_date.strftime("%d.%m.%Y")}' if w.start_date and w.end_date else ''
        works_html += f'<li>{w.task_name}: бюджет {w.planned_budget} руб., статус {w.status}, выполнено {w.completion_percent}%{period} <a class="delete" href="/delete_workplan?id={w.id}&token={token}">[Удалить]</a></li>'
    works_html += "</ul>"
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Админка ДТСН Пенсионер</title>
        <meta charset="utf-8">
        <style>
            body {{ font-family: Arial; margin: 20px; background: #f0f2f5; }}
            h1, h2 {{ color: #1a5f7a; }}
            .section {{ background: white; padding: 15px; margin-bottom: 20px; border-radius: 8px; }}
            ul {{ list-style: none; padding: 0; }}
            li {{ padding: 8px; border-bottom: 1px solid #ddd; }}
            form {{ margin-top: 15px; }}
            input, select, textarea {{ margin: 5px; padding: 8px; }}
            button {{ background: #1a5f7a; color: white; border: none; padding: 8px 15px; cursor: pointer; }}
            .delete {{ color: red; margin-left: 10px; text-decoration: none; }}
            .appeal {{ margin-bottom: 20px; padding: 10px; background: #f9f9f9; border-left: 3px solid #1a5f7a; }}
            .agenda-item {{ background: #eef; padding: 10px; margin: 10px 0; }}
        </style>
    </head>
    <body>
        <h1>📋 Панель управления ДТСН "Пенсионер"</h1>
        <div class="section">
            <h2>✉️ Обращения (ожидают ответа)</h2>
            {appeals_html}
        </div>
        <div class="section">
            <h2>📊 Тарифы</h2>
            {tariffs_html}
            <form method="post" action="/add_tariff">
                <input type="hidden" name="token" value="{token}">
                <input type="text" name="service_name" placeholder="Название" required>
                <input type="number" step="0.01" name="price" placeholder="Цена" required>
                <input type="text" name="unit" placeholder="Ед. изм" required>
                <button type="submit">➕ Добавить</button>
            </form>
        </div>
        <div class="section">
            <h2>🗓 Собрания</h2>
            {meetings_html}
            <form method="post" action="/add_meeting">
                <input type="hidden" name="token" value="{token}">
                <input type="datetime-local" name="date" required>
                <input type="text" name="description" placeholder="Описание собрания" style="width: 100%;"><br>
                <div id="agenda-container"></div>
                <button type="button" onclick="addAgendaItem()">➕ Добавить вопрос повестки</button><br>
                <button type="submit">Сохранить собрание</button>
            </form>
        </div>
        <div class="section">
            <h2>📄 Документы</h2>
            {docs_html}
            <form method="post" action="/add_document">
                <input type="hidden" name="token" value="{token}">
                <select name="doc_type">
                    <option value="protocol">Протокол</option>
                    <option value="resolution">Решение</option>
                    <option value="budget">Смета</option>
                </select>
                <input type="text" name="title" placeholder="Название" required>
                <input type="url" name="file_url" placeholder="Ссылка на файл" required>
                <button type="submit">➕ Добавить</button>
            </form>
        </div>
        <div class="section">
            <h2>📝 План работ</h2>
            {works_html}
            <form method="post" action="/add_workplan">
                <input type="hidden" name="token" value="{token}">
                <input type="text" name="task_name" placeholder="Название задачи" required><br>
                <input type="number" step="0.01" name="planned_budget" placeholder="Бюджет" required><br>
                <select name="status">
                    <option value="planned">Запланировано</option>
                    <option value="in_progress">В работе</option>
                    <option value="completed">Завершено</option>
                </select><br>
                <input type="number" name="completion_percent" placeholder="Процент выполнения" value="0"><br>
                <label>Дата начала: <input type="date" name="start_date"></label><br>
                <label>Дата окончания: <input type="date" name="end_date"></label><br>
                <button type="submit">➕ Добавить задачу</button>
            </form>
        </div>
        <div class="section">
            <h2>🏆 Рейтинг удовлетворённости</h2>
            <form method="get" action="/rating">
                <input type="hidden" name="token" value="{token}">
                <label>Период:</label>
                <input type="date" name="date_from" value="{datetime.now().replace(day=1).strftime('%Y-%m-%d')}">
                <input type="date" name="date_to" value="{datetime.now().strftime('%Y-%m-%d')}">
                <button type="submit">Показать рейтинг</button>
            </form>
        </div>
        <p><a href="/?token={token}">🔄 Обновить страницу</a></p>
        <script>
            let agendaIndex = 0;
            function addAgendaItem() {{
                const container = document.getElementById('agenda-container');
                const div = document.createElement('div');
                div.className = 'agenda-item';
                div.innerHTML = `
                    <input type="text" name="agenda_text_${{agendaIndex}}" placeholder="Текст вопроса" style="width: 80%;">
                    <input type="text" name="agenda_files_${{agendaIndex}}" placeholder="Ссылки на файлы (через запятую)" style="width: 80%; margin-top: 5px;">
                    <button type="button" onclick="this.parentElement.remove()">Удалить</button>
                `;
                container.appendChild(div);
                agendaIndex++;
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

# ---------- CRUD обработчики (без изменений) ----------
@app.post("/answer_appeal")
async def answer_appeal(appeal_id: int = Form(...), response_text: str = Form(...), token: str = Form(...), db: Session = Depends(get_db)):
    if not verify_token(token):
        return HTMLResponse("403", status_code=403)
    appeal = db.query(Appeal).filter(Appeal.id == appeal_id).first()
    if not appeal:
        return HTMLResponse("Обращение не найдено", status_code=404)
    appeal.board_response = response_text
    appeal.status = "answered"
    appeal.answered_at = datetime.now()
    appeal.reminder_sent = False
    db.commit()
    asyncio.create_task(send_to_user(
        int(appeal.chat_id),
        f"📩 **Ответ правления на обращение #{appeal.id}**\n\n{response_text}\n\nОцените ответ цифрой от 1 до 5."
    ))
    return RedirectResponse(f"/?token={token}", status_code=303)

@app.post("/add_tariff")
async def add_tariff(service_name: str = Form(...), price: float = Form(...), unit: str = Form(...), token: str = Form(...), db: Session = Depends(get_db)):
    if not verify_token(token): return HTMLResponse("403", status_code=403)
    db.add(Tariff(service_name=service_name, price=price, unit=unit))
    db.commit()
    return RedirectResponse(f"/?token={token}", status_code=303)

@app.get("/delete_tariff")
async def delete_tariff(id: int, token: str, db: Session = Depends(get_db)):
    if not verify_token(token): return HTMLResponse("403", status_code=403)
    t = db.query(Tariff).filter(Tariff.id == id).first()
    if t: db.delete(t); db.commit()
    return RedirectResponse(f"/?token={token}", status_code=303)

@app.post("/add_meeting")
async def add_meeting(request: Request, token: str = Form(...), date: str = Form(...), description: str = Form(...), db: Session = Depends(get_db)):
    if not verify_token(token): return HTMLResponse("403", status_code=403)
    form = await request.form()
    agenda_items = []
    i = 0
    while True:
        text_key = f"agenda_text_{i}"
        files_key = f"agenda_files_{i}"
        if text_key not in form:
            break
        text_val = form[text_key]
        files_str = form.get(files_key, "")
        files = [f.strip() for f in files_str.split(",") if f.strip()]
        if text_val:
            agenda_items.append({"text": text_val, "files": files})
        i += 1
    new_meeting = Meeting(date=datetime.fromisoformat(date), description=description, agenda_items=agenda_items)
    db.add(new_meeting)
    db.commit()
    return RedirectResponse(f"/?token={token}", status_code=303)

@app.get("/delete_meeting")
async def delete_meeting(id: int, token: str, db: Session = Depends(get_db)):
    if not verify_token(token): return HTMLResponse("403", status_code=403)
    m = db.query(Meeting).filter(Meeting.id == id).first()
    if m: db.delete(m); db.commit()
    return RedirectResponse(f"/?token={token}", status_code=303)

@app.post("/add_document")
async def add_document(doc_type: str = Form(...), title: str = Form(...), file_url: str = Form(...), token: str = Form(...), db: Session = Depends(get_db)):
    if not verify_token(token): return HTMLResponse("403", status_code=403)
    db.add(Document(doc_type=doc_type, title=title, file_url=file_url))
    db.commit()
    return RedirectResponse(f"/?token={token}", status_code=303)

@app.get("/delete_document")
async def delete_document(id: int, token: str, db: Session = Depends(get_db)):
    if not verify_token(token): return HTMLResponse("403", status_code=403)
    d = db.query(Document).filter(Document.id == id).first()
    if d: db.delete(d); db.commit()
    return RedirectResponse(f"/?token={token}", status_code=303)

@app.post("/add_workplan")
async def add_workplan(
    token: str = Form(...),
    task_name: str = Form(...),
    planned_budget: float = Form(...),
    status: str = Form(...),
    completion_percent: int = Form(...),
    start_date: str = Form(None),
    end_date: str = Form(None),
    db: Session = Depends(get_db)
):
    if not verify_token(token): return HTMLResponse("403", status_code=403)
    start = datetime.fromisoformat(start_date) if start_date else None
    end = datetime.fromisoformat(end_date) if end_date else None
    new_task = WorkPlan(
        task_name=task_name,
        planned_budget=planned_budget,
        status=status,
        completion_percent=completion_percent,
        start_date=start,
        end_date=end
    )
    db.add(new_task)
    db.commit()
    return RedirectResponse(f"/?token={token}", status_code=303)

@app.get("/delete_workplan")
async def delete_workplan(id: int, token: str, db: Session = Depends(get_db)):
    if not verify_token(token): return HTMLResponse("403", status_code=403)
    wp = db.query(WorkPlan).filter(WorkPlan.id == id).first()
    if wp: db.delete(wp); db.commit()
    return RedirectResponse(f"/?token={token}", status_code=303)

@app.get("/rating")
async def show_rating(request: Request, token: str, date_from: str, date_to: str, db: Session = Depends(get_db)):
    if not verify_token(token):
        return HTMLResponse("403", status_code=403)
    from_date = datetime.fromisoformat(date_from)
    to_date = datetime.fromisoformat(date_to)
    appeals = db.query(Appeal).filter(
        Appeal.answered_at >= from_date,
        Appeal.answered_at <= to_date,
        Appeal.satisfaction_score.isnot(None)
    ).all()
    if appeals:
        avg = sum(a.satisfaction_score for a in appeals) / len(appeals)
        stars = "⭐" * int(avg) + ("✨" if avg - int(avg) >= 0.5 else "")
        rating_text = f"<h3>Рейтинг за период {from_date.strftime('%d.%m.%Y')} - {to_date.strftime('%d.%m.%Y')}: {avg:.1f}/5</h3><p>{stars or '☆☆☆☆☆'}</p>"
    else:
        rating_text = "<p>Нет оценок за выбранный период.</p>"
    return HTMLResponse(content=f"""
    <html><body>
        <h2>Рейтинг правления</h2>
        {rating_text}
        <a href="/?token={token}">← Назад</a>
    </body></html>
    """)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
