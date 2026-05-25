import os
import json
import logging
from curl_cffi import requests
from telebot import TeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime, timedelta

from locations import LOCATION_GEO_IDS, LOCATION_OPTIONS

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
REPO = os.environ.get("GITHUB_REPOSITORY")

bot = TeleBot(TOKEN) if TOKEN else None

def format_location_header(locations):
    loc_set = set(locations)
    if loc_set >= {"dublin-6-dublin", "dublin-6w-dublin"}:
        return " — Dublin 6/6W"
    parts = [LOCATION_OPTIONS[loc] for loc in locations if loc in LOCATION_OPTIONS]
    return f" — {', '.join(parts)}" if parts else ""

def load_json(filepath, default):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading {filepath}: {e}")
        return default

def save_json(filepath, data):
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving {filepath}: {e}")

def build_first_publish_date_range(date_range_days):
    if date_range_days is None:
        return None
    try:
        days = int(date_range_days)
    except (TypeError, ValueError):
        return None
    if days <= 0:
        return None
    since_ms = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
    return {"name": "firstPublishDate", "from": str(since_ms), "to": ""}

def get_daft_listings(state):
    url = "https://gateway.daft.ie/api/v2/ads/listings"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "brand": "daft",
        "platform": "web",
        "Origin": "https://www.daft.ie",
        "Referer": "https://www.daft.ie/",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    }

    locations = state.get("locations", ["dublin-6-dublin", "dublin-6w-dublin"])
    price_min = str(state.get("price_min", 1500))
    price_max = str(state.get("price_max", 1800))

    shape_ids = [LOCATION_GEO_IDS[loc] for loc in locations if loc in LOCATION_GEO_IDS]
    if not shape_ids:
        logger.error(f"No geo IDs found for locations: {locations}")
        return []

    payload = {
        "section": "residential-to-rent",
        "filters": [
            {"name": "adState", "values": ["published"]},
        ],
        "andFilters": [],
        "ranges": [
            {"name": "rentalPrice", "from": price_min, "to": price_max}
        ],
        "geoFilter": {
            "storedShapeIds": shape_ids,
            "geoSearchType": "STORED_SHAPES",
        },
        "paging": {"from": "0", "pagesize": "50"}
    }

    date_range = build_first_publish_date_range(state.get("date_range_days"))
    if date_range:
        payload["ranges"].append(date_range)

    try:
        response = requests.post(url, headers=headers, json=payload, impersonate="chrome120", timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("listings", [])
    except Exception as e:
        logger.error(f"Error fetching from Daft API: {e}")
        return []

def format_listing_message(listing, locations=None):
    title = listing.get("title", "Bilinmeyen Başlık")
    price = listing.get("price", "Bilinmeyen Fiyat")
    daft_url = "https://www.daft.ie" + listing.get("seoFriendlyPath", "")

    property_type = listing.get("propertyType", "Bilinmeyen Tip")
    beds = listing.get("numBedrooms", property_type)

    published = listing.get("publishDate", "")
    if published:
        try:
            pub_date = datetime.fromtimestamp(published / 1000)
            date_str = pub_date.strftime("%d %b %Y, %H:%M")
        except Exception:
            date_str = str(published)
    else:
        date_str = "Bilinmiyor"

    location_suffix = format_location_header(locations or [])
    msg = f"🏠 *YENİ İLAN{location_suffix}*\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"📍 {title}\n"
    msg += f"💶 {price}\n"
    msg += f"🏢 {property_type}\n"
    msg += f"🛏 {beds}\n"
    msg += f"📅 Yayın: {date_str}\n\n"
    msg += f"[🔗 Daft.ie'de Gör]({daft_url})"

    return msg

def extract_recent_data(listing):
    return {
        "id": str(listing.get("id", "")),
        "title": listing.get("title", "İlan"),
        "price": listing.get("price", "Fiyat Yok"),
        "url": "https://www.daft.ie" + listing.get("seoFriendlyPath", "")
    }

def send_telegram_notification(listing, ad_id, locations=None):
    if not bot or not CHAT_ID:
        logger.warning("Telegram bot token or chat ID not set.")
        return

    msg = format_listing_message(listing, locations)

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("⭐ Favori Ekle/Çıkar", callback_data=f"fav_{ad_id}"))

    try:
        # Try to get the main image
        media_list = listing.get("media", {}).get("images", [])
        if media_list and len(media_list) > 0:
            image_url = media_list[0].get("size600x600") or media_list[0].get("size300x225")
            if image_url:
                bot.send_photo(CHAT_ID, image_url, caption=msg, parse_mode="Markdown", reply_markup=markup)
                return

        # Fallback to text message if no image
        bot.send_message(CHAT_ID, msg, parse_mode="Markdown", reply_markup=markup, disable_web_page_preview=False)
    except Exception as e:
        logger.error(f"Error sending Telegram notification: {e}")
        # Final fallback
        try:
            bot.send_message(CHAT_ID, msg, parse_mode="Markdown", reply_markup=markup)
        except Exception as e2:
            logger.error(f"Failed to send fallback message: {e2}")

def commit_files_to_github(files_to_commit):
    """files_to_commit is a list of tuples: (filename, message)"""
    if not GITHUB_TOKEN or not REPO:
        logger.warning("GITHUB_TOKEN or GITHUB_REPOSITORY not set, skipping commit.")
        return

    import base64
    import requests as req # Standard requests for github api

    for filename, message in files_to_commit:
        url = f"https://api.github.com/repos/{REPO}/contents/{filename}"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }

        sha = None
        res = req.get(url, headers=headers)
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

        put_res = req.put(url, headers=headers, json=data)
        if put_res.status_code in [200, 201]:
            logger.info(f"Successfully committed {filename} to GitHub.")
        else:
            logger.error(f"Failed to commit {filename}: {put_res.text}")

def main():
    logger.info("Starting Daft.ie scan...")
    state = load_json("state.json", {"price_min": 1500, "price_max": 1800, "locations": ["dublin-6-dublin", "dublin-6w-dublin"], "auto_notify": True, "date_range_days": 30})

    # Download latest state from github just to be safe
    # But usually actions run with latest repo code anyway

    seen_ids = load_json("seen_ids.json", [])
    recent_listings = load_json("recent_listings.json", [])

    listings = get_daft_listings(state)
    logger.info(f"Found {len(listings)} listings from Daft.ie")

    # Update recent listings
    new_recent = []
    for item in listings[:20]: # Keep up to 20 recent
        listing = item.get("listing", {})
        if listing:
             new_recent.append(extract_recent_data(listing))

    recent_changed = False
    if json.dumps(new_recent) != json.dumps(recent_listings):
        save_json("recent_listings.json", new_recent)
        recent_changed = True

    if not state.get("auto_notify", True):
        logger.info("Auto notifications are disabled in state. Updating recent listings if needed, but not sending new alerts.")
        if recent_changed:
            commit_files_to_github([("recent_listings.json", "Update recent_listings.json from scanner")])
        return

    new_listings_found = False
    for item in listings:
        listing = item.get("listing", {})
        ad_id = str(listing.get("id", ""))

        if not ad_id:
            continue

        if ad_id not in seen_ids:
            logger.info(f"New listing found: {ad_id} - {listing.get('title')}")
            send_telegram_notification(listing, ad_id, state.get("locations", []))
            seen_ids.append(ad_id)
            new_listings_found = True

    files_to_commit = []
    if new_listings_found:
        save_json("seen_ids.json", seen_ids)
        files_to_commit.append(("seen_ids.json", "Update seen_ids.json from scanner"))

    if recent_changed and not any(f[0] == "recent_listings.json" for f in files_to_commit):
        files_to_commit.append(("recent_listings.json", "Update recent_listings.json from scanner"))

    if files_to_commit:
        commit_files_to_github(files_to_commit)
        logger.info(f"Updated and committed {len(files_to_commit)} files to GitHub.")
    else:
        logger.info("No new updates needed.")

if __name__ == "__main__":
    main()
