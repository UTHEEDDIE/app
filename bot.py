import logging
import json
from telegram import Update, Chat
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from collections import defaultdict
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone
from dotenv import load_dotenv
import os
import sqlite3

# Загрузка переменных окружения из .env файла
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Включаем логирование с правильным форматом
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация базы данных
conn = sqlite3.connect('bot_data.db')
c = conn.cursor()
c.execute('''
CREATE TABLE IF NOT EXISTS statistics (
    date TEXT,
    user_id INTEGER,
    message_type TEXT,
    count INTEGER,
    PRIMARY KEY (date, user_id, message_type)
)
''')
c.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT
)
''')
conn.commit()

# Файл для хранения данных привязки
CONFIG_FILE = 'config.json'

# Чтение конфигурации из файла
def read_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as file:
            return json.load(file)
    return {}

# Запись конфигурации в файл
def write_config(config):
    with open(CONFIG_FILE, 'w') as file:
        json.dump(config, file)

# Инициализация переменных для хранения ID группы и ID администратора
config = read_config()
group_id = config.get('group_id')
admin_id = config.get('admin_id')

def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text('Привет! Я бот, который считает количество сообщений в группе. Используйте команду /bind в личном чате, чтобы привязать меня к группе.')

def bind(update: Update, context: CallbackContext) -> None:
    if update.message.chat.type == 'private':
        global group_id, admin_id
        if group_id is None:
            update.message.reply_text('Для привязки бота сначала упомяните его в группе, затем используйте команду /bind в личном чате.')
            return
        if not context.bot.get_chat_member(group_id, update.message.from_user.id).status in ['administrator', 'creator']:
            update.message.reply_text('Вы должны быть администратором группы для привязки бота.')
            return
        admin_id = update.message.from_user.id
        config['admin_id'] = admin_id
        write_config(config)
        update.message.reply_text('Бот успешно привязан к группе.')
        logger.info(f'Бот привязан к группе {group_id} администратором {admin_id}')
    else:
        update.message.reply_text('Эту команду можно использовать только в личном чате с ботом.')

def count_message(update: Update, context: CallbackContext) -> None:
    global group_id
    if update.message.chat_id == group_id:
        user_id = update.message.from_user.id
        date = datetime.now().strftime('%Y-%m-%d')
        if update.message.text:
            message_type = 'text'
        elif update.message.photo:
            message_type = 'photo'
        elif update.message.video:
            message_type = 'video'
        elif update.message.document:
            message_type = 'document'
        elif update.message.audio:
            message_type = 'audio'
        elif update.message.voice:
            message_type = 'voice'
        else:
            message_type = 'other'

        # Сохранение информации о пользователе
        user = update.message.from_user
        with sqlite3.connect('bot_data.db') as conn:
            c = conn.cursor()
            c.execute('''
                INSERT INTO users (user_id, username, first_name, last_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id)
                DO UPDATE SET username = ?, first_name = ?, last_name = ?
            ''', (user.id, user.username, user.first_name, user.last_name, user.username, user.first_name, user.last_name))
            c.execute('''
                INSERT INTO statistics (date, user_id, message_type, count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(date, user_id, message_type)
                DO UPDATE SET count = count + 1
            ''', (date, user_id, message_type))
            conn.commit()
        logger.info(f"Message from user ID {user_id} of type {message_type} counted.")
    elif update.message.chat.type in [Chat.GROUP, Chat.SUPERGROUP]:
        if '@' + context.bot.username in update.message.text:
            group_id = update.message.chat_id
            config['group_id'] = group_id
            write_config(config)
            logger.info(f'Group ID set to {group_id}')
            context.bot.send_message(chat_id=update.message.chat_id, text='ID группы сохранен. Теперь используйте команду /bind в личном чате.')

def generate_report():
    date = datetime.now().strftime('%Y-%m-%d')
    report_message = f"Статистика сообщений за {date} в привязанной группе:\n"
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute('''
            SELECT s.user_id, u.username, u.first_name, u.last_name, s.message_type, s.count 
            FROM statistics s
            LEFT JOIN users u ON s.user_id = u.user_id
            WHERE s.date = ?
        ''', (date,))
        rows = c.fetchall()
    stats = defaultdict(lambda: defaultdict(int))
    users = {}
    for row in rows:
        user_id, username, first_name, last_name, message_type, count = row
        stats[user_id][message_type] = count
        users[user_id] = {'username': username, 'first_name': first_name, 'last_name': last_name}
    for user_id, message_types in stats.items():
        messages = ", ".join([f"{message_type}: {count}" for message_type, count in message_types.items()])
        user_info = users[user_id]
        if user_info['username']:
            user_display = f"@{user_info['username']}"
        else:
            user_display = f"{user_info['first_name']} {user_info['last_name']}".strip()
        report_message += f"\n[{user_display}](tg://user?id={user_id}): {messages}"
    return report_message

def send_daily_report(context: CallbackContext) -> None:
    global admin_id
    if admin_id:
        report_message = generate_report()
        context.bot.send_message(chat_id=admin_id, text=report_message, parse_mode='Markdown')

def report(update: Update, context: CallbackContext) -> None:
    if update.message.chat.type == 'private':
        global group_id
        if group_id is None:
            update.message.reply_text('Бот не привязан ни к одной группе.')
            return
        report_message = generate_report()
        update.message.reply_text(report_message, parse_mode='Markdown')
    else:
        update.message.reply_text('Эту команду можно использовать только в личном чате с ботом.')

def reset_statistics() -> None:
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute('DELETE FROM statistics')
        conn.commit()
    logger.info('Статистика сообщений сброшена')

def main() -> None:
    updater = Updater(TOKEN)

    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("bind", bind))
    dispatcher.add_handler(CommandHandler("report", report))

    # Обработчик для учета текстовых и медиа сообщений
    dispatcher.add_handler(MessageHandler(
        Filters.text | Filters.photo | Filters.video | Filters.document | Filters.audio | Filters.voice,
        count_message
    ))

    # Настройка планировщика для сброса статистики и отправки ежедневного отчета
    scheduler = BackgroundScheduler(timezone=timezone('Europe/Moscow'))
    trigger_reset = CronTrigger(hour=0, minute=0, timezone=timezone('Europe/Moscow'))  # Запуск в полночь по московскому времени
    scheduler.add_job(reset_statistics, trigger_reset)
    scheduler.add_job(send_daily_report, trigger_reset, args=[updater.dispatcher])
    scheduler.start()

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
