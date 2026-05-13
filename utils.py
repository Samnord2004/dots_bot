import httpx
import logging
from config import config

logger = logging.getLogger(__name__)
BASE_API_URL = "https://platform-api.max.ru"
HEADERS = {"Authorization": config.MAX_BOT_TOKEN, "Content-Type": "application/json"}

async def send_to_user(user_id: int, text: str, reply_markup: dict = None):
    """Отправка личного сообщения пользователю."""
    payload = {"text": text, "format": "markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(f"{BASE_API_URL}/messages?user_id={user_id}", json=payload, headers=HEADERS)
            if resp.status_code != 200:
                logger.error(f"Send to user {user_id} error: {resp.status_code} - {resp.text}")
            return resp.status_code == 200
        except Exception as e:
            logger.exception(f"Send to user exception: {e}")
            return False

async def send_to_chat(chat_id: int, text: str):
    """Отправка в групповой чат: пробует query-параметр, затем тело запроса."""
    payload = {"text": text, "format": "markdown"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Способ 1: chat_id как query-параметр
        try:
            resp = await client.post(f"{BASE_API_URL}/messages?chat_id={chat_id}", json=payload, headers=HEADERS)
            if resp.status_code == 200:
                return True
            logger.warning(f"Send to chat via query failed: {resp.status_code} - {resp.text}")
        except Exception as e:
            logger.warning(f"Query method exception: {e}")
        # Способ 2: chat_id в теле запроса
        try:
            payload_body = {"chat_id": chat_id, "text": text, "format": "markdown"}
            resp2 = await client.post(f"{BASE_API_URL}/messages", json=payload_body, headers=HEADERS)
            if resp2.status_code == 200:
                return True
            logger.error(f"Send to chat via body failed: {resp2.status_code} - {resp2.text}")
        except Exception as e:
            logger.exception(f"Body method exception: {e}")
        return False

async def send_to_admin(text: str):
    """Отправка сообщения председателю в личку (запасной канал)."""
    admin_id = getattr(config, 'ADMIN_USER_ID', None)
    if admin_id:
        return await send_to_user(int(admin_id), text)
    else:
        logger.warning("ADMIN_USER_ID не задан в .env")
        return False
