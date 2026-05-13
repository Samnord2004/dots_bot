import asyncio
from yadisk_async import YaDisk
from config import config

disk = YaDisk(token=config.YANDEX_DISK_TOKEN)

async def get_file_link(path: str) -> str:
    """Получить публичную ссылку на файл по полному пути на диске"""
    try:
        # Проверяем, существует ли файл
        info = await disk.get_meta(path)
        if info.type == 'file':
            # Получаем прямую ссылку (работает, если файл доступен)
            # Внимание: требует, чтобы файл был опубликован, или используйте /resources/download
            link = await disk.get_download_link(path)
            return link
        else:
            return None
    except Exception as e:
        print(f"Ошибка доступа к Яндекс.Диску: {e}")
        return None

async def upload_file(local_path: str, remote_path: str) -> bool:
    """Загружает файл на диск (не требуется для текущей версии, но можно добавить)"""
    try:
        with open(local_path, 'rb') as f:
            await disk.upload(f, remote_path)
        return True
    except Exception as e:
        print(f"Ошибка загрузки: {e}")
        return False
