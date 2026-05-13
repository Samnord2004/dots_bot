from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from database import get_db
from models import Tariff, Meeting, Document, WorkPlan, Appeal, AppealFile
from config import config
from datetime import datetime
import asyncio
import httpx
import os

app = FastAPI()

BASE_API_URL = "https://platform-api.max.ru"
HEADERS = {"Authorization": config.MAX_BOT_TOKEN, "Content-Type": "application/json"}

# Раздача статических файлов (фото из обращений)
if os.path.exists("uploads"):
    app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

def verify_token(token: str):
    return token == config.ADMIN_SECRET_KEY

async def send_to_user(user_id: int, text: str):
    payload = {"text": text, "format": "markdown"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(f"{BASE_API_URL}/messages?user_id={user_id}", json=payload, headers=HEADERS)
            if resp.status_code != 200:
                print(f"Send error {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"Send exception: {e}")

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, token: str, db: Session = Depends(get_db)):
    if not verify_token(token):
        return HTMLResponse("<h1>403 Forbidden</h1><p>Неверный токен.</p>", status_code=403)
    
    tariffs = db.query(Tariff).all()
    meetings = db.query(Meeting).order_by(Meeting.date.desc()).all()
    docs = db.query(Document).all()
    works = db.query(WorkPlan).all()
    waiting_appeals = db.query(Appeal).filter(Appeal.status == "waiting").order_by(Appeal.created_at.desc()).all()
    appeals_with_files = {}
    for appeal in waiting_appeals:
        files = db.query(AppealFile).filter(AppealFile.appeal_id == appeal.id).all()
        appeals_with_files[appeal.id] = files
    
    # Формируем HTML для списка собраний
    meetings_html = ""
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
    
    # Формируем HTML для списка задач
    works_html = ""
    for w in works:
        period = f', период: {w.start_date.strftime("%d.%m.%Y")} - {w.end_date.strftime("%d.%m.%Y")}' if w.start_date and w.end_date else ''
        works_html += f'<li>{w.task_name}: бюджет {w.planned_budget} руб., статус {w.status}, выполнено {w.completion_percent}%{period} <a class="delete" href="/delete_workplan?id={w.id}&token={token}">[Удалить]</a></li>'
    
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
            img {{ max-width: 200px; max-height: 200px; margin: 5px; }}
        </style>
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
    </head>
    <body>
        <h1>📋 Панель управления ДТСН "Пенсионер"</h1>
        
        <div class="section">
            <h2>✉️ Обращения (ожидают ответа)</h2>
            {''.join(f'''
            <div class="appeal">
                <div><strong>#{a.id}</strong> от {a.created_at.strftime('%d.%m.%Y %H:%M')}</div>
                <div><strong>ФИО:</strong> {a.full_name}</div>
                <div><strong>Участок:</strong> {a.plot_number}</div>
                <div><strong>Вопрос:</strong> {a.question}</div>
                {''.join(f'<div><strong>Файлы:</strong> <a href="/uploads/{os.path.basename(f.file_path)}">{os.path.basename(f.file_path)}</a></div>' for f in appeals_with_files.get(a.id, []))}
                <form method="post" action="/answer_appeal">
                    <input type="hidden" name="token" value="{token}">
                    <input type="hidden" name="appeal_id" value="{a.id}">
                    <textarea name="response_text" rows="3" placeholder="Введите ответ..."></textarea><br>
                    <button type="submit">📤 Отправить ответ</button>
                </form>
            </div>
            ''' for a in waiting_appeals) or '<p>Нет обращений</p>'}
        </div>
        
        <div class="section">
            <h2>📊 Тарифы</h2>
            <ul>
                {''.join(f'<li>{t.service_name}: {t.price} руб./{t.unit} <a class="delete" href="/delete_tariff?id={t.id}&token={token}">[Удалить]</a></li>' for t in tariffs) or '<li>Нет тарифов</li>'}
            </ul>
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
            <ul>
                {meetings_html or '<li>Нет собраний</li>'}
            </ul>
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
            <ul>
                {''.join(f'<li>{d.title} ({d.doc_type}): <a href="{d.file_url}">Скачать</a> <a class="delete" href="/delete_document?id={d.id}&token={token}">[Удалить]</a></li>' for d in docs) or '<li>Нет документов</li>'}
            </ul>
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
            <ul>
                {works_html or '<li>Нет задач</li>'}
            </ul>
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
            <h2>🏆 Рейтинг удовлетворённости работой правления</h2>
            <form method="get" action="/rating">
                <input type="hidden" name="token" value="{token}">
                <label>Период:</label>
                <input type="date" name="date_from" value="{datetime.now().replace(day=1).strftime('%Y-%m-%d')}">
                <input type="date" name="date_to" value="{datetime.now().strftime('%Y-%m-%d')}">
                <button type="submit">Показать рейтинг</button>
            </form>
        </div>
        
        <p><a href="/?token={token}">🔄 Обновить страницу</a></p>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

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
        f"📩 **Ответ правления на обращение #{appeal.id}**\n\n{response_text}\n\nПожалуйста, оцените качество ответа по шкале от 1 до 5 (отправьте просто цифру)."
    ))
    return RedirectResponse(f"/?token={token}", status_code=303)

@app.post("/add_meeting")
async def add_meeting(request: Request, token: str = Form(...), date: str = Form(...), description: str = Form(...), db: Session = Depends(get_db)):
    if not verify_token(token):
        return HTMLResponse("403", status_code=403)
    form = await request.form()
    agenda_items = []
    i = 0
    while True:
        text_key = f"agenda_text_{i}"
        files_key = f"agenda_files_{i}"
        if text_key not in form:
            break
        text = form[text_key]
        files_str = form.get(files_key, "")
        files = [f.strip() for f in files_str.split(",") if f.strip()]
        if text:
            agenda_items.append({"text": text, "files": files})
        i += 1
    new_meeting = Meeting(
        date=datetime.fromisoformat(date),
        description=description,
        agenda_items=agenda_items
    )
    db.add(new_meeting)
    db.commit()
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
    if not verify_token(token):
        return HTMLResponse("403", status_code=403)
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

# --- CRUD для тарифов ---
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

@app.get("/delete_workplan")
async def delete_workplan(id: int, token: str, db: Session = Depends(get_db)):
    if not verify_token(token): return HTMLResponse("403", status_code=403)
    wp = db.query(WorkPlan).filter(WorkPlan.id == id).first()
    if wp: db.delete(wp); db.commit()
    return RedirectResponse(f"/?token={token}", status_code=303)
