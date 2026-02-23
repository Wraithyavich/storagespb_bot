import csv
import os
import re
from collections import defaultdict
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ---------- Получение токена из переменной окружения ----------
API_TOKEN = os.environ.get('API_TOKEN')
if API_TOKEN is None:
    raise ValueError("❌ Переменная окружения INVENTORY_BOT_TOKEN не задана!")

# ---------- Список разрешенных пользователей (user_id) ----------
ALLOWED_IDS_STR = os.environ.get('ALLOWED_IDS', '')
ALLOWED_IDS = set()
if ALLOWED_IDS_STR:
    try:
        ALLOWED_IDS = set(int(id.strip()) for id in ALLOWED_IDS_STR.split(',') if id.strip())
        print(f"✅ Загружено {len(ALLOWED_IDS)} разрешенных пользователей")
    except ValueError:
        print("⚠️ Ошибка парсинга ALLOWED_IDS, проверьте формат (числа через запятую)")

# ---------- Список администраторов (user_id) ----------
ADMIN_IDS_STR = os.environ.get('ADMIN_IDS', '')
ADMIN_IDS = set()
if ADMIN_IDS_STR:
    try:
        ADMIN_IDS = set(int(id.strip()) for id in ADMIN_IDS_STR.split(',') if id.strip())
        print(f"✅ Загружено {len(ADMIN_IDS)} администраторов")
    except ValueError:
        print("⚠️ Ошибка парсинга ADMIN_IDS, проверьте формат (числа через запятую)")

# ---------- Имена пользователей (для отображения в логах) ----------
USER_NAMES = {
    1219230738: "Савелий",
    526211024: "Ваня",
    1995599290: "Настя"
}

# ---------- Имя файла с данными ----------
DATA_FILE = 'inventory.csv'
LOG_FILE = 'last_changes.log'
MIN_SEARCH_LENGTH = 2

# ---------- Очистка текста ----------
def clean_text(s):
    s = s.strip()
    s = s.replace('\r', '').replace('\n', '').replace('\ufeff', '')
    return ' '.join(s.split())

def normalize_art(s):
    s = s.lower()
    s = re.sub(r'[^a-z0-9]', '', s)
    return s

# ---------- Загрузка данных из CSV ----------
inventory = {}
art_norm_to_original = {}
dop_norm_to_original = {}

try:
    with open(DATA_FILE, mode='r', encoding='utf-8-sig') as file:
        reader = csv.reader(file, delimiter=';')
        for row in reader:
            if len(row) >= 4:
                art = clean_text(row[0])
                dop = clean_text(row[1])
                try:
                    qty = int(clean_text(row[2]))
                except ValueError:
                    qty = 0
                price = clean_text(row[3])
                if art:
                    inventory[art] = [dop, qty, price]
                    art_norm_to_original[normalize_art(art)] = art
                    if dop:
                        dop_norm_to_original[normalize_art(dop)] = art
except FileNotFoundError:
    print(f"⚠️ Файл {DATA_FILE} не найден, будет создан при первом изменении.")
except Exception as e:
    print(f"❌ Ошибка загрузки: {e}")

print(f"✅ Загружено {len(inventory)} записей.")

# ---------- Сохранение данных в CSV ----------
def save_inventory():
    with open(DATA_FILE, mode='w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file, delimiter=';')
        for art, (dop, qty, price) in inventory.items():
            writer.writerow([art, dop, qty, price])

# ---------- Логирование изменений ----------
def log_change(user_id, action, art, delta, new_qty):
    """Записывает действие в лог-файл."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    name = USER_NAMES.get(user_id, str(user_id))
    log_line = f"[{timestamp}] {name} (ID:{user_id}) {action} {delta} к артикулу {art}, новый остаток: {new_qty}\n"
    with open(LOG_FILE, mode='a', encoding='utf-8') as f:
        f.write(log_line)

# ---------- Чтение последних N записей из лога ----------
def get_last_changes(n=5):
    try:
        with open(LOG_FILE, mode='r', encoding='utf-8') as f:
            lines = f.readlines()
        return lines[-n:] if lines else []
    except FileNotFoundError:
        return []

# ---------- Функции поиска ----------
def find_exact_original_art(query):
    norm_query = normalize_art(query)
    if norm_query in art_norm_to_original:
        return art_norm_to_original[norm_query]
    if norm_query in dop_norm_to_original:
        return dop_norm_to_original[norm_query]
    return None

def partial_search(query):
    norm_query = normalize_art(query)
    if len(norm_query) < MIN_SEARCH_LENGTH:
        return []
    results = set()
    for norm_art, orig_art in art_norm_to_original.items():
        if norm_query in norm_art:
            results.add(orig_art)
    for norm_dop, orig_art in dop_norm_to_original.items():
        if norm_query in norm_dop:
            results.add(orig_art)
    return sorted(results)

def format_item_info(art):
    dop, qty, price = inventory[art]
    return (
        f"🔍 Артикул: {art}\n"
        f"  📎 Доп. артикул: {dop}\n"
        f"  📦 Количество: {qty}\n"
        f"  💰 Цена: {price}"
    )

# ---------- Проверка доступа ----------
def is_allowed(user_id):
    return user_id in ALLOWED_IDS

# ---------- Обработчики команд ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ к боту запрещён.")
        return

    is_admin = user_id in ADMIN_IDS

    welcome_text = (
        "👋 Бот складского учёта.\n\n"
        "🔍 Просто отправьте артикул (основной или дополнительный), и я покажу информацию о нём.\n"
        f"Можно искать по части номера (минимум {MIN_SEARCH_LENGTH} символа).\n"
        "Регистр и разделители (дефисы, точки) не важны — я пойму.\n\n"
    )

    if is_admin:
        welcome_text += (
            "📦 У вас есть права администратора. Доступны команды:\n"
            "• добавить АРТИКУЛ, КОЛИЧЕСТВО — увеличить запас\n"
            "• убавить АРТИКУЛ, КОЛИЧЕСТВО — уменьшить запас\n\n"
            "Пример: добавить AC-K171eh, 5\n\n"
        )
    else:
        welcome_text += "⛔ Команды изменения количества доступны только администраторам.\n\n"

    welcome_text += "📋 Команда /last — показать последние 5 изменений (доступна всем)."

    await update.message.reply_text(welcome_text)

async def admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ к боту запрещён.")
        return
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ У вас нет прав на просмотр этой информации.")
        return

    if not ADMIN_IDS:
        await update.message.reply_text("Список администраторов пуст.")
        return

    lines = [f"• {uid}" for uid in sorted(ADMIN_IDS)]
    await update.message.reply_text("👤 Администраторы:\n" + "\n".join(lines))

async def last_changes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ к боту запрещён.")
        return

    lines = get_last_changes(5)
    if not lines:
        await update.message.reply_text("Пока нет записей об изменениях.")
    else:
        await update.message.reply_text("📋 Последние изменения:\n" + "".join(lines))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ к боту запрещён.")
        return

    text = clean_text(update.message.text)
    if not text:
        return

    match_cmd = re.match(r'^(добавить|убавить)\s+([^,]+?)\s*,\s*(\d+)$', text, re.IGNORECASE)
    if match_cmd:
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("⛔ У вас нет прав на изменение количества.")
            return

        command = match_cmd.group(1).lower()
        art_input = clean_text(match_cmd.group(2))
        try:
            delta = int(match_cmd.group(3))
        except ValueError:
            await update.message.reply_text("❌ Количество должно быть целым числом.")
            return

        if delta <= 0:
            await update.message.reply_text("❌ Количество должно быть положительным.")
            return

        original_art = find_exact_original_art(art_input)
        if original_art is None:
            candidates = partial_search(art_input)
            if not candidates:
                await update.message.reply_text(f"❌ Артикул '{art_input}' не найден.")
                return
            if len(candidates) == 1:
                original_art = candidates[0]
            else:
                lines = [format_item_info(art) for art in candidates]
                reply = "🔍 Найдено несколько артикулов:\n\n" + "\n\n".join(lines)
                await update.message.reply_text(reply)
                return

        dop, qty, price = inventory[original_art]

        if command == 'добавить':
            qty += delta
            action = "добавлено"
        else:
            if qty - delta < 0:
                await update.message.reply_text(
                    f"❌ Недостаточно запаса: текущее количество {qty}, невозможно убавить {delta}.")
                return
            qty -= delta
            action = "убавлено"

        inventory[original_art] = [dop, qty, price]

        try:
            save_inventory()
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка при сохранении: {e}")
            return

        # Логируем действие
        log_change(user_id, action, original_art, delta, qty)

        # Определяем имя для ответа
        actor_name = USER_NAMES.get(user_id, f"пользователь {user_id}")
        reply = (
            f"✅ {action.capitalize()} {delta} ед. для артикула {original_art}.\n"
            f"📦 Теперь количество: {qty}\n"
            f"💰 Цена за единицу: {price}\n"
            f"👤 Изменение внёс: {actor_name}"
        )
        await update.message.reply_text(reply)
        return

    # Обычный запрос артикула
    original_art = find_exact_original_art(text)
    if original_art is not None:
        dop, qty, price = inventory[original_art]
        reply = (
            f"🔍 Артикул: {original_art}\n"
            f"📎 Доп. артикул: {dop}\n"
            f"📦 Количество: {qty}\n"
            f"💰 Цена: {price}"
        )
    else:
        candidates = partial_search(text)
        if not candidates:
            await update.message.reply_text(f"❌ Артикул '{text}' не найден.")
            return
        lines = [format_item_info(art) for art in candidates]
        reply = "🔍 Найдено несколько артикулов:\n\n" + "\n\n".join(lines)

    await update.message.reply_text(reply)

def main():
    app = Application.builder().token(API_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admins", admins))
    app.add_handler(CommandHandler("last", last_changes))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 Бот складского учёта запущен...")
    print(f"🔒 Доступ разрешён для {len(ALLOWED_IDS)} пользователей.")
    print(f"🔑 Администраторов: {len(ADMIN_IDS)}")
    app.run_polling()

if __name__ == '__main__':
    main()
