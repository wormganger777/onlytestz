import sqlite3
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Создаем заглушку для модуля imghdr, который отсутствует в Python 3.13
import sys
class ImghdrMock:
    def what(self, *args, **kwargs):
        return 'jpeg'  # Возвращаем значение по умолчанию
sys.modules['imghdr'] = ImghdrMock()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters, CallbackContext
import pytz
from datetime import datetime, timedelta
import asyncio
from asyncio import Lock
import os
import re
import undetected_chromedriver as uc
from selenium.webdriver.common.action_chains import ActionChains
import time
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException, NoSuchElementException
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from selenium.webdriver.chrome.service import Service
from glob import glob
import logging
import traceback
from config import TOKEN

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,  # Устанавливаем уровень DEBUG для более подробного логирования
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot_debug.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Добавляем логирование для telegram библиотеки
telegram_logger = logging.getLogger('telegram')
telegram_logger.setLevel(logging.DEBUG)

logger.info("Бот запускается...")

bot = Bot(token=TOKEN)

event_queue = asyncio.Queue()

scheduler = AsyncIOScheduler()

user_roles = {
    "owner": "wormganger777",  
    "head_admins": {"vip_way_trip"},  
    "admins": {}  
}

pending_groups = {}
user_time_slots = {}

db_connection = sqlite3.connect("database.db", check_same_thread=False)
cursor = db_connection.cursor()

conn = sqlite3.connect("chat_logs.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS chat_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    user_id INTEGER,
    username TEXT,
    message_text TEXT,
    file_path TEXT,
    message_id INTEGER,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

cursor.execute("""
CREATE TABLE IF NOT EXISTS employee_time_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE,
    start_time TIME,
    end_time TIME,
    sender_chat_id TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

cursor.execute("""
CREATE TABLE IF NOT EXISTS shift_totals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    entry_number INTEGER,
    exit_number INTEGER,
    total INTEGER,
    entry_time DATETIME,
    exit_time DATETIME
)
""")
conn.commit()

cursor.execute("""
CREATE TABLE IF NOT EXISTS onlymonster_credentials (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    email TEXT,
    password TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

def get_chat_id_from_db(username):
    try:
        cursor.execute("SELECT chat_id FROM user_settings WHERE username = ?", (username,))
        result = cursor.fetchone()
        if result:
            return result[0]
        else:
            print(f"Chat ID for users @{username} not found.")
            return None
    except Exception as e:
        print(f"Error while retrieving chat_id: {e}")
        return None

def check_stat_command(update: Update, context: CallbackContext) -> None:
    try:
        username = update.message.from_user.username
        if username != user_roles["owner"] and username not in user_roles["head_admins"]:
            update.message.reply_text("❌ You do not have permissions to execute this command.")
            return

        telegram_id = update.message.from_user.id
        cursor.execute("""SELECT email, password FROM onlymonster_credentials WHERE telegram_id = ?""", (telegram_id,))
        credentials = cursor.fetchone()

        if not credentials:
            update.message.reply_text("❌ No credentials found. Use the '/login' command to log in.")
            return

        email, password = credentials

        message_parts = update.message.text.split()
        if len(message_parts) != 3:
            update.message.reply_text("❌ Incorrect command format. Use: '/check_stat' <start_date> <end_date>.")
            return
        
        start_date = message_parts[1]
        end_date = message_parts[2]

        status_message = update.message.reply_text("🔄 Performing data exports...")
        
        # Поскольку асинхронные функции не поддерживаются, используем синхронную версию или отключаем функциональность
        # file_path = manager.check_stat_sync(update, email, password, start_date, end_date)
        
        downloads_dir = r"C:\Users\Арсений\Downloads" #change the "user" to your own
        file_path = find_latest_file(downloads_dir)

        if file_path:
            print(f"Файл найден: {file_path}")
            status_message.edit_text("✅ Data successfully exported!")
            update.message.reply_document(document=open(file_path, "rb"))
        else:
            status_message.edit_text("❌ Failed to find the exported file.")

    except Exception as e:
        print(f"Ошибка в check_stat_command: {str(e)}")
        update.message.reply_text(f"❌ An error occurred while executing the command: {str(e)}")

    finally:
        if manager.driver:
            manager.driver.quit()


class OnlyMonsterManager:
    def __init__(self):
        self.driver = None 

    def setup_driver(self):
        if self.driver is None:
            options = uc.ChromeOptions()
            options.add_argument('--headless') #you can remove this line to see how the bot works when you punch in the "/check_stat" command
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--window-size=1920,1080')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36')

            chromedriver_path = r"C:\Users\Арсений\Downloads\chromedriver.exe" #change "user" to ur name of system
            service = Service(chromedriver_path)

            self.driver = uc.Chrome(service=service, options=options)


    def login_to_onlymonster(self, update: Update, email: str, password: str) -> bool:
        username = update.message.from_user.username
        if username != user_roles["owner"] and username not in user_roles["head_admins"]:
            update.message.reply_text("❌You do not have permissions to execute this command.")
            return False
        try:
            self.setup_driver()
            self.driver.get("https://onlymonster.ai/auth/signin")

            wait = WebDriverWait(self.driver, 60)

            print("Authorization...")
            email_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "[name='identifier']")))
            password_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "[name='password']")))

            email_field.send_keys(email)
            password_field.send_keys(password)

            self.find_and_click_button(wait, css_selector=".cl-formButtonPrimary")

            
            try:
                wait.until(lambda d: "/panel/creators" in d.current_url)
                print("✅ Successful log in")
                
                
                if self.driver:
                    self.driver.quit()
                    self.driver = None
                    
                return True
            except Exception as e:
                print(f"❌ Error on URL validation after login: {str(e)}")
                return False

        except Exception as e:
            print(f"❌ Error attempting to log in: {str(e)}")
            return False
        finally:
            
            if self.driver:
                self.driver.quit()
                self.driver = None

    def wait_for_page_load(self, timeout=120):  
        print("Waiting for the page to fully load...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                page_state = self.driver.execute_script('return document.readyState;')
                jquery_state = self.driver.execute_script('''return (typeof jQuery !== "undefined") ? jQuery.active == 0 : true;''')
                loading_elements = self.driver.find_elements(By.CSS_SELECTOR, '.loading-indicator, .loader, .spinner')
                no_loaders = len(loading_elements) == 0
                
                xhr_complete = self.driver.execute_script('''return window.performance
                        .getEntriesByType('resource')
                        .filter(e => e.initiatorType === 'xmlhttprequest')
                        .every(e => e.responseEnd > 0);''')
                
                if page_state == 'complete' and jquery_state and no_loaders and xhr_complete:
                    print("✅ The page is fully loaded")
                    time.sleep(2)
                    return True
            
            except Exception as e:
                print(f"Error when checking page load: {str(e)}")
            
            time.sleep(1)
        
        print("❌ Timeout when waiting for page load")
        return False

    def find_and_click_button(self, wait, css_selector=None, xpath=None, button_text=None, retries=3):
        for attempt in range(retries):
            try:
                if button_text == "Export":
                    possible_locators = [
                        (By.XPATH, "//button[normalize-space()='Export']"),
                        (By.XPATH, "//button[contains(., 'Export') and not(contains(., 'Excel'))]"),
                        (By.CSS_SELECTOR, "button:has(svg) span:contains('Export')"),
                        (By.XPATH, "//button[.//svg and contains(normalize-space(), 'Export')]"),
                    ]
                    
                    for locator in possible_locators:
                        try:
                            button = wait.until(EC.element_to_be_clickable(locator))
                            if button:
                                break
                        except:
                            continue
                else:
                    button = None
                    if css_selector:
                        button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, css_selector)))
                    elif xpath:
                        button = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
                    elif button_text:
                        button = wait.until(EC.element_to_be_clickable(
                            (By.XPATH, f"//button[contains(normalize-space(), '{button_text}')]")
                        ))

                if button:
                    print(f"Button found: {button.text}")
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center', behavior: 'smooth'});", button)
                    time.sleep(2)
                    try:
                        button.click()
                    except:
                        try:
                            self.driver.execute_script(
                                "arguments[0].dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));", 
                                button
                            )
                        except:
                            actions = ActionChains(self.driver)
                            actions.move_to_element(button)
                            actions.click()
                            actions.perform()
                    
                    print(f"Successful button click: {button_text or 'Unknown'}")
                    return True
                
            except Exception as e:
                print(f"Attempt {attempt + 1}/{retries} failed: {str(e)}")
                time.sleep(2)
        
        print(f"❌ Failed to click the button after {retries} attempts")
        return False

    def format_date(self, date_str):
        date_obj = datetime.strptime(date_str, "%d.%m.%Y")
        return date_obj.strftime("%m-%d-%Y %I:%M %p")  

    def check_stat(self, update: Update, email: str, password: str, start_date: str, end_date: str) -> str:
        try:
            self.setup_driver()
            self.driver.get("https://onlymonster.ai/auth/signin")

            wait = WebDriverWait(self.driver, 60)

            print("Авторизация...")
            email_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "[name='identifier']")))
            password_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "[name='password']")))
            email_field.send_keys(email)
            password_field.send_keys(password)

            self.find_and_click_button(wait, css_selector=".cl-formButtonPrimary")

            wait.until(lambda d: "/panel/creators" in d.current_url)
            print("✅ Successful Log In.")

            self.driver.get("https://onlymonster.ai/panel/chatter-metrics/")
            if not self.wait_for_page_load():
                return None

            print("✅ The statistics page is loaded")

            
            checkbox = wait.until(EC.presence_of_element_located((By.ID, "likeOnlyfans")))
            checkbox.click()
            time.sleep(5)
            
            start_date_formatted = self.format_date(start_date)
            end_date_formatted = self.format_date(end_date)
            date_input = self.driver.find_element(By.NAME, "date")
        
            date_input.click()
            date_input.click()  
            time.sleep(1)  
            date_input.clear()  
            time.sleep(2)

            print(f"Start Date: {start_date_formatted}, End Date: {end_date_formatted}")
            date_input.send_keys(f"{start_date_formatted} ~ {end_date_formatted}")

            
            select_time_button = self.driver.find_element(By.XPATH, "//button[contains(text(),'Select time')]")
            
            select_time_button.click()
            time.sleep(3)

            try:
                
                WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located(
                        (By.XPATH, "//button[contains(@class, 'primary-btn') and contains(., 'Select date')]")
                    )
                )
                print("The date selection window is found. Re-enter the time...")
                
                date_input.click()
                date_input.send_keys(Keys.CONTROL + "a")
                time.sleep(1)
                date_input.send_keys(Keys.BACKSPACE)
                time.sleep(0.5)
                date_input.send_keys(f"{start_date_formatted} ~ {end_date_formatted}")
                self.driver.find_element(By.TAG_NAME, "body").click()
            except:
                print("The date selection window did not appear.")

            
            time.sleep(5)

            
            export_button = wait.until(EC.presence_of_element_located((By.XPATH, "//button[normalize-space()='Export']")))
            if export_button.is_displayed() and export_button.is_enabled():
                print("Found button: Export")
                
                self.driver.execute_script("arguments[0].scrollIntoView(true);", export_button)
                time.sleep(1)  
                self.driver.execute_script("arguments[0].click();", export_button)
                print("Successful click on the Export button")
                time.sleep(2)
            else:
                print("The Export button is not visible or unavailable.")
                return None

            
            export_to_excel_button = wait.until(EC.presence_of_element_located((By.XPATH, "//button[normalize-space()='Export to Excel']")))

            if export_to_excel_button.is_displayed() and export_to_excel_button.is_enabled():
                print("Found button: Export to Excel")
                
                self.driver.execute_script("arguments[0].scrollIntoView(true);", export_to_excel_button)
                time.sleep(1)  
                self.driver.execute_script("arguments[0].click();", export_to_excel_button)
                print("Successful click on the Export to Excel button")
                time.sleep(2)
            else:
                print("The Export to Excel button is not visible or available.")
                return None

            
            download_folder = r"C:\Users\Арсений\Downloads" #change it to your download path

            if not os.path.exists(download_folder):
                print(f"❌ The {download_folder} folder does not exist.")
                return None

            print("Waiting for the file download to complete...")
            time.sleep(5)  

            downloaded_file = None
            for _ in range(10):  
                files = [f for f in os.listdir(download_folder) if f.endswith(".xlsx")]
                if files:
                    downloaded_file = os.path.join(download_folder, files[0])
                    print(f"File found: {downloaded_file}")
                    break
                time.sleep(1)

            if downloaded_file:
                return downloaded_file
            else:
                print("❌ The file has not been uploaded.")
                return None
        except Exception as e:
            print(f"Error in check_stat: {e}")
            return None


def find_latest_file(directory: str, extension: str = "*.xlsx") -> str:
    
    files = glob(os.path.join(directory, extension))
    
    if not files:
        return None  

    latest_file = max(files, key=os.path.getmtime)
    return latest_file

manager = OnlyMonsterManager()  

async def check_stat_command(update: Update, context: CallbackContext) -> None:
    try:
        username = update.message.from_user.username
        if username != user_roles["owner"] and username not in user_roles["head_admins"]:
            await update.message.reply_text("❌ You do not have permissions to execute this command.")
            return

        telegram_id = update.message.from_user.id
        cursor.execute("""SELECT email, password FROM onlymonster_credentials WHERE telegram_id = ?""", (telegram_id,))
        credentials = cursor.fetchone()

        if not credentials:
            await update.message.reply_text("❌ No credentials found. Use the '/login' command to log in.")
            return

        email, password = credentials

        message_parts = update.message.text.split()
        if len(message_parts) != 3:
            await update.message.reply_text("❌ Incorrect command format. Use: '/check_stat' <start_date> <end_date>.")
            return
        
        start_date = message_parts[1]
        end_date = message_parts[2]

        status_message = await update.message.reply_text("🔄 Performing data exports...")
        try:
            file_path = await manager.check_stat(update, email, password, start_date, end_date)
        
            if file_path:
                print(f"Файл найден: {file_path}")
                await status_message.edit_text("✅ Data successfully exported!")
                await update.message.reply_document(document=open(file_path, "rb"))
            else:
                await status_message.edit_text("❌ Failed to find the exported file.")
        except Exception as e:
            await status_message.edit_text(f"❌ Error during export: {str(e)}")

    except Exception as e:
        print(f"Ошибка в check_stat_command: {str(e)}")
        await update.message.reply_text("❌ An error occurred while executing the command.")

    finally:
        if manager.driver:
            manager.driver.quit()


def notify_on_startup(context):
    try:
        kyiv_tz = pytz.timezone('Europe/Kyiv') #correct it to your time zone.
        now = datetime.now(kyiv_tz).strftime("%Y-%m-%d %H:%M:%S") #correct it to your time zone.
        
        message = f"🤖 Bot successfully launched! \nServer time: {now}\n\n"
        message += "🕒 Employee time slots:\n"

        for username, slot in user_time_slots.items():
            start_time = slot["start_time"].strftime("%H:%M")
            end_time = slot["end_time"].strftime("%H:%M")
            message += f"@{username}: {start_time} - {end_time}\n"

        context.bot.send_message(chat_id=1955277860, text=message)

    except Exception as e:
        print(f"Error when sending a notification: {e}")


def main():
    try:
        logger.info("Инициализация бота...")
        
        # Инициализируем планировщик
        scheduler = AsyncIOScheduler()
        scheduler.start()
        logger.info("Планировщик запущен")
        
        # Создаем приложение
        logger.info(f"Создание приложения с токеном: {TOKEN[:5]}...")
        updater = Updater(TOKEN, use_context=True)
        
        # Загружаем временные слоты
        logger.info("Загрузка временных слотов...")
        load_saved_time_slots()
        logger.info("Временные слоты загружены")
        
        # Запускаем уведомление о запуске
        updater.job_queue.run_once(notify_on_startup, when=0)
        
        # Регистрируем обработчики команд
        logger.info("Регистрация обработчиков команд...")
        updater.dispatcher.add_handler(CommandHandler("start", start))
        updater.dispatcher.add_handler(CommandHandler("set_role", set_role))
        updater.dispatcher.add_handler(CommandHandler("remove_role", remove_role))
        updater.dispatcher.add_handler(CommandHandler("manage_surveys", manage_surveys))
        updater.dispatcher.add_handler(CommandHandler("add_to_chat", add_to_chat))
        updater.dispatcher.add_handler(CommandHandler("verify_chat", verify_chat))
        updater.dispatcher.add_handler(CommandHandler("show_logs", show_logs))
        updater.dispatcher.add_handler(CommandHandler("clear_logs", clear_logs))
        updater.dispatcher.add_handler(CommandHandler("set_time_slot", set_time_slot))
        updater.dispatcher.add_handler(CommandHandler("check_time", check_time))
        updater.dispatcher.add_handler(CommandHandler("help", help))
        updater.dispatcher.add_handler(CommandHandler("get_chat_id", get_chat_id))
        updater.dispatcher.add_handler(CommandHandler("show_balance", show_balance))
        updater.dispatcher.add_handler(CommandHandler("del_time", del_time))
        updater.dispatcher.add_handler(CommandHandler("login", login_command))
        updater.dispatcher.add_handler(CommandHandler("check_stat", check_stat_command))
        updater.dispatcher.add_handler(CommandHandler("restart_bot", restart_bot))
        updater.dispatcher.add_handler(CommandHandler("clear_balance", clear_balance))
        logger.info("Команды зарегистрированы")
        
        # Регистрируем обработчики callback-запросов
        updater.dispatcher.add_handler(CallbackQueryHandler(select_surveys, pattern="^select_admin:"))
        updater.dispatcher.add_handler(CallbackQueryHandler(assign_surveys, pattern="^assign_survey:|^assign_done$"))
        logger.info("Обработчики callback-запросов зарегистрированы")
        
        # Регистрируем обработчики сообщений
        updater.dispatcher.add_handler(MessageHandler(
            Filters.chat_type.supergroup & 
            Filters.text & 
            ~Filters.command,
            monitor_messages
        ), group=1)
        logger.info("Обработчик сообщений monitor_messages зарегистрирован")
        
        updater.dispatcher.add_handler(MessageHandler(
            Filters.chat_type.supergroup & 
            ~Filters.command,
            log_messages
        ), group=2)
        logger.info("Обработчик сообщений log_messages зарегистрирован")
        
        # Запускаем бота
        logger.info("Запуск бота...")
        updater.start_polling()
        updater.idle()
        
        logger.info("Бот запущен и готов к работе!")
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        logger.error(traceback.format_exc())

def login_command(update: Update, context: CallbackContext) -> None:
    try:
        # Проверяем права пользователя
        username = update.message.from_user.username
        if username != user_roles["owner"] and username not in user_roles["head_admins"]:
            update.message.reply_text("❌ You do not have permissions to execute this command.")
            return

        # Проверяем формат команды
        if len(context.args) != 2:
            update.message.reply_text(
                "❌ Incorrect command format.\n"
                "Use: /login email password"
            )
            return

        email = context.args[0]
        password = context.args[1]

        manager_obj = OnlyMonsterManager()
        
        status_message = update.message.reply_text("🔄 Login to OnlyMonster is in progress...")

        success = manager_obj.login_to_onlymonster(update, email, password)

        if success:
            # Сохраняем учетные данные в базе
            telegram_id = update.message.from_user.id
            cursor.execute("""
                INSERT OR REPLACE INTO onlymonster_credentials 
                (telegram_id, username, email, password) 
                VALUES (?, ?, ?, ?)
            """, (telegram_id, username, email, password))
            conn.commit()

            status_message.edit_text("✅ Successful entry into OnlyMonster!")
        else:
            status_message.edit_text("❌ Could not log in to OnlyMonster. Check your credentials.")

    except Exception as e:
        print(f"Ошибка в login_command: {str(e)}")
        update.message.reply_text(f"❌ There's been a mistake: {str(e)}")

def load_saved_time_slots():
    try:
        cursor.execute("SELECT username, start_time, end_time, sender_chat_id FROM employee_time_slots")
        saved_slots = cursor.fetchall()
        
        kyiv_tz = pytz.timezone('Europe/Kyiv') #change to the desired time zone
        now = datetime.now(kyiv_tz) #change to the desired time zone
        
        messages = []  

        for username, start_time_str, end_time_str, sender_chat_id in saved_slots:
            start_time = kyiv_tz.localize(datetime.combine(now.date(), datetime.strptime(start_time_str, "%H:%M").time())) #change "kyiv_tz" to your time zone
            end_time = kyiv_tz.localize(datetime.combine(now.date(), datetime.strptime(end_time_str, "%H:%M").time())) #change "kyiv_tz" to your time zone
            
            if end_time <= start_time:
                end_time += timedelta(days=1)
                
            user_time_slots[username] = {
                "start_time": start_time,
                "end_time": end_time
            }

            scheduler.add_job(
                schedule_user_check_with_entry,
                CronTrigger(hour=start_time.hour, minute=start_time.minute),
                kwargs={
                    "target_username": username,
                    "start_time": start_time,
                    "end_time": end_time,
                    "sender_chat_id": sender_chat_id,  
                    "bot": bot  
                },
                id=f"check_{username}",
                replace_existing=True
            )
            print(f"DEBUG: Task restored for {username}: {start_time} - {end_time}, sender_chat_id={sender_chat_id}")
            print(f"DEBUG: Downloaded from base: username={username}, start_time={start_time_str}, end_time={end_time_str}, sender_chat_id={sender_chat_id}")

            
            messages.append(
                f"User @{username}: time interval {start_time_str} - {end_time_str} has been successfully downloaded and is being analyzed."
            )
        
        print(f"Loaded {len(saved_slots)} time slots from the database")
        
        
        if messages:
            print("\n".join(messages))

    except Exception as e:
        print(f"Error when loading time slots: {e}")

def schedule_user_check_with_entry(target_username, start_time, end_time, sender_chat_id, bot):
    try:
        print(f"\nDEBUG schedule_user_check_with_entry:")
        print(f"- Target username: {target_username}")
        print(f"- Start time: {start_time}")
        print(f"- End time: {end_time}")
        print(f"- Sender Chat ID: {sender_chat_id}")

        kyiv_tz = pytz.timezone('Europe/Kyiv')  # correct it to your time zone.
        now = datetime.now(kyiv_tz)  # correct it to your time zone.

        print(f"Current time: {now}")
        print(f"Set start time: {start_time}")
        print(f"Set end time: {end_time}")

        # Проверяем, входил ли пользователь в систему
        print(f"- Current state of entry_logs: {entry_logs}")

        if target_username in entry_logs:
            log_entry = entry_logs[target_username]
            bot.send_message(
                chat_id=sender_chat_id,
                text=f"✅ Employee @{target_username} came in on time.\nMessage: '{log_entry['message']}' (in {log_entry['timestamp']})"
            )
            entry_logs.pop(target_username, None)
        else:
            bot.send_message(
                chat_id=sender_chat_id,
                text=f"❌ Employee @{target_username} did not come in within the specified time frame."
            )
            print(f"User @{target_username} did not check in")

    except Exception as e:
        print(f"- ERROR in schedule_user_check_with_entry: {e}")
        import traceback
        traceback.print_exc()

def start(update: Update, context: CallbackContext) -> None:
    username = update.message.from_user.username

    if username is None:
        update.message.reply_text("You do not have username '@' in Telegram. Please set it in the settings.")
        return

    if username == user_roles["owner"]:
        update.message.reply_text(f"Hi, @{username}! You are the owner of the bot.")
        return

    if username in user_roles["head_admins"]:
        update.message.reply_text(f"Hi, @{username}! The owner has appointed you as the head admin.")
        return

    if username in user_roles["admins"]:
        update.message.reply_text(f"Hi, @{username}! The owner or head admin has appointed you as an admin.")
        return

    update.message.reply_text(f"Hi, @{username}! You have no role in this bot.")


def set_role(update: Update, context: CallbackContext) -> None:
    username = update.message.from_user.username
    
    if username != user_roles["owner"] and username not in user_roles["head_admins"]:
        update.message.reply_text("❌ You do not have permissions to execute this command.")
        return
    
    args = context.args
    if len(args) < 2:
        update.message.reply_text("Use: /set_role <username> <role>. Роли: admin, head_admin.")
        return

    target_username = args[0].lstrip("@")  
    role = args[1].lower()
    
    if role not in ["admin", "head_admin"]:
        update.message.reply_text("Wrong role. Available roles: admin, head_admin.")
        return
    
    if role == "admin":
        user_roles["admins"][target_username] = {}
        update.message.reply_text(f"User @{target_username} has been assigned the 'Admin' role.")
    elif role == "head_admin":
        if username != user_roles["owner"]:
            update.message.reply_text("Only the owner can assign the 'Head Admins' role.")
            return
        user_roles["head_admins"].add(target_username)
        update.message.reply_text(f"User @{target_username} has been assigned the role of 'Head Admins'.")


def remove_role(update: Update, context: CallbackContext) -> None:
    username = update.message.from_user.username
    
    if username not in user_roles["head_admins"] and username != user_roles["owner"]:
        update.message.reply_text("❌ You do not have permissions to execute this command.")
        return
    
    args = context.args
    if len(args) < 1:
        update.message.reply_text("Use: /remove_role <username>. Specify the username.")
        return

    target_username = args[0].lstrip("@")  
    
    if target_username in user_roles["admins"]:
        del user_roles["admins"][target_username]
        update.message.reply_text(f"The admin role of @{target_username} has been removed.")
    elif target_username in user_roles["head_admins"]:
        user_roles["head_admins"].remove(target_username)
        update.message.reply_text(f"The head admin role of @{target_username} has been removed.")
    else:
        update.message.reply_text(f"The user @{target_username} has no role.")


def manage_surveys(update: Update, context: CallbackContext) -> None:
    username = update.message.from_user.username
    
    if username != user_roles["owner"] and username not in user_roles["head_admins"]:
        update.message.reply_text("❌ You do not have permissions to execute this command.")
        return
    
    buttons = []
    for admin_username in user_roles["admins"]:
        buttons.append([InlineKeyboardButton(f"Admin: @{admin_username}", callback_data=f"select_admin:{admin_username}")])
    for head_admin_username in user_roles["head_admins"]:
        buttons.append([InlineKeyboardButton(f"Head admin: @{head_admin_username}", callback_data=f"select_admin:{head_admin_username}")])

    if not buttons:
        update.message.reply_text("There are no available admins or head admins to assign profiles to.")
        return

    reply_markup = InlineKeyboardMarkup(buttons)
    update.message.reply_text("Select an admin or head admin to assign questionnaires:", reply_markup=reply_markup)


def select_surveys(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    
    _, admin_username = query.data.split(":")
    context.user_data["selected_admin"] = admin_username  
    
    surveys_for_admin = admin_surveys.get(admin_username, [])
    
    buttons = []
    for survey in available_surveys:
        button_label = f"✅ {survey}" if survey in surveys_for_admin else survey
        buttons.append([InlineKeyboardButton(button_label, callback_data=f"assign_survey:{survey}")])

    buttons.append([InlineKeyboardButton("Готово", callback_data="assign_done")])
    reply_markup = InlineKeyboardMarkup(buttons)

    query.edit_message_text(
        f"Which profiles do you wish to choose for @{admin_username}? Choose from the list below:",
        reply_markup=reply_markup
    )


def assign_surveys(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()

    if "selected_admin" not in context.user_data:
        query.edit_message_text("There's been an error. Please start again.")
        return

    admin_username = context.user_data["selected_admin"]
    if admin_username not in admin_surveys:
        admin_surveys[admin_username] = []

    if query.data.startswith("assign_survey:"):
        _, survey = query.data.split(":")
        if survey not in admin_surveys[admin_username]:
            admin_surveys[admin_username].append(survey)
        
        surveys_for_admin = admin_surveys.get(admin_username, [])

        buttons = []
        for survey in available_surveys:
            button_label = f"✅ {survey}" if survey in surveys_for_admin else survey
            buttons.append([InlineKeyboardButton(button_label, callback_data=f"assign_survey:{survey}")])

        buttons.append([InlineKeyboardButton("Готово", callback_data="assign_done")])
        reply_markup = InlineKeyboardMarkup(buttons)

        query.edit_message_text(
            f"The {survey} questionnaire has been added for @{admin_username}. You can select more or click 'Done'.",
            reply_markup=reply_markup
        )

    elif query.data == "assign_done":
        surveys = ", ".join(admin_surveys[admin_username]) or "no profiles"
        query.edit_message_text(f"Profiles for @{admin_username} have been saved: {surveys}.")


def add_to_chat(update: Update, context: CallbackContext) -> None:
    username = update.message.from_user.username
    if username != user_roles["owner"] and username not in user_roles["head_admins"]:
        update.message.reply_text("❌ You do not have permissions to execute this command.")
        return  

    if not context.args:  
        update.message.reply_text("Please specify the name of the group. For example: /add_to_chat ML045")
        return
    
    group_name = " ".join(context.args).strip()
    
    pending_groups[username] = group_name
    update.message.reply_text(
        f"You have requested to add a bot to the group: '{group_name}'. "
        "Now add the bot to this group, and then use the '/verify_chat' command."
    )


def verify_chat(update: Update, context: CallbackContext) -> None:
    username = update.message.from_user.username
    if username not in pending_groups:
        update.message.reply_text("You have not requested to be added to the supergroup.")
        return
    
    if not context.args:
        update.message.reply_text("Please provide the name of the group. For example: /verify_chat ML045")
        return
    
    group_name = " ".join(context.args).strip().lower()
    expected_group_name = pending_groups[username].strip().lower()
    
    print(f"User: @{username}, Name entered: '{group_name}', Expected name: '{expected_group_name}'")
    
    if group_name == expected_group_name:
        update.message.reply_text("Thanks for adding to the group! I will analyze the chats and sexters in the future")
        del pending_groups[username]  
    else:
        update.message.reply_text("The names are not identical. Please double-check.")


def help(update: Update, context: CallbackContext):
    help_text = (
        "Here are the available commands:\n"
        "/start - Start interacting with the bot\n"
        "/set_role - Set a role for a user\n"
        "/remove_role - Remove a role for a user\n"
        "/manage_surveys - Manage questionnaires\n"
        "/add_to_chat - Add bot to supergroup\n"
        "/verify_chat - Check if the bot has been added to the supergroup\n"
        "/show_logs - Show logs\n"
        "/clear_logs - Clear a certain number of logs\n"
        "/set_time_slot - Set the login time for the user's changeover\n"
        "/check_time - Check the set time of employees\n"
        "/get_chat_id - Define chat and group IDs\n"
        "/show_balance - Check employee balance\n"
        "/del_time - Remove the time period of entry for an employee\n"
        "/login - Authorize on the OnlyMonster website\n"
        "/check_stat - Download employee statistics from the OnlyMonster website\n"
        "/restart_bot - Restart bot\n"
        "/clear_balance - Clears the employee's balance\n"
        "/help - Bot commands\n"
    )
    update.message.reply_text(help_text)


def restart_bot(update: Update, context: CallbackContext) -> None:
    # Проверяем права пользователя
    username = update.message.from_user.username
    if username != user_roles["owner"] and username not in user_roles["head_admins"]:
        update.message.reply_text("❌ You do not have permissions to execute this command.")
        return  
    
    update.message.reply_text("The bot will be restarted...")

    # Ожидаем отправки сообщения
    time.sleep(2)

    # Перезапускаем бота
    os.execv(sys.executable, ['python'] + sys.argv)


# Объявляем глобальную переменную для опросов
admin_surveys = {}
available_surveys = ["ML01", "ML02", "ML03", "ML04", "ML05", "ML06", "ML07", "ML08"] #the name of the model profiles or the name of your group

entry_logs = {}  # Чтобы хранить информацию о входах пользователя

def log_messages(update: Update, context: CallbackContext) -> None:
    try:
        message = update.message
        if not message:
            return

        chat_id = update.effective_chat.id
        user_id = message.from_user.id
        username = message.from_user.username or "Unknown"
        message_text = message.text or ""
        message_id = message.message_id
        file_path = None

        # Сохраняем сообщение в базу данных
        cursor.execute("""
            INSERT INTO chat_logs 
            (chat_id, user_id, username, message_text, file_path, message_id) 
            VALUES (?, ?, ?, ?, ?, ?)
        """, (chat_id, user_id, username, message_text, file_path, message_id))
        conn.commit()

    except Exception as e:
        print(f"Ошибка в log_messages: {e}")

def monitor_messages(update: Update, context: CallbackContext) -> None:
    try:
        message = update.message
        if not message:
            return

        username = message.from_user.username
        if not username:
            return

        # Проверяем, есть ли пользователь во временных слотах
        if username in user_time_slots:
            kyiv_tz = pytz.timezone('Europe/Kyiv')  # Укажите ваш часовой пояс
            now = datetime.now(kyiv_tz)
            slot = user_time_slots[username]
            
            start_time = slot["start_time"]
            end_time = slot["end_time"]
            
            # Если сейчас в пределах временного слота
            if start_time <= now <= end_time:
                entry_logs[username] = {
                    'message': message.text or "No text",
                    'timestamp': now.strftime("%H:%M:%S")
                }
                print(f"User @{username} logged entry at {now}")

            # Сохраняем информацию для подсчета смены
            cursor.execute("""
                SELECT id FROM shift_totals 
                WHERE username = ? AND exit_time IS NULL
                ORDER BY id DESC LIMIT 1
            """, (username,))
            existing_shift = cursor.fetchone()
            
            if existing_shift:
                cursor.execute("""
                    UPDATE shift_totals 
                    SET exit_number = ?, exit_time = CURRENT_TIMESTAMP 
                    WHERE id = ?
                """, (message.message_id, existing_shift[0]))
            else:
                cursor.execute("""
                    INSERT INTO shift_totals 
                    (username, entry_number, entry_time) 
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                """, (username, message.message_id))
            
            conn.commit()
            
    except Exception as e:
        print(f"Ошибка в monitor_messages: {e}")

def get_chat_id(update: Update, context: CallbackContext) -> None:
    chat = update.effective_chat  
    chat_id = chat.id  
    thread_id = update.effective_message.message_thread_id  

    response = f"ID чата (chat_id): {chat_id}"
    if thread_id:
        response += f"\nID темы (message_thread_id): {thread_id}"

    update.message.reply_text(response)

def show_logs(update: Update, context: CallbackContext) -> None:
    username = update.message.from_user.username
    if username != user_roles["owner"] and username not in user_roles["head_admins"]:
        update.message.reply_text("❌ У вас нет прав для выполнения этой команды.")
        return  
    try:
        if len(context.args) == 1:
            try:
                single_date = datetime.strptime(context.args[0], "%d.%m.%Y")
                start_date = single_date
                end_date = single_date + timedelta(days=1)  
            except ValueError:
                update.message.reply_text("Неверный формат даты. Используйте: /show_logs 13.12.2024")
                return
        elif len(context.args) == 2:
            try:
                start_date = datetime.strptime(context.args[0], "%d.%m.%Y")
                end_date = datetime.strptime(context.args[1], "%d.%m.%Y") + timedelta(days=1)  
            except ValueError:
                update.message.reply_text("Неверный формат даты. Используйте: /show_logs 10.12.2024 13.12.2024")
                return
        else:
            update.message.reply_text("Укажите одну дату или период в формате: /show_logs 13.12.2024 или /show_logs 10.12.2024 13.12.2024")
            return

        start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%S')
        end_date_str = end_date.strftime('%Y-%m-%d %H:%M:%S')

        cursor.execute("""
            SELECT user_id, username, message_text, file_path, timestamp 
            FROM chat_logs 
            WHERE timestamp BETWEEN ? AND ?
            ORDER BY timestamp DESC
        """, (start_date_str, end_date_str))
        rows = cursor.fetchall()

        if not rows:
            update.message.reply_text("Нет доступных логов за указанный период.")
            return

        for row in rows:
            user_id, username, message_text, file_path, timestamp = row
            log_message = f"@{username} (ID: {user_id}) в {timestamp}:\n{message_text}"

            if file_path and os.path.exists(file_path):  
                update.message.reply_photo(photo=open(file_path, 'rb'), caption=log_message)
            else:
                update.message.reply_text(log_message)
    except Exception as e:
        update.message.reply_text(f"Произошла ошибка: {e}")

def clear_logs(update: Update, context: CallbackContext) -> None:
    username = update.message.from_user.username
    if username != user_roles["owner"] and username not in user_roles["head_admins"]:
        update.message.reply_text("❌ У вас нет прав для выполнения этой команды.")
        return  
    try:
        if context.args:
            try:
                num_logs = int(context.args[0])
            except ValueError:
                update.message.reply_text("Пожалуйста, укажите корректное число.")
                return
        else:
            update.message.reply_text("Укажите количество логов для удаления, например: /clear_logs 5")
            return

        cursor.execute("DELETE FROM chat_logs WHERE id IN (SELECT id FROM chat_logs ORDER BY id DESC LIMIT ?)", (num_logs,))
        conn.commit()

        update.message.reply_text(f"Удалено последних {num_logs} логов.")
        print(f"clear_logs: Удалено {num_logs} логов.")

    except Exception as e:
        print(f"Ошибка в clear_logs: {e}")
        update.message.reply_text("Произошла ошибка при очистке логов.")

def set_time_slot(update: Update, context: CallbackContext) -> None:
    sender_username = update.message.from_user.username
    sender_chat_id = update.message.chat.id  
    print(f"DEBUG: sender_chat_id из update.message.chat.id: {sender_chat_id}")

    if sender_username != user_roles["owner"] and sender_username not in user_roles["head_admins"]:
        update.message.reply_text("❌ У вас нет прав для выполнения этой команды.")
        return

    bot_instance = context.bot

    try:
        args = context.args
        if len(args) < 3:
            update.message.reply_text(
                "Использование: /set_time_slot <username> <start_time> <end_time>.\n"
                "Пример: /set_time_slot @user 07:30 08:30"
            )
            return

        target_username = args[0].lstrip("@")
        print(f"DEBUG: target_username: {target_username}")
        
        start_time_str = args[1]
        end_time_str = args[2]

        try:
            datetime.strptime(start_time_str, "%H:%M")
            datetime.strptime(end_time_str, "%H:%M")
        except ValueError:
            update.message.reply_text("Неверный формат времени. Используйте ЧЧ:ММ, например 07:30.")
            return

        kyiv_tz = pytz.timezone('Europe/Kyiv') # Укажите ваш часовой пояс
        now = datetime.now(kyiv_tz) # Укажите ваш часовой пояс

        start_time = kyiv_tz.localize(datetime.combine(now.date(), datetime.strptime(start_time_str, "%H:%M").time()))
        end_time = kyiv_tz.localize(datetime.combine(now.date(), datetime.strptime(end_time_str, "%H:%M").time()))

        if end_time <= start_time:
            end_time += timedelta(days=1)

        cursor.execute(""" 
            INSERT OR REPLACE INTO employee_time_slots (username, start_time, end_time, sender_chat_id, created_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (target_username, start_time_str, end_time_str, sender_chat_id))
        print(f"DEBUG: Данные для {target_username} успешно добавлены в базу данных.")
        conn.commit()
        cursor.execute("SELECT * FROM employee_time_slots WHERE username = ?", (target_username,))
        result = cursor.fetchone()
        if result:
            print(f"DEBUG: Данные успешно сохранены: {result}")
        else:
            print(f"DEBUG: Данные для {target_username} не найдены после INSERT.")

        user_time_slots[target_username] = {
            "start_time": start_time,
            "end_time": end_time
        }

        scheduler.add_job(
            schedule_user_check_with_entry,
            CronTrigger(hour=start_time.hour, minute=start_time.minute),
            kwargs={
                "target_username": target_username,
                "start_time": start_time,
                "end_time": end_time,
                "sender_chat_id": sender_chat_id,  
                "bot": bot_instance  
            },
            id=f"check_{target_username}",
            replace_existing=True
        )

        update.message.reply_text(
            f"Время для @{target_username} установлено:\n"
            f"Начало: {start_time_str}\n"
            f"Конец: {end_time_str}"
        )
        print(f"DEBUG: Планирование задачи: target_username={target_username}, sender_chat_id={sender_chat_id}")
        print(f"Задача для @{target_username} запланирована: {start_time} - {end_time}")

    except Exception as e:
        print(f"Ошибка в set_time_slot: {e}")
        update.message.reply_text("Произошла ошибка при установке времени.")

def check_time(update: Update, context: CallbackContext) -> None:
    username = update.message.from_user.username
    if username != user_roles["owner"] and username not in user_roles["head_admins"]:
        update.message.reply_text("❌ У вас нет прав для выполнения этой команды.")
        return  
    try:
        if not context.args:
            cursor.execute("""
                SELECT username, start_time, end_time, updated_at
                FROM employee_time_slots
                ORDER BY username
            """)
            time_slots = cursor.fetchall()

            if not time_slots:
                update.message.reply_text("Нет установленного времени ни для одного сотрудника.")
                return

            response = "Установленное время сотрудников:\n\n"
            for username, start_time, end_time, updated_at in time_slots:
                updated_at_str = updated_at if updated_at else "не обновлялось"
                response += (f"@{username}\n"
                            f"├ Время: {start_time} - {end_time}\n"
                            f"└ Обновлено: {updated_at_str}\n\n")

            update.message.reply_text(response)
        else:
            target_username = context.args[0].lstrip("@")
            
            cursor.execute("""
                SELECT username, start_time, end_time, updated_at
                FROM employee_time_slots
                WHERE username = ?
            """, (target_username,))
            
            result = cursor.fetchone()
            
            if result:
                username, start_time, end_time, updated_at = result
                updated_at_str = updated_at if updated_at else "не обновлялось"
                response = (f"Время для @{username}:\n"
                          f"├ Начало: {start_time}\n"
                          f"├ Конец: {end_time}\n"
                          f"└ Обновлено: {updated_at_str}")
                update.message.reply_text(response)
            else:
                update.message.reply_text(f"Для сотрудника @{target_username} не установлено время.")

    except Exception as e:
        print(f"Ошибка в check_time: {e}")
        update.message.reply_text("Произошла ошибка при проверке времени.")

def show_balance(update: Update, context: CallbackContext) -> None:
    username = update.message.from_user.username
    if username != user_roles["owner"] and username not in user_roles["head_admins"]:
        update.message.reply_text("❌ У вас нет прав для выполнения этой команды.")
        return  
    try:
        message = update.message
        if not message:
            print("show_balance: Нет сообщения.")
            return
        args = context.args
        username = None
        start_date = None
        end_date = None
        
        if args:
            if args[0].startswith("@"):
                username = args[0].lstrip("@")
                args = args[1:]  
            else:
                username = message.from_user.username or "Unknown"
                username = username.lstrip("@")
            
            if len(args) == 1:
                start_date = end_date = datetime.strptime(args[0], '%d.%m.%Y').date()
            elif len(args) == 2:
                start_date = datetime.strptime(args[0], '%d.%m.%Y').date()
                end_date = datetime.strptime(args[1], '%d.%m.%Y').date()
        else:
            username = message.from_user.username or "Unknown"
            username = username.lstrip("@")

        print(f"Запрос баланса для пользователя @{username} за период: {start_date} - {end_date}")

        cursor.execute("SELECT COUNT(*) FROM shift_totals WHERE username = ?", (username,))
        user_exists = cursor.fetchone()[0] > 0

        if not user_exists:
            message.reply_text(f"❌ Пользователь @{username} не найден в базе данных.")
            print(f"❌ Пользователь @{username} не найден.")
            return

        query = """
            SELECT entry_number, exit_number, DATE(entry_time)
            FROM shift_totals
            WHERE username = ? AND exit_number IS NOT NULL
        """
        params = [username]

        if start_date:
            query += " AND DATE(entry_time) >= ?"
            params.append(start_date)

        if end_date:
            query += " AND DATE(entry_time) <= ?"
            params.append(end_date)

        cursor.execute(query, tuple(params))
        shifts = cursor.fetchall()

        if not shifts:
            message.reply_text(f"❌ У пользователя @{username} нет завершенных смен за указанный период.")
            print(f"❌ У пользователя @{username} нет завершенных смен за выбранный период.")
            return

        balance = 0
        shift_details = []
        for entry_number, exit_number, shift_date in shifts:
            shift_balance = exit_number - entry_number
            balance += shift_balance
            shift_details.append(f"{shift_date}: {entry_number} ➡ {exit_number} = {shift_balance}")

        balance = round(balance, 2)
        payouts = round(balance * 0.2, 2) # Измените на вашу систему оплаты

        shift_details_text = "\n".join(shift_details)
        response_text = (
            f"💼 Расчет баланса для пользователя @{username} за период:\n"
            f"{shift_details_text}\n"
            f"\n💼 Итого баланс: {balance}\n"
            f"💵 Итоговые выплаты: {payouts}"
        )

        message.reply_text(response_text)
        print(f"✅ Баланс для @{username} отправлен.")

    except Exception as e:
        print(f"❌ Ошибка в show_balance: {e}")
        message.reply_text("❌ Произошла ошибка при расчете баланса.")
        import traceback
        traceback.print_exc()

def clear_balance(update: Update, context: CallbackContext) -> None:
    username = update.message.from_user.username
    if username != user_roles["owner"] and username not in user_roles["head_admins"]:
        update.message.reply_text("❌ У вас нет прав для выполнения этой команды.")
        return  
    
    try:
        message = update.message
        if not message:
            print("clear_balance: Нет сообщения.")
            return

        args = context.args
        username = None

        if args:
            if args[0].startswith("@"):
                username = args[0].lstrip("@")
            else:
                username = message.from_user.username or "Unknown"
                username = username.lstrip("@")
        else:
            username = message.from_user.username or "Unknown"
            username = username.lstrip("@")

        print(f"Сброс баланса для пользователя @{username}")

        cursor.execute("SELECT COUNT(*) FROM shift_totals WHERE username = ?", (username,))
        user_exists = cursor.fetchone()[0] > 0

        if not user_exists:
            message.reply_text(f"❌ Пользователь @{username} не найден в базе данных.")
            print(f"❌ Пользователь @{username} не найден.")
            return
        
        cursor.execute("DELETE FROM shift_totals WHERE username = ?", (username,))
        conn.commit()  

        message.reply_text(f"✅ Баланс пользователя @{username} успешно очищен.")
        print(f"✅ Баланс пользователя @{username} очищен.")

    except Exception as e:
        print(f"❌ Ошибка в clear_balance: {e}")
        message.reply_text("❌ Произошла ошибка при очистке баланса.")
        import traceback
        traceback.print_exc()

def del_time(update: Update, context: CallbackContext) -> None:
    try:
        username = update.message.from_user.username
        if username != user_roles["owner"] and username not in user_roles["head_admins"]:
            update.message.reply_text("❌ У вас нет прав для выполнения этой команды.")
            return

        if not context.args:
            update.message.reply_text(
                "Пожалуйста, укажите пользователя.\n"
                "Пример: /del_time @username"
            )
            return

        target_username = context.args[0].lstrip("@")

        cursor.execute("""
            DELETE FROM employee_time_slots
            WHERE username = ?
        """, (target_username,))
        
        if cursor.rowcount > 0:
            conn.commit()
            
            scheduler_job_id = f"check_{target_username}"
            try:
                scheduler.remove_job(scheduler_job_id)
                print(f"Задача {scheduler_job_id} удалена из планировщика")
            except Exception as e:
                print(f"Ошибка при удалении задачи из планировщика: {e}")
            
            if target_username in user_time_slots:
                del user_time_slots[target_username]
            update.message.reply_text(f"✅ Время для пользователя @{target_username} успешно удалено.")
        else:
            update.message.reply_text(f"❌ Время для пользователя @{target_username} не найдено или уже удалено.")

    except Exception as e:
        print(f"Ошибка в del_time: {e}")
        update.message.reply_text("Произошла ошибка при удалении времени.")

if __name__ == "__main__":
    main()
