import csv
import os
import re
import json
from collections import defaultdict
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# ---------- Получение токена из переменной окружения ----------
API_TOKEN = os.environ.get('API_TOKEN')
if API_TOKEN is None:
    raise ValueError("❌ Переменная окружения API_TOKEN не задана!")

# ---------- Список разрешенных пользователей ----------
ALLOWED_IDS_STR = os.environ.get('ALLOWED_IDS', '')
ALLOWED_IDS = set()
if ALLOWED_IDS_STR:
    try:
        ALLOWED_IDS = set(int(id.strip()) for id in ALLOWED_IDS_STR.split(',') if id.strip())
        print(f"✅ Загружено {len(ALLOWED_IDS)} разрешенных пользователей")
    except ValueError:
        print("⚠️ Ошибка парсинга ALLOWED_IDS")

# ---------- Список администраторов ----------
ADMIN_IDS_STR = os.environ.get('ADMIN_IDS', '')
ADMIN_IDS = set()
if ADMIN_IDS_STR:
    try:
        ADMIN_IDS = set(int(id.strip()) for id in ADMIN_IDS_STR.split(',') if id.strip())
        print(f"✅ Загружено {len(ADMIN_IDS)} администраторов")
    except ValueError:
        print("⚠️ Ошибка парсинга ADMIN_IDS")

# ---------- Имена пользователей ----------
USER_NAMES = {
    1219230738: "Савелий",
    526211024: "Ваня",
    1995599290: "Настя"
}

# ---------- Константы ----------
MIN_SEARCH_LENGTH = 2
DATA_FILE = 'inventory.csv'
LOG_FILE = 'last_changes.log'
RESERVES_FILE = 'reserves.json'
CATALOG_FILE = 'data.csv'

# ---------- Очистка текста ----------
def clean_text(s):
    s = s.strip()
    s = s.replace('\r', '').replace('\n', '').replace('\ufeff', '')
    return ' '.join(s.split())

def normalize_art(s):
    s = s.lower()
    s = re.sub(r'[^a-z0-9]', '', s)
    return s

# ---------- Загрузка складских данных (inventory.csv) ----------
inventory = {}
stock_norm_to_art = {}

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
                    stock_norm_to_art[normalize_art(art)] = art
except FileNotFoundError:
    print(f"⚠️ Файл {DATA_FILE} не найден, будет создан при первом изменении.")
except Exception as e:
    print(f"❌ Ошибка загрузки {DATA_FILE}: {e}")

print(f"✅ Загружено {len(inventory)} складских записей.")

# ---------- Загрузка каталога (data.csv) ----------
catalog = defaultdict(list)
catalog_norm_to_original = defaultdict(list)

try:
    with open(CATALOG_FILE, mode='r', encoding='utf-8-sig') as file:
        reader = csv.reader(file, delimiter=';')
        for row in reader:
            if len(row) >= 2:
                col1 = clean_text(row[0])
                col2 = clean_text(row[1])
                if col1:
                    catalog[col1].append(col2)
                    catalog_norm_to_original[normalize_art(col1)].append(col1)
                if col2:
                    catalog_norm_to_original[normalize_art(col2)].append(col1)
except FileNotFoundError:
    print(f"⚠️ Файл {CATALOG_FILE} не найден, поиск по каталогу недоступен.")
except Exception as e:
    print(f"❌ Ошибка загрузки {CATALOG_FILE}: {e}")

print(f"✅ Загружено {len(catalog)} записей в каталоге.")

# ---------- Загрузка резервов ----------
def load_reserves():
    try:
        with open(RESERVES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"⚠️ Ошибка загрузки резервов: {e}")
        return {}

def save_reserves(reserves):
    with open(RESERVES_FILE, 'w', encoding='utf-8') as f:
        json.dump(reserves, f, ensure_ascii=False, indent=2)

reserves = load_reserves()

# ---------- Сохранение складских данных ----------
def save_inventory():
    with open(DATA_FILE, mode='w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file, delimiter=';')
        for art, (dop, qty, price) in inventory.items():
            writer.writerow([art, dop, qty, price])

# ---------- Логирование изменений ----------
def log_change(user_id, action, art, delta, new_qty):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    name = USER_NAMES.get(user_id, str(user_id))
    log_line = f"[{timestamp}] {name} (ID:{user_id}) {action} {delta} к артикулу {art}, новый остаток: {new_qty}\n"
    with open(LOG_FILE, mode='a', encoding='utf-8') as f:
        f.write(log_line)

def get_last_changes(n=5):
    try:
        with open(LOG_FILE, mode='r', encoding='utf-8') as f:
            lines = f.readlines()
        return lines[-n:] if lines else []
    except FileNotFoundError:
        return []

# ---------- Функции поиска в каталоге ----------
def find_catalog_arts(query):
    norm_query = normalize_art(query)
    if len(norm_query) < MIN_SEARCH_LENGTH:
        return set()
    results = set()
    for norm_art, orig_arts in catalog_norm_to_original.items():
        if norm_query in norm_art:
            results.update(orig_arts)
    return results

def format_catalog_art(art):
    dop_list = catalog.get(art, [])
    dop_short = []
    for d in dop_list:
        if '-' in d:
            base = d.rsplit('-', 1)[0]
        else:
            base = d
        dop_short.append(base)
    unique_dop = sorted(set(dop_short))
    dop_str = ", ".join(unique_dop) if unique_dop else "нет"
    if art in inventory:
        _, qty, price = inventory[art]
        stock_info = f"📦 На складе: {qty} ед., цена: {price}"
    else:
        stock_info = "❌ На складе отсутствует"
    return f"🔍 Артикул: {art}\n📎 Доп. артикулы: {dop_str}\n{stock_info}"

# ---------- Проверка доступа ----------
def is_allowed(user_id):
    return user_id in ALLOWED_IDS

# ---------- Функции для создания клавиатур ----------
def get_main_keyboard(is_admin):
    keyboard = [
        [InlineKeyboardButton("🔍 Поиск по артикулу", callback_data="search")],
        [InlineKeyboardButton("📋 Резервы", callback_data="reserves"),
         InlineKeyboardButton("📜 Последние изменения", callback_data="last")]
    ]
    if is_admin:
        keyboard.append([InlineKeyboardButton("➕ Добавить", callback_data="add"),
                         InlineKeyboardButton("➖ Убавить", callback_data="remove")])
        keyboard.append([InlineKeyboardButton("🕒 Отложить", callback_data="reserve"),
                         InlineKeyboardButton("❌ Снять резерв", callback_data="unreserve")])
    return InlineKeyboardMarkup(keyboard)

def get_admin_actions_keyboard(art):
    keyboard = [
        [InlineKeyboardButton("➕ Добавить", callback_data=f"add_{art}"),
         InlineKeyboardButton("➖ Убавить", callback_data=f"remove_{art}")],
        [InlineKeyboardButton("🕒 Отложить", callback_data=f"reserve_{art}"),
         InlineKeyboardButton("❌ Снять резерв", callback_data=f"unreserve_{art}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard():
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
    return InlineKeyboardMarkup(keyboard)

# ---------- Обработчики ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ к боту запрещён.")
        return

    is_admin = user_id in ADMIN_IDS
    welcome_text = "👋 Добро пожаловать! Выберите действие:"
    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard(is_admin))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await query.edit_message_text("⛔ Доступ к боту запрещён.")
        return

    data = query.data
    is_admin = user_id in ADMIN_IDS

    if data == "back_to_main":
        await query.edit_message_text("👋 Выберите действие:", reply_markup=get_main_keyboard(is_admin))
        return

    if data == "search":
        await query.edit_message_text("🔍 Введите артикул или его часть:", reply_markup=get_back_keyboard())
        context.user_data['awaiting'] = 'search'
        return

    if data == "reserves":
        if not reserves:
            await query.edit_message_text("📭 Нет активных резервов.", reply_markup=get_back_keyboard())
        else:
            lines = []
            for art, res_list in reserves.items():
                for r in res_list:
                    lines.append(f"• {art} — {r['client']}: {r['qty']} ед.")
            await query.edit_message_text("📋 Текущие резервы:\n" + "\n".join(lines), reply_markup=get_back_keyboard())
        return

    if data == "last":
        lines = get_last_changes(5)
        if not lines:
            await query.edit_message_text("Пока нет записей об изменениях.", reply_markup=get_back_keyboard())
        else:
            await query.edit_message_text("📋 Последние изменения:\n" + "".join(lines), reply_markup=get_back_keyboard())
        return

    if is_admin:
        if data == "add":
            await query.edit_message_text("➕ Введите: добавить АРТИКУЛ, КОЛИЧЕСТВО", reply_markup=get_back_keyboard())
            context.user_data['awaiting'] = 'add'
            return
        if data == "remove":
            await query.edit_message_text("➖ Введите: убавить АРТИКУЛ, КОЛИЧЕСТВО", reply_markup=get_back_keyboard())
            context.user_data['awaiting'] = 'remove'
            return
        if data == "reserve":
            await query.edit_message_text("🕒 Введите: отложить АРТИКУЛ, КОЛИЧЕСТВО, КЛИЕНТ", reply_markup=get_back_keyboard())
            context.user_data['awaiting'] = 'reserve'
            return
        if data == "unreserve":
            await query.edit_message_text("❌ Введите: снять АРТИКУЛ, КЛИЕНТ [КОЛИЧЕСТВО]", reply_markup=get_back_keyboard())
            context.user_data['awaiting'] = 'unreserve'
            return

        # Обработка кнопок с артикулом (например, add_art)
        if data.startswith("add_"):
            art = data[4:]
            await query.edit_message_text(f"➕ Введите количество для добавления к {art}:", reply_markup=get_back_keyboard())
            context.user_data['awaiting'] = f"add_{art}"
            return
        if data.startswith("remove_"):
            art = data[7:]
            await query.edit_message_text(f"➖ Введите количество для убавления с {art}:", reply_markup=get_back_keyboard())
            context.user_data['awaiting'] = f"remove_{art}"
            return
        if data.startswith("reserve_"):
            art = data[8:]
            await query.edit_message_text(f"🕒 Введите: количество, клиент для {art} (через запятую)", reply_markup=get_back_keyboard())
            context.user_data['awaiting'] = f"reserve_{art}"
            return
        if data.startswith("unreserve_"):
            art = data[10:]
            await query.edit_message_text(f"❌ Введите: клиент [количество] для снятия с {art}", reply_markup=get_back_keyboard())
            context.user_data['awaiting'] = f"unreserve_{art}"
            return

    await query.edit_message_text("Неизвестная команда.", reply_markup=get_back_keyboard())

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ к боту запрещён.")
        return

    text = clean_text(update.message.text)
    if not text:
        return

    # Если ожидается ввод от кнопок
    awaiting = context.user_data.get('awaiting')
    if awaiting:
        del context.user_data['awaiting']
        # Обработка ожидаемых команд (аналогично предыдущей логике, но с учётом артикула из awaiting)
        if awaiting == 'search':
            # Поиск
            arts = find_catalog_arts(text)
            if not arts:
                await update.message.reply_text(f"❌ Артикул '{text}' не найден в каталоге.", reply_markup=get_back_keyboard())
                return
            sorted_arts = sorted(arts)
            total = len(sorted_arts)
            if total == 1:
                art = sorted_arts[0]
                reply = format_catalog_art(art)
                if user_id in ADMIN_IDS:
                    await update.message.reply_text(reply, reply_markup=get_admin_actions_keyboard(art))
                else:
                    await update.message.reply_text(reply, reply_markup=get_back_keyboard())
            else:
                MAX_DISPLAY = 10
                await update.message.reply_text(f"🔍 Найдено артикулов: {total}. Показываю первые {MAX_DISPLAY}:")
                shown = 0
                for art in sorted_arts:
                    if shown >= MAX_DISPLAY:
                        break
                    await update.message.reply_text(format_catalog_art(art))
                    shown += 1
                if total > MAX_DISPLAY:
                    await update.message.reply_text(f"... и ещё {total - MAX_DISPLAY} артикулов. Уточните запрос.", reply_markup=get_back_keyboard())
            return

        # Админские команды
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("⛔ У вас нет прав.")
            return

        # Добавить
        if awaiting == 'add':
            match = re.match(r'^добавить\s+([^,]+?)\s*,\s*(\d+)$', text, re.IGNORECASE)
            if not match:
                await update.message.reply_text("❌ Неверный формат. Используйте: добавить АРТИКУЛ, КОЛИЧЕСТВО", reply_markup=get_back_keyboard())
                return
            art_input = clean_text(match.group(1))
            try:
                delta = int(match.group(2))
            except ValueError:
                await update.message.reply_text("❌ Количество должно быть целым числом.", reply_markup=get_back_keyboard())
                return
            if delta <= 0:
                await update.message.reply_text("❌ Количество должно быть положительным.", reply_markup=get_back_keyboard())
                return
            # Поиск артикула
            norm_art = normalize_art(art_input)
            if norm_art in stock_norm_to_art:
                original_art = stock_norm_to_art[norm_art]
            else:
                candidates = [a for a in inventory if normalize_art(a) == norm_art]
                if not candidates:
                    await update.message.reply_text(f"❌ Артикул '{art_input}' не найден на складе.", reply_markup=get_back_keyboard())
                    return
                if len(candidates) == 1:
                    original_art = candidates[0]
                else:
                    lines = [format_catalog_art(art) for art in candidates]
                    await update.message.reply_text("🔍 Найдено несколько артикулов на складе:\n\n" + "\n\n".join(lines), reply_markup=get_back_keyboard())
                    return
            dop, qty, price = inventory[original_art]
            qty += delta
            inventory[original_art] = [dop, qty, price]
            save_inventory()
            log_change(user_id, "добавлено", original_art, delta, qty)
            art_reserves = reserves.get(original_art, [])
            total_reserved = sum(r['qty'] for r in art_reserves)
            available = qty - total_reserved
            actor_name = USER_NAMES.get(user_id, f"пользователь {user_id}")
            reply = f"✅ Добавлено {delta} ед. для артикула {original_art}.\n📦 Теперь количество: {qty} (доступно: {available}, зарезервировано: {total_reserved})\n💰 Цена за единицу: {price}\n👤 Изменение внёс: {actor_name}"
            await update.message.reply_text(reply, reply_markup=get_back_keyboard())
            return

        # Убавить (аналогично, можно реализовать по шаблону)
        # Для краткости оставлю как заглушку — нужно дописать все команды аналогично

        # Если awaiting содержит артикул (например, add_art)
        if awaiting.startswith('add_'):
            art = awaiting[4:]
            try:
                delta = int(text)
            except ValueError:
                await update.message.reply_text("❌ Количество должно быть целым числом.", reply_markup=get_back_keyboard())
                return
            if delta <= 0:
                await update.message.reply_text("❌ Количество должно быть положительным.", reply_markup=get_back_keyboard())
                return
            if art not in inventory:
                await update.message.reply_text(f"❌ Артикул '{art}' не найден на складе.", reply_markup=get_back_keyboard())
                return
            dop, qty, price = inventory[art]
            qty += delta
            inventory[art] = [dop, qty, price]
            save_inventory()
            log_change(user_id, "добавлено", art, delta, qty)
            art_reserves = reserves.get(art, [])
            total_reserved = sum(r['qty'] for r in art_reserves)
            available = qty - total_reserved
            actor_name = USER_NAMES.get(user_id, f"пользователь {user_id}")
            reply = f"✅ Добавлено {delta} ед. для артикула {art}.\n📦 Теперь количество: {qty} (доступно: {available}, зарезервировано: {total_reserved})\n💰 Цена за единицу: {price}\n👤 Изменение внёс: {actor_name}"
            await update.message.reply_text(reply, reply_markup=get_back_keyboard())
            return

        # Аналогично для remove_, reserve_, unreserve_

        # Если ничего не подошло, просто показываем кнопки
        await update.message.reply_text("Неизвестная команда.", reply_markup=get_back_keyboard())
        return

    # Если нет ожидания, обрабатываем как поиск
    arts = find_catalog_arts(text)
    if not arts:
        await update.message.reply_text(f"❌ Артикул '{text}' не найден в каталоге.", reply_markup=get_back_keyboard())
        return
    sorted_arts = sorted(arts)
    total = len(sorted_arts)
    if total == 1:
        art = sorted_arts[0]
        reply = format_catalog_art(art)
        if user_id in ADMIN_IDS:
            await update.message.reply_text(reply, reply_markup=get_admin_actions_keyboard(art))
        else:
            await update.message.reply_text(reply, reply_markup=get_back_keyboard())
    else:
        MAX_DISPLAY = 10
        await update.message.reply_text(f"🔍 Найдено артикулов: {total}. Показываю первые {MAX_DISPLAY}:")
        shown = 0
        for art in sorted_arts:
            if shown >= MAX_DISPLAY:
                break
            await update.message.reply_text(format_catalog_art(art))
            shown += 1
        if total > MAX_DISPLAY:
            await update.message.reply_text(f"... и ещё {total - MAX_DISPLAY} артикулов. Уточните запрос.", reply_markup=get_back_keyboard())

def main():
    app = Application.builder().token(API_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 Бот складского учёта и каталога с кнопками запущен...")
    print(f"🔒 Доступ разрешён для {len(ALLOWED_IDS)} пользователей.")
    print(f"🔑 Администраторов: {len(ADMIN_IDS)}")
    app.run_polling()

if __name__ == '__main__':
    main()
