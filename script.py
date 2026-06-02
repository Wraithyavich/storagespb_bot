import csv
import os
import re
from collections import defaultdict
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

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

# ---------- Проверка доступа ----------
def is_allowed(user_id):
    if not ALLOWED_IDS_STR:
        return True
    return user_id in ALLOWED_IDS

# ---------- Константы ----------
MIN_SEARCH_LENGTH = 4
DATA_FILE = 'data.csv'
JRONE_FILE = 'jronecross.csv'
OEM_FILE = 'oemcross.csv'
FLP_FILE = 'flp.csv'
INVENTORY_FILE = 'vz.csv'

WAREHOUSES = [
    {
        'code': 'vz',
        'name': os.environ.get('WAREHOUSE_1_NAME') or 'Вязовский',
        'file': os.environ.get('WAREHOUSE_1_FILE') or INVENTORY_FILE,
    },
    {
        'code': 'gar',
        'name': os.environ.get('WAREHOUSE_2_NAME') or 'Гаражная',
        'file': os.environ.get('WAREHOUSE_2_FILE') or 'gar.csv',
    },
    {
        'code': 'per',
        'name': os.environ.get('WAREHOUSE_3_NAME') or 'Перовская',
        'file': os.environ.get('WAREHOUSE_3_FILE') or 'per.csv',
    },
    {
        'code': 'spb',
        'name': os.environ.get('WAREHOUSE_4_NAME') or 'СПБ',
        'file': os.environ.get('WAREHOUSE_4_FILE') or 'spb.csv',
    },
]

MARGIN_OPTIONS = [20, 25, 30, 35, 40, 45, 50]
DEFAULT_MARGIN = 50

# ---------- Очистка текста ----------
def clean_text(s):
    s = s.strip()
    s = s.replace('\r', '').replace('\n', '').replace('\ufeff', '')
    return ' '.join(s.split())

# ---------- Замена кириллических букв, похожих на латиницу ----------
CYRILLIC_TO_LATIN = {
    'А': 'A', 'а': 'a',
    'В': 'B', 'в': 'b',
    'Е': 'E', 'е': 'e',
    'К': 'K', 'к': 'k',
    'М': 'M', 'м': 'm',
    'Н': 'H', 'н': 'h',
    'О': 'O', 'о': 'o',
    'Р': 'P', 'р': 'p',
    'С': 'C', 'с': 'c',
    'Т': 'T', 'т': 't',
    'У': 'Y', 'у': 'y',
    'Х': 'X', 'х': 'x',
}

def replace_cyrillic_like_latin(s):
    return ''.join(CYRILLIC_TO_LATIN.get(ch, ch) for ch in s)

def normalize(s):
    s = replace_cyrillic_like_latin(s)
    return s.replace('-', '').lower()

def is_11_digit_number(s):
    return re.fullmatch(r'\d{11}', s) is not None

# ---------- Работа с ценами ----------
def parse_price(price_str):
    if not price_str:
        return 0.0
    cleaned = price_str.replace(' ', '').replace(',', '.')
    try:
        return float(cleaned)
    except ValueError:
        return 0.0

def format_price(price):
    rounded = round(price, 2)
    s = f"{rounded:.2f}".replace('.', ',')
    parts = s.split(',')
    int_part = parts[0]
    frac_part = parts[1] if len(parts) > 1 else '00'
    int_part_with_spaces = ''
    for i, digit in enumerate(reversed(int_part)):
        if i > 0 and i % 3 == 0:
            int_part_with_spaces = ' ' + int_part_with_spaces
        int_part_with_spaces = digit + int_part_with_spaces
    return f"{int_part_with_spaces},{frac_part}"

# ---------- Загрузка основной базы (data.csv) ----------
dict_by_col1 = defaultdict(list)
dict_by_col2 = defaultdict(list)
col1_norm_to_original = defaultdict(list)
col2_norm_to_original = defaultdict(list)

try:
    with open(DATA_FILE, mode='r', encoding='utf-8-sig') as file:
        reader = csv.reader(file, delimiter=';')
        for row in reader:
            if len(row) >= 2:
                col1 = clean_text(row[0])
                col2 = clean_text(row[1])
                if col1 and col2:
                    dict_by_col1[col1].append(col2)
                    dict_by_col2[col2].append(col1)
                    col1_norm_to_original[normalize(col1)].append(col1)
                    col2_norm_to_original[normalize(col2)].append(col2)
except FileNotFoundError:
    print("❌ Файл data.csv не найден! Поместите его в папку со скриптом.")
    exit(1)

print(f"✅ Основная база: {len(dict_by_col1)} Turbo P/N, {len(dict_by_col2)} E&E P/N.")

# ---------- Загрузка JRN-кроссов (jronecross.csv) ----------
jrone_norm_to_art = defaultdict(set)

try:
    with open(JRONE_FILE, mode='r', encoding='utf-8-sig') as file:
        reader = csv.reader(file, delimiter=';')
        for row in reader:
            if len(row) >= 3:
                jrone = clean_text(row[0])
                our_art = clean_text(row[2])
                if jrone and our_art:
                    norm = normalize(jrone)
                    jrone_norm_to_art[norm].add(our_art)
except FileNotFoundError:
    print("⚠️ Файл jronecross.csv не найден, поиск по JRN-номерам недоступен.")
except Exception as e:
    print(f"❌ Ошибка загрузки {JRONE_FILE}: {e}")

print(f"✅ JRN-база: {len(jrone_norm_to_art)} уникальных нормализованных JRN-номеров.")

# ---------- Загрузка OEM-кроссов (oemcross.csv) ----------
oem_norm_to_art = defaultdict(set)

try:
    with open(OEM_FILE, mode='r', encoding='utf-8-sig') as file:
        reader = csv.reader(file, delimiter=';')
        for row in reader:
            if len(row) >= 2:
                art = clean_text(row[0])
                oem = clean_text(row[1])
                if art and oem:
                    norm = normalize(oem)
                    oem_norm_to_art[norm].add(art)
except FileNotFoundError:
    print("⚠️ Файл oemcross.csv не найден, поиск по OEM-номерам недоступен.")
except Exception as e:
    print(f"❌ Ошибка загрузки {OEM_FILE}: {e}")

print(f"✅ OEM-база: {len(oem_norm_to_art)} уникальных нормализованных OEM-номеров.")

# ---------- Загрузка FLP-кроссов (flp.csv) ----------
flp_norm_to_art = defaultdict(set)
art_norm_to_flp = defaultdict(set)

try:
    with open(FLP_FILE, mode='r', encoding='utf-8-sig') as file:
        reader = csv.reader(file, delimiter=';')
        for row in reader:
            if len(row) >= 2:
                art = clean_text(row[0])
                flp = clean_text(row[1])
                if art and flp:
                    norm_flp = normalize(flp)
                    norm_art = normalize(art)
                    flp_norm_to_art[norm_flp].add(art)
                    art_norm_to_flp[norm_art].add(flp)
except FileNotFoundError:
    print("⚠️ Файл flp.csv не найден, поиск по FLP-номерам недоступен.")
except Exception as e:
    print(f"❌ Ошибка загрузки {FLP_FILE}: {e}")

print(f"✅ FLP-база: {len(flp_norm_to_art)} уникальных FLP-номеров, {len(art_norm_to_flp)} уникальных артикулов.")

# ---------- Загрузка складских баз ----------
loaded_warehouses = []
warehouse_inventories = {}
warehouse_norm_to_arts = {}
stock_norm_to_arts = defaultdict(set)

def add_stock_index(warehouse_code, raw_value, art):
    norm_value = normalize(raw_value)
    if not norm_value:
        return
    stock_norm_to_arts[norm_value].add(art)
    warehouse_norm_to_arts[warehouse_code][norm_value].add(art)

def load_inventory_file(warehouse):
    warehouse_code = warehouse['code']
    file_name = warehouse['file']
    inventory_data = {}
    warehouse_norm_to_arts[warehouse_code] = defaultdict(set)

    try:
        with open(file_name, mode='r', encoding='utf-8-sig') as file:
            reader = csv.reader(file, delimiter=';')
            for row in reader:
                # Формат: артикул;доп. артикул;количество;цена;флаг скидки
                if len(row) < 4:
                    continue
                art = clean_text(row[0])
                if not art:
                    continue
                dop = clean_text(row[1]) if len(row) > 1 else ''
                try:
                    qty = int(clean_text(row[2]))
                except ValueError:
                    qty = 0
                price_str = clean_text(row[3])
                discount = True
                if len(row) >= 5 and clean_text(row[4]) == "1":
                    discount = False

                inventory_data[art] = {
                    'dop': dop,
                    'qty': qty,
                    'price': price_str,
                    'discount': discount,
                }
                add_stock_index(warehouse_code, art, art)
                if dop:
                    add_stock_index(warehouse_code, dop, art)
    except FileNotFoundError:
        print(f"⚠️ Файл склада {warehouse_code} ({file_name}) не найден, склад пропущен.")
        return
    except Exception as e:
        print(f"❌ Ошибка загрузки склада {warehouse_code} ({file_name}): {e}")
        return

    warehouse_inventories[warehouse_code] = inventory_data
    loaded_warehouses.append(warehouse)
    print(f"✅ {warehouse_code} ({warehouse['name']}): {len(inventory_data)} записей из {file_name}.")
    print(f"Примеры артикулов {warehouse_code}:", list(inventory_data.keys())[:5])

for warehouse in WAREHOUSES:
    load_inventory_file(warehouse)

if not loaded_warehouses:
    print("⚠️ Складские файлы не загружены, информация о наличии будет недоступна.")

# ---------- Частичный поиск в основной базе ----------
def partial_search_main(search_norm):
    results = set()
    for norm_key, original_keys in col1_norm_to_original.items():
        if search_norm in norm_key:
            for orig_key in original_keys:
                for val in dict_by_col1[orig_key]:
                    results.add(val)
    for norm_key, original_keys in col2_norm_to_original.items():
        if search_norm in norm_key:
            for orig_key in original_keys:
                for val in dict_by_col2[orig_key]:
                    results.add(val)
    return results

# ---------- Форматирование артикула с учётом складов и наценки ----------
def get_price_with_margin(price_str, margin):
    original_price = parse_price(price_str)
    if original_price == 0:
        return None
    base_price = original_price / 1.5
    return base_price * (1 + margin / 100.0)

def get_stock_for_art(warehouse, art):
    warehouse_code = warehouse['code']
    inventory_data = warehouse_inventories.get(warehouse_code, {})
    stock = inventory_data.get(art)
    if stock:
        return stock

    norm_art = normalize(art)
    candidates = warehouse_norm_to_arts.get(warehouse_code, {}).get(norm_art, set())
    for candidate in sorted(candidates):
        stock = inventory_data.get(candidate)
        if stock:
            return stock
    return None

def get_art_stocks(art):
    return [
        (warehouse, get_stock_for_art(warehouse, art))
        for warehouse in loaded_warehouses
    ]

def get_art_price_text(stocks, margin):
    prices = []
    discount = False
    for _, stock in stocks:
        if not stock:
            continue
        price_value = get_price_with_margin(stock['price'], margin)
        if price_value is not None:
            prices.append(price_value)
        if stock['discount']:
            discount = True

    if not prices:
        return ""

    price_text = f"цена: {format_price(min(prices))}"
    if discount:
        price_text += " (скидка)"
    return price_text

def format_stock_for_warehouse(warehouse, stock):
    return f"{warehouse['name']}: {stock['qty']}"

def get_art_stock_sort_key(art, margin=DEFAULT_MARGIN):
    stocks = [get_stock_for_art(warehouse, art) for warehouse in loaded_warehouses]
    has_positive_qty = any(stock and stock['qty'] > 0 for stock in stocks)
    has_any_stock = any(stock is not None for stock in stocks)
    price_values = []
    for stock in stocks:
        if not stock:
            continue
        price_value = get_price_with_margin(stock['price'], margin)
        if price_value is not None:
            price_values.append(price_value)
    best_price = min(price_values) if price_values else float('inf')
    availability_rank = 0 if has_positive_qty else 1 if has_any_stock else 2
    return (availability_rank, best_price, normalize(art))

def format_art_with_stock(art, links=None, margin=DEFAULT_MARGIN, label=None):
    display_art = label or art
    if loaded_warehouses:
        stocks = get_art_stocks(art)
        found_stocks = [
            (warehouse, stock)
            for warehouse, stock in stocks
            if stock
        ]
        if found_stocks:
            price_part = get_art_price_text(found_stocks, margin)
            qty_part = "; ".join(
                format_stock_for_warehouse(warehouse, stock)
                for warehouse, stock in found_stocks
            )
            stock_part = f"{price_part} | {qty_part}" if price_part else qty_part
        else:
            stock_part = "отсутствует"
    else:
        stock_part = "склады не загружены"

    links_part = f" → {', '.join(links)}" if links else ""
    return f"• {display_art} — {stock_part}{links_part}"

# ---------- Клавиатура выбора наценки ----------
def get_margin_keyboard():
    buttons = []
    row = []
    for i, margin in enumerate(MARGIN_OPTIONS, 1):
        row.append(KeyboardButton(f"{margin}%"))
        if i % 4 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([KeyboardButton("Текущая наценка")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_warehouse_codes_text():
    warehouses = loaded_warehouses or WAREHOUSES
    return ", ".join(warehouse['name'] for warehouse in warehouses)

# ---------- Функция безопасной отправки длинных сообщений ----------
async def safe_send(update: Update, text: str, reply_markup=None, parse_mode=None, max_len=4000):
    if len(text) <= max_len:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return

    lines = text.split('\n')
    parts = []
    current = ''

    for line in lines:
        if len(line) > max_len:
            if current:
                parts.append(current)
                current = ''
            for i in range(0, len(line), max_len):
                parts.append(line[i:i+max_len])
            continue

        if current:
            candidate = current + '\n' + line
        else:
            candidate = line

        if len(candidate) <= max_len:
            current = candidate
        else:
            parts.append(current)
            current = line

    if current:
        parts.append(current)

    for i, part in enumerate(parts):
        if i == len(parts) - 1:
            await update.message.reply_text(part, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await update.message.reply_text(part, parse_mode=parse_mode)

# ---------- Обработчики ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ к боту запрещён.")
        return

    context.user_data['margin'] = DEFAULT_MARGIN

    welcome_text = (
        "🔎 Поиск запчастей по складам\n\n"
        "Введите E&E / Turbo / JRN / OEM / FLP номер.\n"
        f"Можно часть номера от {MIN_SEARCH_LENGTH} символов; дефисы не важны.\n\n"
        "Пример: CT-VNT11B или 17201-52010\n"
        f"Склады: {get_warehouse_codes_text()}\n"
        f"Наценка сейчас: {DEFAULT_MARGIN}%"
    )
    await safe_send(update, welcome_text, reply_markup=get_margin_keyboard())

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await safe_send(update, "⛔ Доступ к боту запрещён.")
        return

    user_input = clean_text(update.message.text)
    if not user_input:
        return

    # Обработка кнопок наценки
    if user_input.endswith('%') and user_input[:-1].isdigit():
        margin = int(user_input[:-1])
        if margin in MARGIN_OPTIONS:
            context.user_data['margin'] = margin
            await safe_send(update, f"✅ Установлена наценка {margin}%", reply_markup=get_margin_keyboard())
            return
        else:
            await safe_send(update, "❌ Неверное значение. Используйте кнопки.", reply_markup=get_margin_keyboard())
            return
    elif user_input == "Текущая наценка":
        current = context.user_data.get('margin', DEFAULT_MARGIN)
        await safe_send(update, f"📊 Текущая наценка: {current}%", reply_markup=get_margin_keyboard())
        return

    margin = context.user_data.get('margin', DEFAULT_MARGIN)
    user_input_norm = normalize(user_input)
    input_len = len(user_input_norm)

    # Сбор результатов
    main_arts = set()
    jrone_arts = set()
    oem_arts = set()
    flp_arts = set()
    flp_nums = set()
    inventory_arts = set()

    # Основная база
    if input_len < MIN_SEARCH_LENGTH:
        if user_input_norm in col2_norm_to_original:
            for key in col2_norm_to_original[user_input_norm]:
                for val in dict_by_col2[key]:
                    main_arts.add(val)
        elif user_input_norm in col1_norm_to_original:
            for key in col1_norm_to_original[user_input_norm]:
                for val in dict_by_col1[key]:
                    main_arts.add(val)
    else:
        main_arts = partial_search_main(user_input_norm)
        if not main_arts and is_11_digit_number(user_input_norm):
            first4 = user_input_norm[:4]
            middle3 = user_input_norm[4:7]
            last4 = user_input_norm[7:]
            if middle3 != '970':
                new_norm = first4 + '970' + last4
                main_arts = partial_search_main(new_norm)

    # JRN
    if input_len < MIN_SEARCH_LENGTH:
        if user_input_norm in jrone_norm_to_art:
            jrone_arts = jrone_norm_to_art[user_input_norm]
    else:
        for norm_key, arts in jrone_norm_to_art.items():
            if user_input_norm in norm_key:
                jrone_arts.update(arts)

    # OEM
    if input_len < MIN_SEARCH_LENGTH:
        if user_input_norm in oem_norm_to_art:
            oem_arts = oem_norm_to_art[user_input_norm]
    else:
        for norm_key, arts in oem_norm_to_art.items():
            if user_input_norm in norm_key:
                oem_arts.update(arts)

    # FLP
    if input_len < MIN_SEARCH_LENGTH:
        if user_input_norm in flp_norm_to_art:
            flp_arts = flp_norm_to_art[user_input_norm]
    else:
        for norm_key, arts in flp_norm_to_art.items():
            if user_input_norm in norm_key:
                flp_arts.update(arts)

    if input_len < MIN_SEARCH_LENGTH:
        if user_input_norm in art_norm_to_flp:
            flp_nums = art_norm_to_flp[user_input_norm]
    else:
        for norm_key, nums in art_norm_to_flp.items():
            if user_input_norm in norm_key:
                flp_nums.update(nums)

    # Склады
    if input_len < MIN_SEARCH_LENGTH:
        inventory_arts.update(stock_norm_to_arts.get(user_input_norm, set()))
    else:
        for norm_art, arts in stock_norm_to_arts.items():
            if user_input_norm in norm_art:
                inventory_arts.update(arts)

    # Формирование строк ответа
    answer_lines = []

    for art in sorted(main_arts, key=lambda item: get_art_stock_sort_key(item, margin)):
        answer_lines.append(format_art_with_stock(art, margin=margin))

    for art in sorted(jrone_arts, key=lambda item: get_art_stock_sort_key(item, margin)):
        links = set()
        if art in dict_by_col1:
            links.update(dict_by_col1[art])
        if art in dict_by_col2:
            links.update(dict_by_col2[art])
        answer_lines.append(format_art_with_stock(art, links=sorted(links), margin=margin))

    for art in sorted(oem_arts, key=lambda item: get_art_stock_sort_key(item, margin)):
        answer_lines.append(format_art_with_stock(art, margin=margin))

    for art in sorted(flp_arts, key=lambda item: get_art_stock_sort_key(item, margin)):
        answer_lines.append(format_art_with_stock(art, margin=margin, label=f"FLP {art}"))

    for num in sorted(flp_nums):
        answer_lines.append(f"• FLP номер: {num}")

    shown_arts = set(main_arts) | set(jrone_arts) | set(oem_arts) | set(flp_arts)
    for art in sorted(inventory_arts, key=lambda item: get_art_stock_sort_key(item, margin)):
        if art not in shown_arts:
            answer_lines.append(format_art_with_stock(art, margin=margin))

    if not answer_lines:
        await safe_send(update, f"❌ Ничего не найдено по запросу: {user_input}", reply_markup=get_margin_keyboard())
        return

    full_text = f"🔎 Найдено: {len(answer_lines)} • наценка {margin}%\n" + "\n".join(answer_lines)
    await safe_send(update, full_text, reply_markup=get_margin_keyboard())

def main():
    app = Application.builder().token(API_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 Бот поиска запчастей по складам с регулировкой наценки запущен...")
    if ALLOWED_IDS_STR:
        print(f"🔒 Доступ разрешён для {len(ALLOWED_IDS)} пользователей.")
    else:
        print("🔓 Доступ разрешён для всех (ALLOWED_IDS не задана).")
    app.run_polling()

if __name__ == '__main__':
    main()
