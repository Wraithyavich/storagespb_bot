import csv
import os
import re
import json
from collections import defaultdict
from datetime import datetime
from telegram import Update
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
CATALOG_FILE = 'data.csv'  # файл первого бота (артикулы и доп. артикулы)

# ---------- Очистка текста ----------
def clean_text(s):
    s = s.strip()
    s = s.replace('\r', '').replace('\n', '').replace('\ufeff', '')
    return ' '.join(s.split())

def normalize_art(s):
    """Убирает всё кроме букв и цифр, нижний регистр"""
    s = s.lower()
    s = re.sub(r'[^a-z0-9]', '', s)
    return s

# ---------- Загрузка складских данных (inventory.csv) ----------
inventory = {}          # артикул -> [доп_артикул, количество, цена]
stock_norm_to_art = {}  # нормализованный артикул -> оригинальный артикул (для склада)

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
catalog = defaultdict(list)      # ключ (оригинальный артикул) -> список доп. артикулов (из второго столбца)
catalog_norm_to_original = defaultdict(list)  # нормализованный артикул (из первого или второго столбца) -> оригинальный артикул

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
    """Возвращает множество оригинальных артикулов из каталога, соответствующих запросу (частичное совпадение)"""
    norm_query = normalize_art(query)
    if len(norm_query) < MIN_SEARCH_LENGTH:
        return set()
    results = set()
    for norm_art, orig_arts in catalog_norm_to_original.items():
        if norm_query in norm_art:
            results.update(orig_arts)
    return results

def format_catalog_art(art):
    """Форматирует информацию об артикуле из каталога с привязкой к складу"""
    dop_list = catalog.get(art, [])
    dop_str = ", ".join(dop_list) if dop_list else "нет"
    # Проверяем наличие на складе
    if art in inventory:
        _, qty, price = inventory[art]
        stock_info = f"📦 На складе: {qty} ед., цена: {price}"
    else:
        stock_info = "❌ На складе отсутствует"
    return f"🔍 Артикул: {art}\n📎 Доп. артикулы: {dop_str}\n{stock_info}"

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
        "👋 Бот складского учёта и каталога.\n\n"
        "🔍 Просто отправьте артикул или его часть, и я покажу информацию из каталога и наличие на складе.\n"
        f"Минимум {MIN_SEARCH_LENGTH} символа для частичного поиска.\n"
        "Регистр и разделители не важны.\n\n"
    )

    if is_admin:
        welcome_text += (
            "📦 У вас есть права администратора. Доступны команды:\n"
            "• добавить АРТИКУЛ, КОЛИЧЕСТВО — увеличить запас\n"
            "• убавить АРТИКУЛ, КОЛИЧЕСТВО — уменьшить запас\n"
            "• отложить АРТИКУЛ, КОЛИЧЕСТВО, КЛИЕНТ — зарезервировать товар за клиентом\n"
            "• снять АРТИКУЛ, КЛИЕНТ [КОЛИЧЕСТВО] — снять резерв\n\n"
            "Примеры:\n"
            "добавить AC-K171eh, 5\n"
            "отложить AC-K171eh, 2, Рейканен\n"
            "снять AC-K171eh, Рейканен, 1\n\n"
        )
    else:
        welcome_text += "⛔ Команды изменения и резервирования доступны только администраторам.\n\n"

    welcome_text += "📋 Команда /last — последние 5 изменений (доступна всем).\n"
    welcome_text += "📋 Команда /reserves — показать все резервы (доступна всем)."

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

async def reserves_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ к боту запрещён.")
        return

    if not reserves:
        await update.message.reply_text("📭 Нет активных резервов.")
        return

    lines = []
    for art, res_list in reserves.items():
        for r in res_list:
            lines.append(f"• {art} — {r['client']}: {r['qty']} ед.")
    await update.message.reply_text("📋 Текущие резервы:\n" + "\n".join(lines))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ к боту запрещён.")
        return

    text = clean_text(update.message.text)
    if not text:
        return

    # ---------- Команды администраторов ----------
    match_reserve = re.match(r'^отложить\s+([^,]+?)\s*,\s*(\d+)\s*,\s*(.+)$', text, re.IGNORECASE)
    if match_reserve:
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("⛔ У вас нет прав на резервирование.")
            return

        art_input = clean_text(match_reserve.group(1))
        try:
            qty_reserve = int(match_reserve.group(2))
        except ValueError:
            await update.message.reply_text("❌ Количество должно быть целым числом.")
            return
        client = clean_text(match_reserve.group(3))

        if qty_reserve <= 0:
            await update.message.reply_text("❌ Количество должно быть положительным.")
            return

        # Поиск артикула на складе (точное совпадение нормализованного)
        norm_art = normalize_art(art_input)
        if norm_art in stock_norm_to_art:
            original_art = stock_norm_to_art[norm_art]
        else:
            candidates = [a for a in inventory if normalize_art(a) == norm_art]
            if not candidates:
                await update.message.reply_text(f"❌ Артикул '{art_input}' не найден на складе.")
                return
            if len(candidates) == 1:
                original_art = candidates[0]
            else:
                # несколько кандидатов (редко)
                lines = [format_catalog_art(art) for art in candidates]
                reply = "🔍 Найдено несколько артикулов на складе:\n\n" + "\n\n".join(lines)
                await update.message.reply_text(reply)
                return

        dop, current_qty, price = inventory[original_art]
        total_reserved = sum(r['qty'] for r in reserves.get(original_art, []))
        available = current_qty - total_reserved
        if qty_reserve > available:
            await update.message.reply_text(
                f"❌ Недостаточно свободного товара. Доступно: {available} (всего {current_qty}, зарезервировано {total_reserved})."
            )
            return

        if original_art not in reserves:
            reserves[original_art] = []
        reserves[original_art].append({"client": client, "qty": qty_reserve})
        save_reserves(reserves)

        actor_name = USER_NAMES.get(user_id, f"пользователь {user_id}")
        reply = (
            f"✅ Зарезервировано {qty_reserve} ед. для '{client}' по артикулу {original_art}.\n"
            f"📦 Доступно на складе: {available - qty_reserve} (всего {current_qty}, зарезервировано {total_reserved + qty_reserve})\n"
            f"👤 Резерв создал: {actor_name}"
        )
        await update.message.reply_text(reply)
        return

    match_remove = re.match(r'^снять\s+([^,]+?)\s*,\s*([^,]+?)(?:\s*,\s*(\d+))?$', text, re.IGNORECASE)
    if match_remove:
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("⛔ У вас нет прав на снятие резерва.")
            return

        art_input = clean_text(match_remove.group(1))
        client = clean_text(match_remove.group(2))
        qty_to_remove = match_remove.group(3)
        if qty_to_remove is not None:
            try:
                qty_to_remove = int(qty_to_remove)
                if qty_to_remove <= 0:
                    await update.message.reply_text("❌ Количество должно быть положительным.")
                    return
            except ValueError:
                await update.message.reply_text("❌ Количество должно быть целым числом.")
                return
        else:
            qty_to_remove = None

        norm_art = normalize_art(art_input)
        if norm_art in stock_norm_to_art:
            original_art = stock_norm_to_art[norm_art]
        else:
            candidates = [a for a in inventory if normalize_art(a) == norm_art]
            if not candidates:
                await update.message.reply_text(f"❌ Артикул '{art_input}' не найден на складе.")
                return
            if len(candidates) == 1:
                original_art = candidates[0]
            else:
                lines = [format_catalog_art(art) for art in candidates]
                reply = "🔍 Найдено несколько артикулов на складе:\n\n" + "\n\n".join(lines)
                await update.message.reply_text(reply)
                return

        if original_art not in reserves or not reserves[original_art]:
            await update.message.reply_text(f"❌ По артикулу {original_art} нет резервов.")
            return

        client_reserves = [r for r in reserves[original_art] if r['client'].lower() == client.lower()]
        if not client_reserves:
            await update.message.reply_text(f"❌ Для артикула {original_art} нет резерва для клиента '{client}'.")
            return

        if qty_to_remove is None:
            removed_total = sum(r['qty'] for r in client_reserves)
            reserves[original_art] = [r for r in reserves[original_art] if r['client'].lower() != client.lower()]
            action_msg = f"✅ Снят весь резерв ({removed_total} ед.) для клиента '{client}' по артикулу {original_art}."
        else:
            found = False
            for i, r in enumerate(reserves[original_art]):
                if r['client'].lower() == client.lower() and r['qty'] >= qty_to_remove:
                    r['qty'] -= qty_to_remove
                    if r['qty'] == 0:
                        del reserves[original_art][i]
                    found = True
                    break
            if not found:
                await update.message.reply_text(f"❌ Нет резерва для '{client}' с количеством >= {qty_to_remove}.")
                return
            action_msg = f"✅ Снято {qty_to_remove} ед. из резерва для клиента '{client}' по артикулу {original_art}."
            reserves[original_art] = [r for r in reserves[original_art] if r['qty'] > 0]

        if not reserves[original_art]:
            del reserves[original_art]

        save_reserves(reserves)

        dop, current_qty, price = inventory[original_art]
        total_reserved = sum(r['qty'] for r in reserves.get(original_art, []))
        available = current_qty - total_reserved
        actor_name = USER_NAMES.get(user_id, f"пользователь {user_id}")
        reply = (
            f"{action_msg}\n"
            f"📦 Теперь по артикулу {original_art}: всего {current_qty}, доступно {available}, зарезервировано {total_reserved}\n"
            f"👤 Действие выполнил: {actor_name}"
        )
        await update.message.reply_text(reply)
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

        norm_art = normalize_art(art_input)
        if norm_art in stock_norm_to_art:
            original_art = stock_norm_to_art[norm_art]
        else:
            candidates = [a for a in inventory if normalize_art(a) == norm_art]
            if not candidates:
                await update.message.reply_text(f"❌ Артикул '{art_input}' не найден на складе.")
                return
            if len(candidates) == 1:
                original_art = candidates[0]
            else:
                lines = [format_catalog_art(art) for art in candidates]
                reply = "🔍 Найдено несколько артикулов на складе:\n\n" + "\n\n".join(lines)
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
        save_inventory()
        log_change(user_id, action, original_art, delta, qty)

        art_reserves = reserves.get(original_art, [])
        total_reserved = sum(r['qty'] for r in art_reserves)
        available = qty - total_reserved

        actor_name = USER_NAMES.get(user_id, f"пользователь {user_id}")
        reply = (
            f"✅ {action.capitalize()} {delta} ед. для артикула {original_art}.\n"
            f"📦 Теперь количество: {qty} (доступно: {available}, зарезервировано: {total_reserved})\n"
            f"💰 Цена за единицу: {price}\n"
            f"👤 Изменение внёс: {actor_name}"
        )
        await update.message.reply_text(reply)
        return

    # ---------- Поиск по каталогу (не команда) ----------
    arts = find_catalog_arts(text)
    if not arts:
        await update.message.reply_text(f"❌ Артикул '{text}' не найден в каталоге.")
        return

    sorted_arts = sorted(arts)
    total = len(sorted_arts)

    if total == 1:
        reply = format_catalog_art(sorted_arts[0])
        await update.message.reply_text(reply)
    else:
        # Отправляем не более 10 артикулов, чтобы не превысить лимит сообщения
        MAX_DISPLAY = 10
        await update.message.reply_text(f"🔍 Найдено артикулов: {total}. Показываю первые {MAX_DISPLAY}:")
        shown = 0
        for art in sorted_arts:
            if shown >= MAX_DISPLAY:
                break
            await update.message.reply_text(format_catalog_art(art))
            shown += 1
        if total > MAX_DISPLAY:
            await update.message.reply_text(f"... и ещё {total - MAX_DISPLAY} артикулов. Уточните запрос.")

def main():
    app = Application.builder().token(API_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admins", admins))
    app.add_handler(CommandHandler("last", last_changes))
    app.add_handler(CommandHandler("reserves", reserves_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 Бот складского учёта и каталога запущен...")
    print(f"🔒 Доступ разрешён для {len(ALLOWED_IDS)} пользователей.")
    print(f"🔑 Администраторов: {len(ADMIN_IDS)}")
    app.run_polling()

if __name__ == '__main__':
    main()
