import os
import json
import logging
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import telebot
from telebot.apihelper import ApiTelegramException
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, Update
import requests
import base64

from locations import LOCATION_OPTIONS

DATE_RANGE_OPTIONS = {
    1: "Son 1 gün",
    7: "Son 7 gün",
    14: "Son 14 gün",
    30: "Son 30 gün",
}

def format_date_range_label(date_range_days):
    if date_range_days is None:
        return "Tümü (filtre yok)"
    return DATE_RANGE_OPTIONS.get(date_range_days, f"Son {date_range_days} gün")

def get_date_range_markup(state):
    current = state.get("date_range_days")
    markup = InlineKeyboardMarkup()
    for days, label in DATE_RANGE_OPTIONS.items():
        status = "✅" if current == days else ""
        markup.row(InlineKeyboardButton(f"{status} {label}".strip(), callback_data=f"date_{days}"))
    all_status = "✅" if current is None else ""
    markup.row(InlineKeyboardButton(f"{all_status} Tümü".strip(), callback_data="date_all"))
    markup.row(InlineKeyboardButton("Bitti", callback_data="date_done"))
    return markup

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constants and Configuration
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") # Needs to be added to Render/Actions
REPO = os.environ.get("GITHUB_REPOSITORY") # Format: user/repo
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)
_webhook_mode = False
_startup_lock = threading.Lock()
_startup_done = False
_bot_username_cache = None
_last_update_at = None
_webhook_info_cache = {}

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
        return {"price_min": 1500, "price_max": 1800, "locations": ["dublin-6-dublin", "dublin-6w-dublin"], "auto_notify": True, "favorites": [], "date_range_days": 30}

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
            return True, "Tarama GitHub Actions üzerinden başlatıldı! Tamamlandığında sonuç bildirimi alacaksınız."
        else:
            return False, f"Tarama başlatılamadı: {res.text}"
    except Exception as e:
        return False, f"Hata oluştu: {e}"

@app.route('/')
def root():
    return ping()

def _status_info():
    """Shared status fields for /ping and /health (no Telegram API calls)."""
    commit = os.environ.get("RENDER_GIT_COMMIT", "local")
    branch = os.environ.get("RENDER_GIT_BRANCH", "unknown")
    mode = "webhook" if _webhook_mode else "polling"
    return {
        "status": "ok",
        "bot": _bot_username_cache or "unknown",
        "mode": mode,
        "branch": branch,
        "commit": commit[:7],
        "locations": len(LOCATION_OPTIONS),
        "date_filter": True,
    }

@app.route('/ping')
def ping():
    """Fast keep-alive endpoint for UptimeRobot (no Telegram API calls)."""
    info = _status_info()
    return (
        f"Pong | bot={info['bot']} | mode={info['mode']} | "
        f"{info['branch']}@{info['commit']} | locations={info['locations']} | date_filter=True"
    ), 200

@app.route('/health')
def health():
    """Health check for Render and UptimeRobot (same data as /ping, JSON)."""
    return jsonify(_status_info()), 200

@app.route('/status')
def status():
    """Diagnostics: last webhook update and Telegram webhook errors."""
    ensure_production_startup()
    last_at = _last_update_at
    last_ago = round(time.time() - last_at, 1) if last_at else None
    return jsonify({
        **_status_info(),
        "last_update_at": datetime.fromtimestamp(last_at, tz=timezone.utc).isoformat() if last_at else None,
        "last_update_ago_sec": last_ago,
        "webhook_url": get_webhook_url() if _webhook_mode else None,
        "webhook_info": _webhook_info_cache,
    }), 200

def _process_update_async(payload_bytes):
    global _last_update_at
    try:
        payload = json.loads(payload_bytes)
        update_id = payload.get("update_id")
        logger.info("Processing webhook update_id=%s", update_id)
        update = Update.de_json(payload)
        bot.process_new_updates([update])
        _last_update_at = time.time()
        logger.info("Finished webhook update_id=%s", update_id)
    except Exception as e:
        logger.exception("Webhook update processing failed: %s", e)

@app.route("/webhook", methods=["POST"])
@app.route("/webhook/<secret>", methods=["POST"])
def webhook_handler(secret=None):
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        return "", 403
    ensure_production_startup()
    if request.content_type != "application/json":
        return "", 400
    payload_bytes = request.get_data()
    if not payload_bytes:
        return "", 400
    try:
        update_id = json.loads(payload_bytes).get("update_id")
    except json.JSONDecodeError:
        return "", 400
    logger.info("Webhook received update_id=%s", update_id)
    threading.Thread(
        target=_process_update_async,
        args=(payload_bytes,),
        daemon=True,
    ).start()
    return "", 200

def get_main_menu_markup():
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🔍 Hemen Tara", callback_data="cmd_scan"))
    markup.row(InlineKeyboardButton("📋 Son İlanlar", callback_data="cmd_list"), InlineKeyboardButton("⭐ Favoriler", callback_data="cmd_fav"))
    markup.row(InlineKeyboardButton("💶 Fiyat Ayarla", callback_data="cmd_setprice"), InlineKeyboardButton("📍 Bölge Ayarla", callback_data="cmd_setlocation"))
    markup.row(InlineKeyboardButton("📅 Tarih Filtresi", callback_data="cmd_setdate"))
    markup.row(InlineKeyboardButton("🔔 Bildirimleri Aç/Kapat", callback_data="cmd_toggle"))
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    text = (
        "🏠 <b>Daft.ie Monitor Bot'a Hoş Geldiniz!</b>\n\n"
        "Aşağıdaki menüden işlemlerinizi seçebilirsiniz."
    )
    try:
        bot.send_message(
            message.chat.id,
            text,
            reply_markup=get_main_menu_markup(),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("Failed to send /start reply to chat %s: %s", message.chat.id, e)
        bot.send_message(message.chat.id, text, reply_markup=get_main_menu_markup())

@bot.message_handler(commands=['help'])
def send_help(message):
    help_text = """
    *Komutlar:*
/start - Ana menüyü gösterir
/scan - Daft.ie'yi hemen tarar
/list - Son görülen ilanları listeler
/setprice - Fiyat aralığını değiştirir
/setlocation - Bölgeleri ayarlar
/setdate - Yayın tarihi filtresini ayarlar
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
    elif cmd == "setdate":
        bot.answer_callback_query(call.id)
        state = load_state()
        label = format_date_range_label(state.get("date_range_days"))
        bot.send_message(
            call.message.chat.id,
            f"Mevcut filtre: *{label}*\n\nYayın tarihi filtresini seçin:",
            reply_markup=get_date_range_markup(state),
            parse_mode="Markdown",
        )
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

@bot.callback_query_handler(func=lambda call: call.data.startswith('date_'))
def handle_date_range(call):
    action = call.data.replace('date_', '')
    if action == "done":
        state = load_state()
        label = format_date_range_label(state.get("date_range_days"))
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(
            call.message.chat.id,
            f"Yayın tarihi filtresi kaydedildi: *{label}*",
            reply_markup=get_main_menu_markup(),
            parse_mode="Markdown",
        )
        return

    state = load_state()
    if action == "all":
        state["date_range_days"] = None
    else:
        state["date_range_days"] = int(action)
    save_state(state)

    label = format_date_range_label(state["date_range_days"])
    bot.answer_callback_query(call.id, f"Filtre: {label}")
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"Mevcut filtre: *{label}*\n\nYayın tarihi filtresini seçin:",
        reply_markup=get_date_range_markup(state),
        parse_mode="Markdown",
    )

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

@bot.message_handler(commands=['setdate'])
def cmd_setdate(message):
    state = load_state()
    label = format_date_range_label(state.get("date_range_days"))
    bot.send_message(
        message.chat.id,
        f"Mevcut filtre: *{label}*\n\nYayın tarihi filtresini seçin:",
        reply_markup=get_date_range_markup(state),
        parse_mode="Markdown",
    )

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
        BotCommand("setdate", "Tarih filtresi"),
        BotCommand("toggle", "Bildirimleri aç/kapat"),
        BotCommand("fav", "Favoriler"),
        BotCommand("help", "Yardım"),
    ])

def get_webhook_base_url():
    return os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")

def get_webhook_url():
    base = get_webhook_base_url()
    if not base:
        return None
    base = base.rstrip("/")
    if WEBHOOK_SECRET:
        return f"{base}/webhook/{WEBHOOK_SECRET}"
    return f"{base}/webhook"

def use_webhook_mode():
    return bool(get_webhook_base_url())

def _cache_bot_identity():
    global _bot_username_cache
    try:
        me = bot.get_me()
        _bot_username_cache = f"@{me.username}"
        logger.info("Bot identity: %s (id=%s)", _bot_username_cache, me.id)
    except Exception as e:
        logger.warning("Could not fetch bot identity: %s", e)

def _log_webhook_info(info, url):
    global _webhook_info_cache
    _webhook_info_cache = {
        "url": info.url,
        "pending_update_count": info.pending_update_count,
        "last_error_date": info.last_error_date,
        "last_error_message": info.last_error_message,
        "max_connections": info.max_connections,
    }
    logger.info(
        "Webhook registered: %s (pending=%s, last_error=%s)",
        url,
        info.pending_update_count,
        info.last_error_message or "none",
    )
    if info.last_error_date:
        logger.warning(
            "Telegram webhook last error (date=%s): %s",
            info.last_error_date,
            info.last_error_message,
        )

def setup_webhook():
    url = get_webhook_url()
    if not url:
        return False
    bot.delete_webhook(drop_pending_updates=False)
    bot.set_webhook(
        url=url,
        drop_pending_updates=False,
        allowed_updates=["message", "callback_query"],
    )
    info = bot.get_webhook_info()
    _log_webhook_info(info, url)
    return True

def setup_webhook_with_retry(max_attempts=5, delay_sec=2):
    for attempt in range(1, max_attempts + 1):
        try:
            if setup_webhook():
                return True
        except Exception as e:
            logger.warning("Webhook setup attempt %d/%d failed: %s", attempt, max_attempts, e)
        if attempt < max_attempts:
            time.sleep(delay_sec)
    logger.error("Webhook setup failed after %d attempts", max_attempts)
    return False

def ensure_production_startup():
    global _webhook_mode, _startup_done
    if _startup_done or not TOKEN:
        return
    with _startup_lock:
        if _startup_done:
            return
        _webhook_mode = use_webhook_mode()
        if _webhook_mode:
            logger.info("Production startup: webhook mode")
            threading.Thread(target=sync_state_from_github, daemon=True).start()
            setup_webhook_with_retry()
            setup_bot_commands()
            _cache_bot_identity()
        _startup_done = True

@app.before_request
def _ensure_startup_before_request():
    if use_webhook_mode() and TOKEN:
        ensure_production_startup()

def teardown_webhook():
    if not _webhook_mode:
        return
    try:
        bot.delete_webhook(drop_pending_updates=False)
        logger.info("Webhook removed on shutdown")
    except Exception as e:
        logger.warning("Failed to remove webhook on shutdown: %s", e)

def _shutdown_handler(signum, _frame):
    logger.info("Received signal %s, shutting down...", signum)
    teardown_webhook()
    sys.exit(0)

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, threaded=True)

class PollingExceptionHandler(telebot.ExceptionHandler):
    def handle(self, exception):
        if isinstance(exception, ApiTelegramException) and exception.error_code == 409:
            logger.error(
                "Telegram 409 Conflict: another getUpdates client is active. "
                "Stop local bot.py when Render is running, or use webhook mode on Render."
            )
            return False
        logger.exception("Unhandled bot exception during polling: %s", exception)
        return False

def start_bot_polling():
    _cache_bot_identity()
    logger.info("Local dev mode: clearing webhook before polling...")
    bot.delete_webhook(drop_pending_updates=False)
    bot.exception_handler = PollingExceptionHandler()
    logger.info(
        "Starting infinity polling (locations=%d, branch=%s)...",
        len(LOCATION_OPTIONS),
        os.environ.get("RENDER_GIT_BRANCH", "local"),
    )
    bot.infinity_polling(
        skip_pending=False,
        timeout=30,
        long_polling_timeout=30,
        allowed_updates=["message", "callback_query"],
        logger_level=logging.WARNING,
    )

if __name__ == "__main__":
    if not TOKEN:
        logger.error("TELEGRAM_TOKEN is not set.")
    else:
        signal.signal(signal.SIGTERM, _shutdown_handler)
        signal.signal(signal.SIGINT, _shutdown_handler)

        if use_webhook_mode():
            logger.info("Render/production mode: webhook (use gunicorn on Render)")
            ensure_production_startup()
            run_flask()
        else:
            logger.info("Local dev mode: Flask + polling (set WEBHOOK_URL to test webhook locally)")
            sync_state_from_github()
            setup_bot_commands()
            _cache_bot_identity()
            flask_thread = threading.Thread(target=run_flask, daemon=True)
            flask_thread.start()
            start_bot_polling()
