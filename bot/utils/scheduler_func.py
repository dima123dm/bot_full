import os
import json
import asyncio

import aiofiles
from aiogram.types import Message, FSInputFile
from aiohttp import ClientSession
from sqlalchemy.ext.asyncio import AsyncSession

from api import KworkAPI
from bot.handlers import localization as loc
from bot.handlers import keyboards as kb
from db.models import User
from cryptographer import decrypt
            
            
async def projects_tracking(user: User, message: Message, db_session: AsyncSession) -> None:
    """
    Tracks new projects for the user on Kwork and sends information about them to the chat.

    Args:
        user (User): The user object for whom the project tracking is performed.
        message (Message): The message object used to send information to the user.
        db_session (AsyncSession): The asynchronous session for database operations.

    Returns:
        None
    """
    async with ClientSession() as session:
        kwork = KworkAPI(session)
        kwork.headers["Cookie"] = decrypt(user.kwork_session.cookie)
        success, projects = await kwork.get_projects()
        
        if not success:
            return
        
        projects_ids = []
        
        for project in projects:
            attachment = False
            projects_ids.append(project.get("id"))
            
            if project.get("id") not in json.loads(user.kwork_session.last_projects):
                for file in project.get("files"):
                    attempts = 0
                    max_attempts = 3
                    success_send = False
                    
                    while attempts < max_attempts:
                        try:
                            # Скачивание файла
                            content = await kwork.get_file_content(url=file["url"])
                            filepath = f"temp/{file['fname']}"
                            
                            async with aiofiles.open(filepath, "wb") as f:
                                await f.write(content)
                            
                            # Отправка документа
                            await message.answer_document(
                                document=FSInputFile(filepath),
                                caption=loc.remove_emojis(project['name'])
                            )
                            
                            os.remove(filepath)
                            attachment = True
                            success_send = True
                            break  # Успех, выходим из цикла
                        
                        except Exception as e:
                            attempts += 1
                            print(f"Попытка {attempts}/{max_attempts} для файла {file['fname']} провалилась: {e}")
                            if os.path.exists(filepath):
                                os.remove(filepath)  # Очистка, если файл частично записан
                            
                            if attempts < max_attempts:
                                await asyncio.sleep(2)  # Пауза 2 секунды перед следующей попыткой
                            else:
                                # Финальная ошибка после всех попыток
                                await message.answer(
                                    text=f"Не удалось отправить вложение '{file['fname']}' после {max_attempts} попыток. Ошибка: {str(e)}"
                                )
                    
                    if not success_send:
                        # Если не удалось отправить, можно продолжить без attachment или пропустить
                
                await message.answer(
                    text=loc.project_info(project, attachment), 
                    reply_markup=kb.project_keyboard(project_id=project["id"]), 
                    disable_web_page_preview=True
                )
                
        user.kwork_session.last_projects = json.dumps(projects_ids)
        await db_session.commit()