import csv
import os
import re
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ---------- Получение токена из переменной окружения ----------
API_TOKEN = os.environ.get('API_TOKEN')
if API_TOKEN is None:
    raise ValueError("❌ Переменная окружения INVENTORY_BOT_TOKEN не задана!")

# ---------- Имя файла с данными ----------
DATA_FILE = 'inventory.csv'
MIN_SEARCH_LENGTH = 4  # минимальная длина для частичного поиска

# ---------- Очистка текста ----------
def clean_text(s):
    """Удаляет лишние пробелы, управляющие символы и BOM."""
    s = s.strip()
    s = s.replace('\r', '').replace('\n', '').replace('\ufeff', '')
    return ' '.join(s.split())

def normalize_art(s):
    """
    Приводит строку к нижнему регистру и удаляет всё, кроме букв и цифр.
    Это позволяет находить артикулы независимо от наличия дефисов, точек, слешей и т.п.
    """
    # сначала приводим к нижнему регистру
    s = s.lower()
    # удаляем все символы, не являющиеся буквами или цифрами
    s = re.sub(r'[^a-z0-9]', '', s)
    return s

# ---------- Загрузка данных из CSV ----------
inventory = {}               # оригинальный артикул -> [доп_артикул, количество, цена (строка)]
art_norm_to_original = {}    # нормализованный основной артикул -> оригинальный артикул
dop_norm_to_original = {}    # нормализованный доп. артикул -> оригинальный основной артикул

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
                price = clean_text(row[3])  # оставляем как есть, включая пробелы и т.д.
                if art:  # артикул не должен быть пустым
                    inventory[art] = [dop, qty, price]
                    art_norm_to_original[normalize_art(art)] = art
                    if dop:
                        dop_norm_to_original[normalize_art(dop)] = art
except FileNotFoundError:
    print(f"⚠️ Файл {DATA_FILE} не найден, будет создан при первом изменении.")
except Exception as e:
    print(f"❌ Ошибка загрузки: {e}")

print(f"✅ Загружено {len(inventory)} записей.")
# Для отладки можно вывести первые несколько нормализованных ключей:
# print("Примеры нормализованных артикулов:", list(art_norm_to_original.keys())[:10])

# ---------- Сохранение данных в CSV ----------
def save_inventory():
    """Перезаписывает файл с актуальными данными."""
    with open(DATA_FILE, mode='w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file, delimiter=';')
        for art, (dop, qty, price) in inventory.items():
            writer.writerow([art, dop, qty, price])

# ---------- Функции поиска ----------
def find_exact_original_art(query):
    """Возвращает оригинальный основной артикул по точному совпадению (основному или доп.)."""
    norm_query = normalize_art(query)
    if norm_query in art_norm_to_original:
        return art_norm_to_original[norm_query]
    if norm_query in dop_norm_to_original:
        return dop_norm_to_original[norm_query]
    return None

def partial_search(query):
    """Возвращает список оригинальных основных артикулов, у которых основной или доп. артикул содержит query как подстроку."""
    norm_query = normalize_art(query)
    if len(norm_query) < MIN_SEARCH_LENGTH:
        return []  # для коротких запросов частичный поиск не делаем

    results = set()
    # Поиск по основным артикулам
    for norm_art, orig_art in art_norm_to_original.items():
        if norm_query in norm_art:
            results.add(orig_art)
    # Поиск по дополнительным артикулам
    for norm_dop, orig_art in dop_norm_to_original.items():
        if norm_query in norm_dop:
            results.add(orig_art)
    return sorted(results)

# ---------- Вспомогательная функция для форматирования информации об артикуле ----------
def format_item_info(art):
    dop, qty, price = inventory[art]
    return (
        f"🔍 Артикул: {art}\n"
        f"  📎 Доп. артикул: {dop}\n"
        f"  📦 Количество: {qty}\n"
        f"  💰 Цена: {price}"
    )

# ---------- Обработчики команд ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 Бот складского учёта.\n\n"
        "🔍 Просто отправьте артикул (основной или дополнительный), и я покажу информацию о нём.\n"
        f"Можно искать по части номера (минимум {MIN_SEARCH_LENGTH} символа).\n"
        "Регистр и разделители (дефисы, точки) не важны — я пойму.\n\n"
        "📦 Команды для изменения количества:\n"
        "• добавить АРТИКУЛ, КОЛИЧЕСТВО — увеличить запас\n"
        "• убавить АРТИКУЛ, КОЛИЧЕСТВО — уменьшить запас\n\n"
        "Пример: добавить AC-K171eh, 5\n\n"
        "Для неполных артикулов при изменении нужно точное совпадение, иначе бот покажет список найденных."
    )
    await update.message.reply_text(welcome_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = clean_text(update.message.text)
    if not text:
        return

    # Проверяем, является ли сообщение командой изменения
    match_cmd = re.match(r'^(добавить|убавить)\s+([^,]+?)\s*,\s*(\d+)$', text, re.IGNORECASE)
    if match_cmd:
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

        # Сначала пытаемся найти точное совпадение
        original_art = find_exact_original_art(art_input)
        if original_art is None:
            # Если точного нет, пробуем частичный поиск
            candidates = partial_search(art_input)
            if not candidates:
                await update.message.reply_text(f"❌ Артикул '{art_input}' не найден.")
                return
            if len(candidates) == 1:
                original_art = candidates[0]
            else:
                # Несколько кандидатов – показываем список и просим уточнить
                lines = [format_item_info(art) for art in candidates]
                reply = "🔍 Найдено несколько артикулов:\n\n" + "\n\n".join(lines)
                await update.message.reply_text(reply)
                return

        dop, qty, price = inventory[original_art]

        if command == 'добавить':
            qty += delta
            action = "добавлено"
        else:  # убавить
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

        reply = (
            f"✅ {action.capitalize()} {delta} ед. для артикула {original_art}.\n"
            f"📦 Теперь количество: {qty}\n"
            f"💰 Цена за единицу: {price}"
        )
        await update.message.reply_text(reply)
        return

    # Если не команда, считаем запросом артикула
    # Сначала точный поиск
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
        # Частичный поиск
        candidates = partial_search(text)
        if not candidates:
            await update.message.reply_text(f"❌ Артикул '{text}' не найден.")
            return
        # Если есть результаты, показываем все
        lines = [format_item_info(art) for art in candidates]
        reply = "🔍 Найдено несколько артикулов:\n\n" + "\n\n".join(lines)

    await update.message.reply_text(reply)

# ---------- Запуск бота ----------
def main():
    app = Application.builder().token(API_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 Бот складского учёта запущен...")
    app.run_polling()

if __name__ == '__main__':
    main()
