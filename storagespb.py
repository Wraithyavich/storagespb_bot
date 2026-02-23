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

# ---------- Очистка текста ----------
def clean_text(s):
    """Удаляет лишние пробелы, управляющие символы и BOM."""
    s = s.strip()
    s = s.replace('\r', '').replace('\n', '').replace('\ufeff', '')
    return ' '.join(s.split())

# ---------- Загрузка данных из CSV ----------
# Структура: словарь, где ключ - артикул (первая колонка), значение - список [доп_артикул, количество, цена]
# Также для быстрого поиска можно хранить все строки в списке, но для изменения удобнее словарь.
inventory = {}  # артикул -> [доп_артикул, количество, цена]

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
                try:
                    price = float(clean_text(row[3].replace(',', '.')))  # поддержка запятой как разделителя
                except ValueError:
                    price = 0.0
                if art:  # артикул не пустой
                    inventory[art] = [dop, qty, price]
except FileNotFoundError:
    # Если файла нет, создадим пустой словарь
    print(f"⚠️ Файл {DATA_FILE} не найден, будет создан при первом изменении.")
except Exception as e:
    print(f"❌ Ошибка загрузки: {e}")

print(f"✅ Загружено {len(inventory)} записей.")

# ---------- Сохранение данных в CSV ----------
def save_inventory():
    """Перезаписывает файл с актуальными данными."""
    with open(DATA_FILE, mode='w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file, delimiter=';')
        for art, (dop, qty, price) in inventory.items():
            writer.writerow([art, dop, qty, price])

# ---------- Обработчики команд ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 Бот управления складом.\n"
        "Команды:\n"
        "• добавить АРТИКУЛ, КОЛИЧЕСТВО — увеличить запас\n"
        "• убавить АРТИКУЛ, КОЛИЧЕСТВО — уменьшить запас\n\n"
        "Пример: добавить AC-K171eh, 5"
    )
    await update.message.reply_text(welcome_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = clean_text(update.message.text)
    if not text:
        return

    # Регулярное выражение для команд: (добавить|убавить) артикул, число
    match = re.match(r'^(добавить|убавить)\s+([^,]+?)\s*,\s*(\d+)$', text, re.IGNORECASE)
    if not match:
        await update.message.reply_text("❌ Не понял команду. Используйте: добавить АРТИКУЛ, КОЛИЧЕСТВО или убавить АРТИКУЛ, КОЛИЧЕСТВО")
        return

    command = match.group(1).lower()
    art = clean_text(match.group(2))
    try:
        delta = int(match.group(3))
    except ValueError:
        await update.message.reply_text("❌ Количество должно быть целым числом.")
        return

    if delta <= 0:
        await update.message.reply_text("❌ Количество должно быть положительным.")
        return

    # Поиск артикула (без учёта регистра? Сейчас ищем точно, но можно добавить нормализацию)
    # Для простоты ищем точное совпадение, но можно привести к нижнему регистру.
    # Если нужно без учёта регистра, можно создать дополнительный словарь.
    found = None
    for key in inventory:
        if key.lower() == art.lower():
            found = key
            break

    if not found:
        await update.message.reply_text(f"❌ Артикул '{art}' не найден.")
        return

    # Получаем текущие данные
    dop, qty, price = inventory[found]

    if command == 'добавить':
        qty += delta
        action = "добавлено"
    else:  # убавить
        if qty - delta < 0:
            await update.message.reply_text(f"❌ Недостаточно запаса: текущее количество {qty}, невозможно убавить {delta}.")
            return
        qty -= delta
        action = "убавлено"

    # Обновляем запись
    inventory[found] = [dop, qty, price]

    # Сохраняем изменения в файл
    try:
        save_inventory()
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при сохранении: {e}")
        return

    # Формируем ответ
    price_str = f"{price:.2f}".replace('.', ',')  # для красоты
    reply = (
        f"✅ {action.capitalize()} {delta} ед. для артикула {found}.\n"
        f"📦 Теперь количество: {qty}\n"
        f"💰 Цена за единицу: {price_str}"
    )
    await update.message.reply_text(reply)

# ---------- Запуск бота ----------
def main():
    app = Application.builder().token(API_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 Бот управления складом запущен...")
    app.run_polling()

if __name__ == '__main__':

    main()

