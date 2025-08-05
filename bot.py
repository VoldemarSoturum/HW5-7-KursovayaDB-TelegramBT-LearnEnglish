import os
import asyncio
import logging
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from database import db
from translator import translate_word, is_in_dictionary
from learning_test import start_learning_test, handle_test_response, end_test_and_show_results
from datetime import datetime
from typing import Union

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация бота
bot_token = os.getenv("BOT_TOKEN")
if not bot_token:
    raise ValueError("Не задан токен бота в переменных окружения")
bot = Bot(token=bot_token)
dp = Dispatcher()

# Состояния бота
class Form(StatesGroup):
    ADDING_WORD = State()
    REMOVING_WORD = State()
    TRANSLATING_WORD = State()
    ADDING_TRANSLATION = State()
    TEST = State()

async def create_words_keyboard(user_id: int, page: int = 0) -> InlineKeyboardMarkup:
    """Создает клавиатуру со словами пользователя"""
    try:
        default_words = await db.get_default_words()
        user_words = await db.get_user_words(user_id)
        all_words = default_words + user_words

        if not all_words:
            return InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить слово", callback_data="add_word")]
            ])

        words_per_page = 6
        start_idx = page * words_per_page
        end_idx = start_idx + words_per_page
        page_words = all_words[start_idx:end_idx]

        buttons = []
        for i in range(0, len(page_words), 2):
            row = []
            if i < len(page_words):
                en_word = page_words[i][0]
                row.append(InlineKeyboardButton(text=en_word, callback_data=f"word_{en_word}"))
            if i + 1 < len(page_words):
                en_word = page_words[i + 1][0]
                row.append(InlineKeyboardButton(text=en_word, callback_data=f"word_{en_word}"))
            buttons.append(row)

        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"page_{page - 1}"))
        if end_idx < len(all_words):
            nav_buttons.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"page_{page + 1}"))
        if nav_buttons:
            buttons.append(nav_buttons)

        buttons.append([
            InlineKeyboardButton(text="➕ Добавить слово", callback_data="add_word"),
            InlineKeyboardButton(text="🔙 Удалить слово", callback_data="remove_word"),
            InlineKeyboardButton(text="📘 Перевести слово", callback_data="translate"),
            InlineKeyboardButton(text="🧠 Тест", callback_data="start_test")
        ])

        return InlineKeyboardMarkup(inline_keyboard=buttons)
    except Exception as e:
        logger.error(f"Error creating keyboard: {e}")
        return InlineKeyboardMarkup(inline_keyboard=[])

@dp.message(Command("start"))
async def start_command(message: types.Message, state: FSMContext):
    """Обработчик команды /start"""
    try:
        user = message.from_user
        await db.add_user(
            user.id,
            user.username or "",
            user.first_name or "",
            user.last_name or ""
        )

        welcome_message = (
            f"Привет, {user.first_name} 👋\n"
            "Ты можешь изучать английские слова, добавлять свои и удалять ненужные.\n"
            "Также теперь доступен быстрый перевод слов!\n\n"
            "Выбирай действие ниже:"
        )
        keyboard = await create_words_keyboard(user.id)
        await message.answer(welcome_message, reply_markup=keyboard)
        await state.clear()
    except Exception as e:
        logger.error(f"Start command error: {e}")
        await message.answer("Произошла ошибка. Пожалуйста, попробуйте позже.")

@dp.callback_query(F.data.startswith("word_"))
async def show_word_translation(callback: types.CallbackQuery):
    """Показывает перевод выбранного слова"""
    try:
        word = callback.data.split("_", 1)[1]
        translation = await db.get_word_translation(word) or await db.get_word_translation(word, 'user')
        if translation:
            await callback.message.edit_text(
                f"<b>{word}</b> - <i>{translation}</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="cancel")]
                ])
            )
        else:
            await callback.message.edit_text("Перевод не найден.")
    except Exception as e:
        logger.error(f"Show word translation error: {e}")
        await callback.answer("Произошла ошибка при получении перевода.")

@dp.callback_query(F.data.startswith("page_"))
async def change_page(callback: types.CallbackQuery):
    """Обработчик переключения страниц"""
    try:
        user_id = callback.from_user.id
        page = int(callback.data.split("_", 1)[1])
        keyboard = await create_words_keyboard(user_id, page)
        await callback.message.edit_text("Выберите слово:", reply_markup=keyboard)
    except (ValueError, IndexError) as e:
        logger.error(f"Invalid page number: {e}")
        await callback.answer("Неверный номер страницы")
    except Exception as e:
        logger.error(f"Page change error: {e}")
        await callback.answer("Произошла ошибка при смене страницы.")
    finally:
        await callback.answer()

@dp.callback_query(F.data == "add_word")
async def start_add_word(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс добавления слова"""
    try:
        await callback.message.answer(
            "Введите слово и перевод через тире. Пример: apple - яблоко",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⬅ Назад")]],
                resize_keyboard=True
            )
        )
        await state.set_state(Form.ADDING_WORD)
    except Exception as e:
        logger.error(f"Add word start error: {e}")
        await callback.message.answer("Произошла ошибка.")
    finally:
        await callback.answer()

@dp.message(Form.ADDING_WORD)
async def process_add_word(message: types.Message, state: FSMContext):
    """Обрабатывает добавление нового слова"""
    try:
        user_id = message.from_user.id
        text = message.text.strip()
        
        if text == "⬅ Назад":
            keyboard = await create_words_keyboard(user_id)
            await message.answer("Отмена.", reply_markup=ReplyKeyboardRemove())
            await message.answer("Выберите действие:", reply_markup=keyboard)
            await state.clear()
            return

        if '-' not in text:
            await message.answer("Формат: слово - перевод", reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⬅ Назад")]], 
                resize_keyboard=True))
            return

        parts = text.split('-', 1)
        if len(parts) < 2:
            await message.answer("Формат: слово - перевод")
            return
            
        en_word, ru_translation = [t.strip() for t in parts]
        
        if await db.add_user_word(user_id, en_word, ru_translation):
            keyboard = await create_words_keyboard(user_id)
            await message.answer(f"✅ Слово добавлено: {en_word} - {ru_translation}", reply_markup=ReplyKeyboardRemove())
            await message.answer("Выберите действие:", reply_markup=keyboard)
        else:
            keyboard = await create_words_keyboard(user_id)
            await message.answer(f"Слово '{en_word}' уже есть.", reply_markup=ReplyKeyboardRemove())
            await message.answer("Выберите действие:", reply_markup=keyboard)

        await state.clear()
    except Exception as e:
        logger.error(f"Add word error: {e}")
        await message.answer("Произошла ошибка при добавлении слова.")

@dp.callback_query(F.data == "remove_word")
async def start_remove_word(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс удаления слова"""
    try:
        user_id = callback.from_user.id
        user_words = await db.get_user_words(user_id)
        
        if not user_words:
            await callback.message.edit_text("У вас нет добавленных слов.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="cancel")]
            ]))
            return

        buttons = [[InlineKeyboardButton(text=f"{en} - {ru}", callback_data=f"remove_{en}")] for en, ru in user_words]
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="cancel")])
        await callback.message.edit_text("Выберите слово для удаления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        await state.set_state(Form.REMOVING_WORD)
    except Exception as e:
        logger.error(f"Remove word start error: {e}")
        await callback.message.answer("Произошла ошибка.")
    finally:
        await callback.answer()

@dp.callback_query(F.data.startswith("remove_"), Form.REMOVING_WORD)
async def remove_word(callback: types.CallbackQuery, state: FSMContext):
    """Удаляет выбранное слово"""
    try:
        user_id = callback.from_user.id
        word = callback.data.split("_", 1)[1]
        await db.remove_user_word(user_id, word)
        logger.info(f"User {user_id} удалил слово: {word}")
        keyboard = await create_words_keyboard(user_id)
        await callback.message.edit_text(f"Слово '{word}' удалено.", reply_markup=keyboard)
        await state.clear()
    except Exception as e:
        logger.error(f"Remove word error: {e}")
        await callback.message.answer("Произошла ошибка при удалении слова.")
    finally:
        await callback.answer()

@dp.callback_query(F.data == "translate")
async def start_translate(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс перевода слова"""
    try:
        await callback.message.answer("Введите слово для перевода:")
        await state.set_state(Form.TRANSLATING_WORD)
    except Exception as e:
        logger.error(f"Translate start error: {e}")
        await callback.message.answer("Произошла ошибка.")
    finally:
        await callback.answer()

@dp.message(Form.TRANSLATING_WORD)
async def translate_input(message: types.Message, state: FSMContext):
    """Обрабатывает ввод слова для перевода"""
    try:
        user_id = message.from_user.id
        word = message.text.strip()
        result = translate_word(word)
        
        if result:
            keyboard = await create_words_keyboard(user_id)
            await message.answer(f"📘 <b>{word}</b> — <i>{result}</i>", parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove())
            await message.answer("Выберите действие:", reply_markup=keyboard)
            await state.clear()
        else:
            if is_in_dictionary(word):
                keyboard = await create_words_keyboard(user_id)
                await message.answer("Перевод есть в XML-словаре, но он не распознан. Уточните.", reply_markup=ReplyKeyboardRemove())
                await message.answer("Выберите действие:", reply_markup=keyboard)
                await state.clear()
                return
            
            await state.update_data(word_to_add=word)
            await message.answer(
                "Перевод не найден. Хотите добавить перевод вручную?\nВведите в формате: слово - перевод",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text="⬅ Назад")]], 
                    resize_keyboard=True
                )
            )
            await state.set_state(Form.ADDING_TRANSLATION)
    except Exception as e:
        logger.error(f"Translate error: {e}")
        await message.answer("Произошла ошибка при переводе слова.")

@dp.message(Form.ADDING_TRANSLATION)
async def add_translation_handler(message: types.Message, state: FSMContext):
    """Обрабатывает добавление перевода вручную"""
    try:
        user_id = message.from_user.id
        text = message.text.strip()
        
        if text == "⬅ Назад":
            keyboard = await create_words_keyboard(user_id)
            await message.answer("Отмена.", reply_markup=ReplyKeyboardRemove())
            await message.answer("Выберите действие:", reply_markup=keyboard)
            await state.clear()
            return

        if '-' not in text:
            await message.answer("Формат: слово - перевод", reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⬅ Назад")]], 
                resize_keyboard=True))
            return

        parts = text.split('-', 1)
        if len(parts) < 2:
            await message.answer("Формат: слово - перевод")
            return
            
        en_word, ru_translation = [t.strip() for t in parts]
        
        if await db.add_user_word(user_id, en_word, ru_translation):
            keyboard = await create_words_keyboard(user_id)
            await message.answer(f"✅ Перевод добавлен: {en_word} - {ru_translation}", reply_markup=ReplyKeyboardRemove())
            await message.answer("Выберите действие:", reply_markup=keyboard)
        else:
            keyboard = await create_words_keyboard(user_id)
            await message.answer(f"Слово '{en_word}' уже есть.", reply_markup=ReplyKeyboardRemove())
            await message.answer("Выберите действие:", reply_markup=keyboard)

        await state.clear()
    except Exception as e:
        logger.error(f"Add translation error: {e}")
        await message.answer("Произошла ошибка при добавлении перевода.")

@dp.callback_query(F.data == "start_test")
async def start_test_handler(callback: types.CallbackQuery, state: FSMContext):
    """Начинает тест на знание слов"""
    try:
        user_id = callback.from_user.id
        question, correct_answer, keyboard = await start_learning_test(user_id, state)
        
        if question is None:
            await callback.answer("Нет доступных слов для теста.", show_alert=True)
            return
            
        await callback.message.edit_text(
            f"🧠 Тест:\n{question}\nВведите пропущенное слово:",
            reply_markup=keyboard
        )
        await state.set_state(Form.TEST)
    except Exception as e:
        logger.error(f"Start test error: {e}")
        await callback.message.answer("Произошла ошибка при запуске теста.")
    finally:
        await callback.answer()

@dp.message(Form.TEST)
async def test_answer_handler(message: types.Message, state: FSMContext):
    """Обрабатывает ответ пользователя в тесте"""
    try:
        data = await state.get_data()
        if not data.get('test_in_progress'):
            await message.answer("Тест не начат или уже завершен")
            return

        user_response = message.text.strip()
        is_correct, feedback, keyboard = await handle_test_response(
            message.from_user.id, user_response, 
            data['test_correct_answer'], state
        )
        await message.answer(feedback, reply_markup=keyboard)

        # Получаем следующий вопрос
        question, correct_answer, keyboard = await start_learning_test(message.from_user.id, state)
        
        if question is None:
            # Тест завершен
            results, keyboard = await end_test_and_show_results(message.from_user.id, state)
            await message.answer(results, parse_mode="HTML")
            menu_keyboard = await create_words_keyboard(message.from_user.id)
            await message.answer("Главное меню:", reply_markup=menu_keyboard)
            await state.clear()
            return

        await state.update_data({"test_correct_answer": correct_answer})
        await message.answer(
            f"🧠 Следующий вопрос:\n{question}\nВведите пропущенное слово:",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Test answer error: {e}")
        await message.answer("Произошла ошибка при обработке ответа")
        await state.clear()

@dp.callback_query(F.data == "end_test")
async def end_test_handler(callback: types.CallbackQuery, state: FSMContext):
    """Завершает тест и показывает результаты"""
    try:
        results, keyboard = await end_test_and_show_results(callback.from_user.id, state)
        await callback.message.answer(results, parse_mode="HTML")
        menu_keyboard = await create_words_keyboard(callback.from_user.id)
        await callback.message.answer("Главное меню:", reply_markup=menu_keyboard)
        await state.clear()
    except Exception as e:
        logger.error(f"Error ending test: {e}")
        await callback.message.answer("Произошла ошибка при завершении теста")
    finally:
        await callback.answer()

@dp.callback_query(F.data == "cancel")
async def cancel_handler(callback: types.CallbackQuery, state: FSMContext):
    """Обрабатывает отмену действий"""
    try:
        data = await state.get_data()
        user_id = callback.from_user.id
        
        if data.get('test_in_progress'):
            test_id = data.get('test_id')
            questions_answered = data.get('questions_answered', 0)
            
            if test_id and questions_answered > 0:
                await db.update_test_session(
                    test_id,
                    questions_answered,
                    data.get('correct_answers', 0),
                    data.get('incorrect_answers', 0)
                )
                message = "Тест отменен. Прогресс сохранен."
            else:
                message = "Тест отменен."
            
            await callback.message.answer(message)
        
        keyboard = await create_words_keyboard(user_id)
        await callback.message.edit_text("Главное меню:", reply_markup=keyboard)
        await state.clear()
        
    except Exception as e:
        logger.error(f"Cancel error: {e}")
        await callback.message.answer("Произошла ошибка при отмене.")
    finally:
        await callback.answer()

@dp.message(Command("history"))
async def show_history_command(message: types.Message):
    """Показывает историю тестов"""
    await show_test_history(message)

@dp.callback_query(F.data == "test_history")
async def show_test_history_handler(callback: types.CallbackQuery):
    """Обработчик кнопки истории тестов"""
    await show_test_history(callback)

async def show_test_history(update: Union[types.Message, types.CallbackQuery]):
    """Общая функция для отображения истории тестов"""
    try:
        user_id = update.from_user.id
        tests = await db.get_user_test_history(user_id)
        
        if not tests:
            text = "📭 У вас пока нет истории тестов."
            if isinstance(update, types.CallbackQuery):
                await update.message.answer(text)
            else:
                await update.answer(text)
            return
            
        history_message = "📋 Ваша история тестов:\n\n"
        
        # ОБНОВЛЕННЫЙ КОД ФОРМИРОВАНИЯ ИСТОРИИ
        for i, test in enumerate(tests, 1):
            total = test['total_questions']
            correct = test['correct_answers']
            incorrect = test['incorrect_answers']
            
            # Проверка на None
            if total is None or correct is None:
                continue
            
            percentage = round((correct / total) * 100) if total > 0 else 0
            
            # Форматирование времени
            start_time = test['start_time'].astimezone(db._moscow_tz)
            start_str = start_time.strftime('%d.%m.%Y %H:%M')
            
            duration = "не завершен"
            if test['end_time']:
                end_time = test['end_time'].astimezone(db._moscow_tz)
                duration_seconds = (end_time - start_time).total_seconds()
                minutes = int(duration_seconds // 60)
                seconds = int(duration_seconds % 60)
                duration = f"{minutes} мин {seconds} сек"
            
            history_message += (
                f"🔹 Тест #{i} от {start_str}\n"
                f"   ✅ {correct}/{total} ({percentage}%)\n"
                f"   ❌ Ошибки: {incorrect}\n"
                f"   ⏱ Длительность: {duration}\n\n"
            )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="menu")],
            [InlineKeyboardButton(text="📝 Посмотреть детали", callback_data="last_test_details")]
        ])
        
        if isinstance(update, types.CallbackQuery):
            await update.message.edit_text(history_message, reply_markup=keyboard)
        else:
            await update.answer(history_message, reply_markup=keyboard)
            
    except Exception as e:
        logger.error(f"Error showing test history: {e}")
        error_msg = "❌ Произошла ошибка при получении истории тестов."
        if isinstance(update, types.CallbackQuery):
            await update.message.answer(error_msg)
        else:
            await update.answer(error_msg)

@dp.message(Command("last_test"))
async def show_last_test_command(message: types.Message):
    """Показывает результаты последнего теста"""
    await show_last_test_results(message)

async def show_last_test_results(update: Union[types.Message, types.CallbackQuery]):
    """Общая функция для отображения результатов последнего теста"""
    try:
        user_id = update.from_user.id
        test_results = await db.get_user_last_test_results(user_id)
        
        if not test_results:
            text = "📭 У вас нет завершенных тестов."
            if isinstance(update, types.CallbackQuery):
                await update.message.answer(text)
            else:
                await update.answer(text)
            return
            
        response = "📝 <b>Результаты последнего теста:</b>\n\n"
        for i, result in enumerate(test_results, 1):
            status = "✅" if result['is_correct'] else "❌"
            response += (
                f"{i}. {status} Слово: <b>{result['word']}</b>\n"
                f"   Ваш ответ: {result['user_answer']}\n"
                f"   Правильно: {result['correct_answer']}\n\n"
            )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="test_history")]
        ])
        
        if isinstance(update, types.CallbackQuery):
            await update.message.edit_text(response, parse_mode="HTML", reply_markup=keyboard)
        else:
            await update.answer(response, parse_mode="HTML", reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error showing last test results: {e}")
        error_msg = "❌ Произошла ошибка при получении результатов."
        if isinstance(update, types.CallbackQuery):
            await update.message.answer(error_msg)
        else:
            await update.answer(error_msg)

@dp.callback_query(F.data == "last_test_details")
async def show_last_test_details(callback: types.CallbackQuery):
    """Показывает детали последнего теста"""
    await show_last_test_results(callback)

@dp.callback_query(F.data == "menu")
async def back_to_menu(callback: types.CallbackQuery, state: FSMContext):
    """Возвращает в главное меню"""
    try:
        keyboard = await create_words_keyboard(callback.from_user.id)
        await callback.message.edit_text("Главное меню:", reply_markup=keyboard)
        await state.clear()
    except Exception as e:
        logger.error(f"Menu error: {e}")
        await callback.answer("Ошибка возврата в меню.")
    finally:
        await callback.answer()

async def main():
    """Основная функция запуска бота"""
    try:
        await db.create_pool()
        await db.initialize_database()
        logger.info("Database pool initialized successfully")
        logger.info("Starting bot...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Bot error: {e}")
    finally:
        if hasattr(db, 'pool') and db.pool is not None:
            await db.pool.close()
            logger.info("Database pool closed")
        await bot.session.close()
        logger.info("Bot session closed")

if __name__ == "__main__":
    asyncio.run(main())