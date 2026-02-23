import csv
import os
import re
import json
from collections import defaultdict
from datetime import datetime, timedelta
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

# ---------- Проверка доступа ----------
def is_allowed(user_id):
    return user_id in ALLOWED_IDS

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

# ---------- Загрузка складских данных ----------
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

# ---------- Загрузка каталога ----------
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

def get_last_changes(n=40):
    try:
        with open(LOG_FILE, mode='r', encoding='utf-8') as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []

    week_ago = datetime.now() - timedelta(days=7)
    filtered = []
    for line in lines:
        match = re.match(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\].*', line)
        if match:
            dt_str = match.group(1)
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            if dt >= week_ago:
                filtered.append(line)
        else:
            filtered.append(line)
    return filtered[-n:]

def clean_old_logs():
    try:
        with open(LOG_FILE, mode='r', encoding='utf-8') as f:
            lines = f.readlines()
    except FileNotFoundError:
        return
    week_ago = datetime.now() - timedelta(days=7)
    new_lines = []
    for line in lines:
        match = re.match(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\].*', line)
        if match:
            dt_str = match.group(1)
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            if dt >= week_ago:
                new_lines.append(line)
        else:
            new_lines.append(line)
    with open(LOG_FILE, mode='w', encoding='utf-8') as f:
        f.writelines(new_lines)

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

# ---------- Вспомогательные функции для резервов ----------
def get_all_clients():
    clients = set()
    for art, res_list in reserves.items():
        for r in res_list:
            clients.add(r['client'])
    return sorted(clients)

def get_client_articles(client):
    articles = []
    for art, res_list in reserves.items():
        for r in res_list:
            if r['client'].lower() == client.lower():
                articles.append(art)
    return sorted(set(articles))

def get_client_article_qty(client, art):
    for res in reserves.get(art, []):
        if res['client'].lower() == client.lower():
            return res['qty']
    return 0

def remove_client_reserves(client, art=None):
    """Удаляет резервы клиента. Если art указан, удаляет только для этого артикула, иначе все."""
    to_delete = []
    if art:
        if art in reserves:
            reserves[art] = [r for r in reserves[art] if r['client'].lower() != client.lower()]
            if not reserves[art]:
                del reserves[art]
    else:
        for a, res_list in list(reserves.items()):
            reserves[a] = [r for r in res_list if r['client'].lower() != client.lower()]
            if not reserves[a]:
                del reserves[a]
    save_reserves(reserves)

def remove_partial_reserve(client, art, qty_to_remove):
    """Снимает часть резерва клиента для конкретного артикула."""
    if art not in reserves:
        return False
    new_list = []
    removed = False
    for r in reserves[art]:
        if r['client'].lower() == client.lower():
            if r['qty'] > qty_to_remove:
                r['qty'] -= qty_to_remove
                new_list.append(r)
                removed = True
            elif r['qty'] == qty_to_remove:
                removed = True
                # не добавляем обратно, т.е. удаляем
            else:
                # не хватает – не должно случиться из-за проверки
                return False
        else:
            new_list.append(r)
    if removed:
        reserves[art] = new_list
        if not reserves[art]:
            del reserves[art]
        save_reserves(reserves)
        return True
    return False

# ---------- Клавиатуры ----------
def get_main_keyboard(is_admin):
    keyboard = [
        [InlineKeyboardButton("📋 Резервы", callback_data="reserves"),
         InlineKeyboardButton("📜 Последние изменения", callback_data="last")]
    ]
    if is_admin:
        keyboard.append([InlineKeyboardButton("➕ Добавить", callback_data="add"),
                         InlineKeyboardButton("➖ Убавить", callback_data="remove")])
        keyboard.append([InlineKeyboardButton("🕒 Отложить", callback_data="reserve"),
                         InlineKeyboardButton("❌ Снять резерв", callback_data="unreserve")])
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard():
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
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

# ---------- Обработчики ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ к боту запрещён.")
        return

    is_admin = user_id in ADMIN_IDS
    welcome_text = (
        "👋 Добро пожаловать в бот складского учёта!\n\n"
        "🔍 Просто отправьте артикул или его часть, и я покажу информацию из каталога и наличие на складе.\n"
        f"Минимум {MIN_SEARCH_LENGTH} символа.\n"
        "Регистр и разделители не важны.\n\n"
        "Используйте кнопки ниже для управления складом."
    )
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

    # Возврат в главное меню
    if data == "back_to_main":
        context.user_data.clear()
        await query.message.reply_text("👋 Выберите действие:", reply_markup=get_main_keyboard(is_admin))
        return

    # Другое количество (при резервировании)
    if data == "reserve_retry_qty":
        await query.message.delete()
        return

    # Добавление позиции в резерв
    if data == "reserve_add_another":
        await query.edit_message_text("🕒 Введите следующий артикул для резервирования:", reply_markup=get_back_keyboard())
        context.user_data['awaiting'] = 'reserve_art'
        return
    if data == "reserve_finish":
        items = context.user_data.get('reserve_items', [])
        client = context.user_data.get('reserve_client')
        if not client:
            await query.edit_message_text("🕒 Введите имя клиента для всех позиций:", reply_markup=get_back_keyboard())
            context.user_data['awaiting'] = 'reserve_client'
            return
        else:
            for art, qty, price in items:
                if art not in reserves:
                    reserves[art] = []
                reserves[art].append({"client": client, "qty": qty, "price": price})
            save_reserves(reserves)
            lines = [f"• {art} — {qty} ед. по цене {price}" for art, qty, price in items]
            await query.edit_message_text(f"✅ Резервы созданы для клиента '{client}':\n" + "\n".join(lines), reply_markup=get_back_keyboard())
            context.user_data.pop('awaiting', None)
            context.user_data.pop('reserve_items', None)
            context.user_data.pop('reserve_client', None)
        return

    # Просмотр резервов
    if data == "reserves":
        if not reserves:
            await query.edit_message_text("📭 Нет активных резервов.", reply_markup=get_back_keyboard())
        else:
            lines = []
            for art, res_list in reserves.items():
                for r in res_list:
                    price_str = r.get('price', 'не указана')
                    lines.append(f"• {r['client']} — {art} — {r['qty']} ед. — цена: {price_str}")
            await query.edit_message_text("📋 Текущие резервы:\n" + "\n".join(lines), reply_markup=get_back_keyboard())
        return

    # Последние изменения
    if data == "last":
        clean_old_logs()
        lines = get_last_changes(40)
        if not lines:
            await query.edit_message_text("Нет записей об изменениях за последние 7 дней.", reply_markup=get_back_keyboard())
        else:
            await query.edit_message_text("📋 Последние 40 изменений (за 7 дней):\n" + "".join(lines), reply_markup=get_back_keyboard())
        return

    # Проверка прав администратора для остальных команд
    if not is_admin:
        await query.edit_message_text("⛔ У вас нет прав на выполнение этой команды.", reply_markup=get_back_keyboard())
        return

    # Админские команды
    if data == "add":
        await query.edit_message_text("➕ Введите артикул для добавления:", reply_markup=get_back_keyboard())
        context.user_data['awaiting'] = 'add_art'
        return
    if data == "remove":
        await query.edit_message_text("➖ Введите артикул для убавления:", reply_markup=get_back_keyboard())
        context.user_data['awaiting'] = 'remove_art'
        return
    if data == "reserve":
        await query.edit_message_text("🕒 Введите артикул для резервирования:", reply_markup=get_back_keyboard())
        context.user_data['awaiting'] = 'reserve_art'
        context.user_data['reserve_items'] = []
        context.user_data['reserve_client'] = None
        return
    if data == "unreserve":
        # Шаг 1: выбор клиента
        clients = get_all_clients()
        if not clients:
            await query.edit_message_text("❌ Нет активных резервов.", reply_markup=get_back_keyboard())
            return
        # Сохраняем список клиентов в user_data для использования в callback'ах
        context.user_data['unreserve_clients'] = clients
        keyboard = []
        for idx, client in enumerate(clients):
            keyboard.append([InlineKeyboardButton(client, callback_data=f"unreserve_client_{idx}")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="back_to_main")])
        await query.edit_message_text("👤 Выберите клиента:", reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data['unreserve_step'] = 'select_client'
        return

    # Обработка выбора клиента для снятия резерва
    if data.startswith("unreserve_client_"):
        idx = int(data.split('_')[-1])
        clients = context.user_data.get('unreserve_clients', [])
        if idx < 0 or idx >= len(clients):
            await query.edit_message_text("❌ Ошибка выбора клиента.", reply_markup=get_back_keyboard())
            return
        client = clients[idx]
        context.user_data['unreserve_client'] = client
        articles = get_client_articles(client)
        if not articles:
            await query.edit_message_text(f"❌ У клиента {client} нет резервов.", reply_markup=get_back_keyboard())
            return
        context.user_data['unreserve_articles'] = articles
        keyboard = []
        for idx2, art in enumerate(articles):
            keyboard.append([InlineKeyboardButton(art, callback_data=f"unreserve_art_{idx2}")])
        keyboard.append([InlineKeyboardButton("✅ Все артикулы", callback_data="unreserve_all")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="unreserve_back_to_clients")])
        await query.edit_message_text(f"📦 Выберите артикул для клиента {client} (или все):", reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data['unreserve_step'] = 'select_article'
        return

    # Назад к выбору клиента
    if data == "unreserve_back_to_clients":
        clients = context.user_data.get('unreserve_clients', [])
        keyboard = []
        for idx, client in enumerate(clients):
            keyboard.append([InlineKeyboardButton(client, callback_data=f"unreserve_client_{idx}")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="back_to_main")])
        await query.edit_message_text("👤 Выберите клиента:", reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data['unreserve_step'] = 'select_client'
        return

    # Выбор конкретного артикула
    if data.startswith("unreserve_art_"):
        idx = int(data.split('_')[-1])
        articles = context.user_data.get('unreserve_articles', [])
        if idx < 0 or idx >= len(articles):
            await query.edit_message_text("❌ Ошибка выбора артикула.", reply_markup=get_back_keyboard())
            return
        art = articles[idx]
        client = context.user_data.get('unreserve_client')
        qty = get_client_article_qty(client, art)
        if qty == 1:
            # Снимаем сразу
            remove_partial_reserve(client, art, 1)
            await query.edit_message_text(f"✅ Резерв для клиента {client} по артикулу {art} (1 ед.) снят.", reply_markup=get_back_keyboard())
            context.user_data.pop('unreserve_step', None)
        else:
            # Запрашиваем количество
            await query.edit_message_text(f"📦 У клиента {client} зарезервировано {qty} ед. артикула {art}.\nВведите количество для снятия (или 'все'):", reply_markup=get_back_keyboard())
            context.user_data['unreserve_art'] = art
            context.user_data['unreserve_max_qty'] = qty
            context.user_data['awaiting'] = 'unreserve_input_qty'
        return

    # Выбор всех артикулов клиента
    if data == "unreserve_all":
        client = context.user_data.get('unreserve_client')
        if not client:
            await query.edit_message_text("❌ Ошибка: не выбран клиент.", reply_markup=get_back_keyboard())
            return
        remove_client_reserves(client)
        await query.edit_message_text(f"✅ Все резервы клиента {client} сняты.", reply_markup=get_back_keyboard())
        context.user_data.pop('unreserve_step', None)
        return

    # Кнопки с предопределённым артикулом (из админских действий)
    if data.startswith("add_"):
        art = data[4:]
        await query.edit_message_text(f"➕ Введите количество для добавления к {art}:", reply_markup=get_back_keyboard())
        context.user_data['awaiting'] = f"add_qty_{art}"
        return
    if data.startswith("remove_"):
        art = data[7:]
        await query.edit_message_text(f"➖ Введите количество для убавления с {art}:", reply_markup=get_back_keyboard())
        context.user_data['awaiting'] = f"remove_qty_{art}"
        return
    if data.startswith("reserve_"):
        art = data[8:]
        await query.edit_message_text(f"🕒 Введите количество для резервирования {art}:", reply_markup=get_back_keyboard())
        context.user_data['awaiting'] = f"reserve_qty_{art}"
        return
    if data.startswith("unreserve_"):
        art = data[10:]
        # Прямой переход к снятию с конкретного артикула (из админской кнопки)
        # Здесь можно сразу запросить клиента? Но по заданию мы хотим через выбор клиента. Для упрощения оставим старую логику или тоже через выбор клиента.
        # Пока оставим старую логику для совместимости.
        await query.edit_message_text(f"❌ Введите клиента для снятия резерва с {art} (или клиент, количество):", reply_markup=get_back_keyboard())
        context.user_data['awaiting'] = f"unreserve_data_{art}"
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

    awaiting = context.user_data.get('awaiting')
    if awaiting:
        await handle_dialog_input(update, context, text, awaiting)
        return

    # Поиск по каталогу
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
        lines = [format_catalog_art(art) for art in sorted_arts[:10]]
        full_message = "\n\n".join(lines)
        if total > 10:
            full_message += f"\n\n... и ещё {total-10} артикулов. Уточните запрос."
        await update.message.reply_text(full_message, reply_markup=get_back_keyboard())

async def handle_dialog_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, awaiting: str):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ У вас нет прав.", reply_markup=get_back_keyboard())
        context.user_data.pop('awaiting', None)
        return

    # ---------- Добавление ----------
    if awaiting == 'add_art':
        norm_art = normalize_art(text)
        if norm_art in stock_norm_to_art:
            art = stock_norm_to_art[norm_art]
        else:
            candidates = [a for a in inventory if normalize_art(a) == norm_art]
            if not candidates:
                await update.message.reply_text(f"❌ Артикул '{text}' не найден на складе.", reply_markup=get_back_keyboard())
                context.user_data.pop('awaiting', None)
                return
            if len(candidates) == 1:
                art = candidates[0]
            else:
                lines = [format_catalog_art(a) for a in candidates]
                await update.message.reply_text("🔍 Найдено несколько артикулов на складе:\n\n" + "\n\n".join(lines), reply_markup=get_back_keyboard())
                context.user_data.pop('awaiting', None)
                return
        context.user_data['add_art'] = art
        await update.message.reply_text(f"➕ Введите количество для добавления к {art}:", reply_markup=get_back_keyboard())
        context.user_data['awaiting'] = 'add_qty'
        return

    if awaiting == 'add_qty':
        try:
            delta = int(text)
        except ValueError:
            await update.message.reply_text("❌ Количество должно быть целым числом.", reply_markup=get_back_keyboard())
            return
        if delta <= 0:
            await update.message.reply_text("❌ Количество должно быть положительным.", reply_markup=get_back_keyboard())
            return
        art = context.user_data.get('add_art')
        if not art or art not in inventory:
            await update.message.reply_text("❌ Ошибка: артикул не найден. Начните заново.", reply_markup=get_back_keyboard())
            context.user_data.pop('awaiting', None)
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
        context.user_data.pop('awaiting', None)
        return

    # ---------- Убавление ----------
    if awaiting == 'remove_art':
        norm_art = normalize_art(text)
        if norm_art in stock_norm_to_art:
            art = stock_norm_to_art[norm_art]
        else:
            candidates = [a for a in inventory if normalize_art(a) == norm_art]
            if not candidates:
                await update.message.reply_text(f"❌ Артикул '{text}' не найден на складе.", reply_markup=get_back_keyboard())
                context.user_data.pop('awaiting', None)
                return
            if len(candidates) == 1:
                art = candidates[0]
            else:
                lines = [format_catalog_art(a) for a in candidates]
                await update.message.reply_text("🔍 Найдено несколько артикулов на складе:\n\n" + "\n\n".join(lines), reply_markup=get_back_keyboard())
                context.user_data.pop('awaiting', None)
                return
        context.user_data['remove_art'] = art
        await update.message.reply_text(f"➖ Введите количество для убавления с {art}:", reply_markup=get_back_keyboard())
        context.user_data['awaiting'] = 'remove_qty'
        return

    if awaiting == 'remove_qty':
        try:
            delta = int(text)
        except ValueError:
            await update.message.reply_text("❌ Количество должно быть целым числом.", reply_markup=get_back_keyboard())
            return
        if delta <= 0:
            await update.message.reply_text("❌ Количество должно быть положительным.", reply_markup=get_back_keyboard())
            return
        art = context.user_data.get('remove_art')
        if not art or art not in inventory:
            await update.message.reply_text("❌ Ошибка: артикул не найден. Начните заново.", reply_markup=get_back_keyboard())
            context.user_data.pop('awaiting', None)
            return
        dop, qty, price = inventory[art]
        if qty - delta < 0:
            await update.message.reply_text(f"❌ Недостаточно запаса: текущее количество {qty}, невозможно убавить {delta}.", reply_markup=get_back_keyboard())
            return
        qty -= delta
        inventory[art] = [dop, qty, price]
        save_inventory()
        log_change(user_id, "убавлено", art, delta, qty)
        art_reserves = reserves.get(art, [])
        total_reserved = sum(r['qty'] for r in art_reserves)
        available = qty - total_reserved
        actor_name = USER_NAMES.get(user_id, f"пользователь {user_id}")
        reply = f"✅ Убавлено {delta} ед. для артикула {art}.\n📦 Теперь количество: {qty} (доступно: {available}, зарезервировано: {total_reserved})\n💰 Цена за единицу: {price}\n👤 Изменение внёс: {actor_name}"
        await update.message.reply_text(reply, reply_markup=get_back_keyboard())
        context.user_data.pop('awaiting', None)
        return

    # ---------- Резервирование (многошаговое) ----------
    if awaiting == 'reserve_art':
        norm_art = normalize_art(text)
        if norm_art in stock_norm_to_art:
            art = stock_norm_to_art[norm_art]
        else:
            candidates = [a for a in inventory if normalize_art(a) == norm_art]
            if not candidates:
                await update.message.reply_text(f"❌ Артикул '{text}' не найден на складе.", reply_markup=get_back_keyboard())
                context.user_data.pop('awaiting', None)
                return
            if len(candidates) == 1:
                art = candidates[0]
            else:
                lines = [format_catalog_art(a) for a in candidates]
                await update.message.reply_text("🔍 Найдено несколько артикулов на складе:\n\n" + "\n\n".join(lines), reply_markup=get_back_keyboard())
                context.user_data.pop('awaiting', None)
                return
        context.user_data['reserve_current_art'] = art
        await update.message.reply_text(f"🕒 Введите количество для резервирования {art}:", reply_markup=get_back_keyboard())
        context.user_data['awaiting'] = 'reserve_qty'
        return

    if awaiting == 'reserve_qty':
        try:
            qty = int(text)
        except ValueError:
            await update.message.reply_text("❌ Количество должно быть целым числом.", reply_markup=get_back_keyboard())
            return
        if qty <= 0:
            await update.message.reply_text("❌ Количество должно быть положительным.", reply_markup=get_back_keyboard())
            return
        art = context.user_data.get('reserve_current_art')
        if not art or art not in inventory:
            await update.message.reply_text("❌ Ошибка: артикул не найден. Начните заново.", reply_markup=get_back_keyboard())
            context.user_data.pop('awaiting', None)
            return
        dop, current_qty, price = inventory[art]
        total_reserved = sum(r['qty'] for r in reserves.get(art, []))
        available = current_qty - total_reserved
        if qty > available:
            keyboard = [
                [InlineKeyboardButton("🔄 Другое количество", callback_data="reserve_retry_qty")],
                [InlineKeyboardButton("❌ Отмена", callback_data="back_to_main")]
            ]
            await update.message.reply_text(
                f"❌ Недостаточно свободного товара. Доступно: {available} (всего {current_qty}, зарезервировано {total_reserved}).\nВы можете ввести другое количество или отменить операцию.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        context.user_data['reserve_current_qty'] = qty
        current_price = inventory[art][2]
        if current_price and current_price != "0":
            price_info = f"Текущая цена в базе: {current_price}\n"
        else:
            price_info = ""
        await update.message.reply_text(f"{price_info}🕒 Введите цену за единицу для {art}:", reply_markup=get_back_keyboard())
        context.user_data['awaiting'] = 'reserve_price'
        return

    if awaiting == 'reserve_price':
        try:
            price = float(text.replace(',', '.'))
        except ValueError:
            await update.message.reply_text("❌ Цена должна быть числом (можно использовать точку или запятую).", reply_markup=get_back_keyboard())
            return
        art = context.user_data.get('reserve_current_art')
        qty = context.user_data.get('reserve_current_qty')
        if not art or not qty:
            await update.message.reply_text("❌ Ошибка данных. Начните заново.", reply_markup=get_back_keyboard())
            context.user_data.pop('awaiting', None)
            return
        items = context.user_data.get('reserve_items', [])
        items.append((art, qty, price))
        context.user_data['reserve_items'] = items
        keyboard = [
            [InlineKeyboardButton("✅ Да", callback_data="reserve_add_another"),
             InlineKeyboardButton("❌ Нет", callback_data="reserve_finish")]
        ]
        await update.message.reply_text("Позиция добавлена. Хотите добавить ещё одну для того же клиента?", reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data['awaiting'] = 'reserve_another'
        return

    if awaiting == 'reserve_client':
        client = text
        items = context.user_data.get('reserve_items', [])
        if not items:
            await update.message.reply_text("❌ Нет позиций для сохранения. Начните заново.", reply_markup=get_back_keyboard())
            context.user_data.pop('awaiting', None)
            return
        for art, qty, price in items:
            if art not in reserves:
                reserves[art] = []
            reserves[art].append({"client": client, "qty": qty, "price": price})
        save_reserves(reserves)
        lines = [f"• {art} — {qty} ед. по цене {price}" for art, qty, price in items]
        await update.message.reply_text(f"✅ Резервы созданы для клиента '{client}':\n" + "\n".join(lines), reply_markup=get_back_keyboard())
        context.user_data.pop('awaiting', None)
        context.user_data.pop('reserve_items', None)
        context.user_data.pop('reserve_client', None)
        return

    # ---------- Снятие резерва (ввод количества) ----------
    if awaiting == 'unreserve_input_qty':
        art = context.user_data.get('unreserve_art')
        client = context.user_data.get('unreserve_client')
        max_qty = context.user_data.get('unreserve_max_qty')
        if not art or not client or not max_qty:
            await update.message.reply_text("❌ Ошибка данных. Начните заново.", reply_markup=get_back_keyboard())
            context.user_data.pop('awaiting', None)
            return
        if text.lower() == 'все':
            qty_to_remove = max_qty
        else:
            try:
                qty_to_remove = int(text)
                if qty_to_remove <= 0:
                    await update.message.reply_text("❌ Количество должно быть положительным.", reply_markup=get_back_keyboard())
                    return
                if qty_to_remove > max_qty:
                    await update.message.reply_text(f"❌ Нельзя снять больше, чем зарезервировано ({max_qty}).", reply_markup=get_back_keyboard())
                    return
            except ValueError:
                await update.message.reply_text("❌ Введите целое число или 'все'.", reply_markup=get_back_keyboard())
                return
        if remove_partial_reserve(client, art, qty_to_remove):
            await update.message.reply_text(f"✅ Снято {qty_to_remove} ед. резерва клиента {client} по артикулу {art}.", reply_markup=get_back_keyboard())
        else:
            await update.message.reply_text("❌ Ошибка при снятии резерва.", reply_markup=get_back_keyboard())
        context.user_data.pop('awaiting', None)
        context.user_data.pop('unreserve_art', None)
        context.user_data.pop('unreserve_max_qty', None)
        return

    # Обработка старых команд (unreserve_data_art) для обратной совместимости
    if awaiting.startswith('unreserve_data_'):
        art = awaiting[14:]
        parts = [p.strip() for p in text.split(',')]
        if len(parts) == 1:
            client = parts[0]
            qty_to_remove = None
        elif len(parts) == 2:
            client = parts[0]
            try:
                qty_to_remove = int(parts[1])
                if qty_to_remove <= 0:
                    await update.message.reply_text("❌ Количество должно быть положительным.", reply_markup=get_back_keyboard())
                    return
            except ValueError:
                await update.message.reply_text("❌ Количество должно быть целым числом.", reply_markup=get_back_keyboard())
                return
        else:
            await update.message.reply_text("❌ Неверный формат. Используйте: клиент или клиент, количество", reply_markup=get_back_keyboard())
            return
        if art not in inventory:
            await update.message.reply_text(f"❌ Артикул '{art}' не найден на складе.", reply_markup=get_back_keyboard())
            return
        if art not in reserves or not reserves[art]:
            await update.message.reply_text(f"❌ По артикулу {art} нет резервов.", reply_markup=get_back_keyboard())
            return
        client_reserves = [r for r in reserves[art] if r['client'].lower() == client.lower()]
        if not client_reserves:
            await update.message.reply_text(f"❌ Для артикула {art} нет резерва для клиента '{client}'.", reply_markup=get_back_keyboard())
            return
        if qty_to_remove is None:
            removed_total = sum(r['qty'] for r in client_reserves)
            reserves[art] = [r for r in reserves[art] if r['client'].lower() != client.lower()]
            if not reserves[art]:
                del reserves[art]
            save_reserves(reserves)
            action_msg = f"✅ Снят весь резерв ({removed_total} ед.) для клиента '{client}' по артикулу {art}."
        else:
            if remove_partial_reserve(client, art, qty_to_remove):
                action_msg = f"✅ Снято {qty_to_remove} ед. из резерва для клиента '{client}' по артикулу {art}."
            else:
                await update.message.reply_text("❌ Не удалось снять резерв.", reply_markup=get_back_keyboard())
                return
        dop, current_qty, price = inventory[art]
        total_reserved = sum(r['qty'] for r in reserves.get(art, []))
        available = current_qty - total_reserved
        actor_name = USER_NAMES.get(user_id, f"пользователь {user_id}")
        reply = f"{action_msg}\n📦 Теперь по артикулу {art}: всего {current_qty}, доступно {available}, зарезервировано {total_reserved}\n👤 Действие выполнил: {actor_name}"
        await update.message.reply_text(reply, reply_markup=get_back_keyboard())
        context.user_data.pop('awaiting', None)
        return

    # Кнопки с предопределённым артикулом (add_qty_art, remove_qty_art, reserve_qty_art)
    if awaiting.startswith('add_qty_'):
        art = awaiting[8:]
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
        context.user_data.pop('awaiting', None)
        return

    if awaiting.startswith('remove_qty_'):
        art = awaiting[11:]
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
        if qty - delta < 0:
            await update.message.reply_text(f"❌ Недостаточно запаса: текущее количество {qty}, невозможно убавить {delta}.", reply_markup=get_back_keyboard())
            return
        qty -= delta
        inventory[art] = [dop, qty, price]
        save_inventory()
        log_change(user_id, "убавлено", art, delta, qty)
        art_reserves = reserves.get(art, [])
        total_reserved = sum(r['qty'] for r in art_reserves)
        available = qty - total_reserved
        actor_name = USER_NAMES.get(user_id, f"пользователь {user_id}")
        reply = f"✅ Убавлено {delta} ед. для артикула {art}.\n📦 Теперь количество: {qty} (доступно: {available}, зарезервировано: {total_reserved})\n💰 Цена за единицу: {price}\n👤 Изменение внёс: {actor_name}"
        await update.message.reply_text(reply, reply_markup=get_back_keyboard())
        context.user_data.pop('awaiting', None)
        return

    if awaiting.startswith('reserve_qty_'):
        art = awaiting[12:]
        try:
            qty = int(text)
        except ValueError:
            await update.message.reply_text("❌ Количество должно быть целым числом.", reply_markup=get_back_keyboard())
            return
        if qty <= 0:
            await update.message.reply_text("❌ Количество должно быть положительным.", reply_markup=get_back_keyboard())
            return
        if art not in inventory:
            await update.message.reply_text(f"❌ Артикул '{art}' не найден на складе.", reply_markup=get_back_keyboard())
            return
        dop, current_qty, price = inventory[art]
        total_reserved = sum(r['qty'] for r in reserves.get(art, []))
        available = current_qty - total_reserved
        if qty > available:
            await update.message.reply_text(f"❌ Недостаточно свободного товара. Доступно: {available} (всего {current_qty}, зарезервировано {total_reserved}).", reply_markup=get_back_keyboard())
            return
        context.user_data['reserve_current_art'] = art
        context.user_data['reserve_current_qty'] = qty
        current_price = inventory[art][2]
        if current_price and current_price != "0":
            price_info = f"Текущая цена в базе: {current_price}\n"
        else:
            price_info = ""
        await update.message.reply_text(f"{price_info}🕒 Введите цену за единицу для {art}:", reply_markup=get_back_keyboard())
        context.user_data['awaiting'] = 'reserve_price'
        return

    await update.message.reply_text("Неизвестная команда.", reply_markup=get_back_keyboard())

def main():
    clean_old_logs()
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
