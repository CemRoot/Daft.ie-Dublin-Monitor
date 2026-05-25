import os
import json
import logging
import threading
from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
import requests
import base64

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constants and Configuration
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") # Needs to be added to Render/Actions
REPO = os.environ.get("GITHUB_REPOSITORY") # Format: user/repo

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

LOCATION_OPTIONS = {
    "dublin-6-dublin": "Dublin 6",
    "dublin-6w-dublin": "Dublin 6W",
    "dublin-1-dublin": "Dublin 1",
    "dublin-2-dublin": "Dublin 2",
    "dublin-4-dublin": "Dublin 4",
    "dublin-8-dublin": "Dublin 8"
}

def download_from_github(filename):
    if not GITHUB_TOKEN or not REPO:
        logger.warning("GITHUB_TOKEN or GITHUB_REPOSITORY not set, skipping download.")
        return None

    url = f"https://api.github.com/repos/{REPO}/contents/{filename}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    res = requests.get(url, headers=headers)
    if res.status_code == 200:
        data = res.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.error(f"Error decoding JSON from {filename}")
            return None
    else:
        logger.error(f"Failed to download {filename} from GitHub: {res.text}")
        return None

def sync_state_from_github():
    state = download_from_github("state.json")
    if state:
        try:
            with open("state.json", "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            logger.info("Successfully synced state.json from GitHub")
        except Exception as e:
            logger.error(f"Error saving synced state.json: {e}")

def load_state():
    try:
        with open("state.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading state: {e}")
        return {"price_min": 1500, "price_max": 1800, "locations": ["dublin-6-dublin", "dublin-6w-dublin"], "auto_notify": True, "favorites": []}

def save_state(state):
    try:
        with open("state.json", "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        commit_to_github("state.json", "Update state.json from bot")
    except Exception as e:
        logger.error(f"Error saving state: {e}")

def load_recent_listings():
    # Sync first to ensure we have latest listings if Render woke up
    listings_data = download_from_github("recent_listings.json")
    if listings_data is not None:
        return listings_data

    try:
        with open("recent_listings.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading recent listings: {e}")
        return []

def commit_to_github(filename, message):
    if not GITHUB_TOKEN or not REPO:
        logger.warning("GITHUB_TOKEN or GITHUB_REPOSITORY not set, skipping commit.")
        return

    url = f"https://api.github.com/repos/{REPO}/contents/{filename}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    # Get current file sha
    sha = None
    res = requests.get(url, headers=headers)
    if res.status_code == 200:
        sha = res.json().get("sha")

    with open(filename, "rb") as f:
        content = base64.b64encode(f.read()).decode("utf-8")

    data = {
        "message": message,
        "content": content,
    }
    if sha:
        data["sha"] = sha

    put_res = requests.put(url, headers=headers, json=data)
    if put_res.status_code in [200, 201]:
        logger.info(f"Successfully committed {filename} to GitHub.")
    else:
        logger.error(f"Failed to commit {filename}: {put_res.text}")

def trigger_github_scan():
    if not GITHUB_TOKEN or not REPO:
        return False, "GITHUB_TOKEN veya GITHUB_REPOSITORY tanımlı değil. Action tetiklenemez."

    url = f"https://api.github.com/repos/{REPO}/actions/workflows/scan.yml/dispatches"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    data = {
        "ref": os.environ.get("GITHUB_REF", "daft-monitor-setup-2684401370211648289")
    }

    try:
        res = requests.post(url, headers=headers, json=data)
        if res.status_code == 204:
            return True, "Tarama GitHub Actions üzerinden başlatıldı! Sonuçlar bulunursa bildirim alacaksınız."
        else:
            return False, f"Tarama başlatılamadı: {res.text}"
    except Exception as e:
        return False, f"Hata oluştu: {e}"

@app.route('/ping')
def ping():
    return "Pong", 200

def get_main_menu_markup():
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🔍 Hemen Tara", callback_data="cmd_scan"))
    markup.row(InlineKeyboardButton("📋 Son İlanlar", callback_data="cmd_list"), InlineKeyboardButton("⭐ Favoriler", callback_data="cmd_fav"))
    markup.row(InlineKeyboardButton("💶 Fiyat Ayarla", callback_data="cmd_setprice"), InlineKeyboardButton("📍 Bölge Ayarla", callback_data="cmd_setlocation"))
    markup.row(InlineKeyboardButton("🔔 Bildirimleri Aç/Kapat", callback_data="cmd_toggle"))
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.send_message(message.chat.id, "🏠 *Daft.ie Monitor Bot'a Hoş Geldiniz!*\n\nAşağıdaki menüden işlemlerinizi seçebilirsiniz.", reply_markup=get_main_menu_markup(), parse_mode="Markdown")

@bot.message_handler(commands=['help'])
def send_help(message):
    help_text = """
    *Komutlar:*
/start - Ana menüyü gösterir
/scan - Daft.ie'yi hemen tarar
/list - Son görülen ilanları listeler
/setprice - Fiyat aralığını değiştirir
/setlocation - Bölgeleri ayarlar
/toggle - Otomatik bildirimleri açıp kapatır
/fav - Favori ilanlarınızı gösterir
/help - Bu yardım mesajını gösterir
    """
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

def handle_scan(chat_id):
    bot.send_message(chat_id, "Tarama isteği GitHub Actions'a iletiliyor...")
    success, msg = trigger_github_scan()
    bot.send_message(chat_id, msg)

def handle_list(chat_id):
    listings = load_recent_listings()
    if not listings:
        bot.send_message(chat_id, "Henüz kaydedilmiş son ilan yok.")
        return

    msg = "*📋 Son İlanlar (Max 10)*\n\n"
    for idx, item in enumerate(listings[:10]):
        msg += f"{idx+1}. [{item.get('title', 'İlan')} - {item.get('price', '')}]({item.get('url', '')})\n"

    bot.send_message(chat_id, msg, parse_mode="Markdown", disable_web_page_preview=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('cmd_'))
def callback_handler(call):
    cmd = call.data.split('_')[1]
    if cmd == "scan":
        bot.answer_callback_query(call.id)
        handle_scan(call.message.chat.id)
    elif cmd == "list":
        bot.answer_callback_query(call.id)
        handle_list(call.message.chat.id)
    elif cmd == "setprice":
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "Yeni fiyat aralığını girin (Örn: 1500-2000):")
        bot.register_next_step_handler(msg, process_price_step)
    elif cmd == "setlocation":
        bot.answer_callback_query(call.id)
        markup = InlineKeyboardMarkup()
        state = load_state()
        for loc_id, loc_name in LOCATION_OPTIONS.items():
            status = "✅" if loc_id in state["locations"] else "❌"
            markup.row(InlineKeyboardButton(f"{status} {loc_name}", callback_data=f"loc_{loc_id}"))
        markup.row(InlineKeyboardButton("Bitti", callback_data="loc_done"))
        bot.send_message(call.message.chat.id, "Takip edilecek bölgeleri seçin:", reply_markup=markup)
    elif cmd == "toggle":
        state = load_state()
        state["auto_notify"] = not state.get("auto_notify", True)
        save_state(state)
        status = "açık" if state["auto_notify"] else "kapalı"
        bot.answer_callback_query(call.id, f"Otomatik bildirimler {status}.")
        bot.send_message(call.message.chat.id, f"Otomatik bildirimler şu an: *{status}*", parse_mode="Markdown")
    elif cmd == "fav":
        bot.answer_callback_query(call.id)
        state = load_state()
        favs = state.get("favorites", [])
        listings = load_recent_listings()
        if not favs:
            bot.send_message(call.message.chat.id, "Henüz favori ilanınız yok.")
        else:
            msg = f"Toplam {len(favs)} favori ilanınız var:\n\n"
            for ad_id in favs:
                # Try to find url from recent listings if possible
                url = next((item.get("url") for item in listings if item.get("id") == ad_id), f"https://www.daft.ie/for-rent/apartment/-/{ad_id}")
                msg += f"⭐ [İlan {ad_id}]({url})\n"
            bot.send_message(call.message.chat.id, msg, parse_mode="Markdown", disable_web_page_preview=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('loc_'))
def handle_location_toggle(call):
    action = call.data.replace('loc_', '')
    if action == "done":
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id, "Bölgeler kaydedildi.", reply_markup=get_main_menu_markup())
        return

    state = load_state()
    if action in state["locations"]:
        state["locations"].remove(action)
    else:
        state["locations"].append(action)
    save_state(state)

    markup = InlineKeyboardMarkup()
    for loc_id, loc_name in LOCATION_OPTIONS.items():
        status = "✅" if loc_id in state["locations"] else "❌"
        markup.row(InlineKeyboardButton(f"{status} {loc_name}", callback_data=f"loc_{loc_id}"))
    markup.row(InlineKeyboardButton("Bitti", callback_data="loc_done"))

    bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('fav_'))
def handle_favorite(call):
    ad_id = call.data.replace('fav_', '')
    state = load_state()
    favs = state.get("favorites", [])
    if ad_id in favs:
        favs.remove(ad_id)
        msg = "Favorilerden çıkarıldı."
    else:
        favs.append(ad_id)
        msg = "Favorilere eklendi! ⭐"

    state["favorites"] = favs
    save_state(state)
    bot.answer_callback_query(call.id, msg)

def process_price_step(message):
    try:
        parts = message.text.split('-')
        if len(parts) != 2:
            raise ValueError
        min_p = int(parts[0].strip())
        max_p = int(parts[1].strip())
        state = load_state()
        state["price_min"] = min_p
        state["price_max"] = max_p
        save_state(state)
        bot.send_message(message.chat.id, f"Fiyat aralığı güncellendi: €{min_p} - €{max_p}", reply_markup=get_main_menu_markup())
    except:
        bot.send_message(message.chat.id, "Hatalı format. Lütfen '1500-1800' şeklinde girin.")

@bot.message_handler(commands=['scan'])
def cmd_scan(message):
    handle_scan(message.chat.id)

@bot.message_handler(commands=['list'])
def cmd_list(message):
    handle_list(message.chat.id)

@bot.message_handler(commands=['setprice'])
def cmd_setprice(message):
    msg = bot.send_message(message.chat.id, "Yeni fiyat aralığını girin (Örn: 1500-2000):")
    bot.register_next_step_handler(msg, process_price_step)

@bot.message_handler(commands=['setlocation'])
def cmd_setlocation(message):
    markup = InlineKeyboardMarkup()
    state = load_state()
    for loc_id, loc_name in LOCATION_OPTIONS.items():
        status = "✅" if loc_id in state["locations"] else "❌"
        markup.row(InlineKeyboardButton(f"{status} {loc_name}", callback_data=f"loc_{loc_id}"))
    markup.row(InlineKeyboardButton("Bitti", callback_data="loc_done"))
    bot.send_message(message.chat.id, "Takip edilecek bölgeleri seçin:", reply_markup=markup)

@bot.message_handler(commands=['toggle'])
def cmd_toggle(message):
    state = load_state()
    state["auto_notify"] = not state.get("auto_notify", True)
    save_state(state)
    status = "açık" if state["auto_notify"] else "kapalı"
    bot.send_message(message.chat.id, f"Otomatik bildirimler şu an: *{status}*", parse_mode="Markdown")

@bot.message_handler(commands=['fav'])
def cmd_fav(message):
    state = load_state()
    favs = state.get("favorites", [])
    listings = load_recent_listings()
    if not favs:
        bot.send_message(message.chat.id, "Henüz favori ilanınız yok.")
    else:
        msg = f"Toplam {len(favs)} favori ilanınız var:\n\n"
        for ad_id in favs:
            url = next((item.get("url") for item in listings if item.get("id") == ad_id), f"https://www.daft.ie/for-rent/apartment/-/{ad_id}")
            msg += f"⭐ [İlan {ad_id}]({url})\n"
        bot.send_message(message.chat.id, msg, parse_mode="Markdown", disable_web_page_preview=True)

def setup_bot_commands():
    bot.set_my_commands([
        BotCommand("start", "Ana menü"),
        BotCommand("scan", "Hemen tara"),
        BotCommand("list", "Son ilanlar"),
        BotCommand("setprice", "Fiyat aralığı"),
        BotCommand("setlocation", "Bölge seç"),
        BotCommand("toggle", "Bildirimleri aç/kapat"),
        BotCommand("fav", "Favoriler"),
        BotCommand("help", "Yardım"),
    ])

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    if not TOKEN:
        logger.error("TELEGRAM_TOKEN is not set.")
    else:
        logger.info("Starting bot and Flask server...")
        sync_state_from_github()
        setup_bot_commands()

        flask_thread = threading.Thread(target=run_flask)
        flask_thread.daemon = True
        flask_thread.start()

        bot.infinity_polling()
