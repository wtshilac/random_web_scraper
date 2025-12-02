import requests
import smtplib
import os
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from supabase import create_client, Client
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---
HALF_SUMO_URL = "https://halfsumo.com/collections/jiu-jitsu/products.json?limit=250"
HALF_SUMO_KEYWORD = "belt"

ARCTERYX_URL = "https://arcteryx.com/us/en/shop/bird-head-toque"
ARCTERYX_PRODUCT_NAME = "Bird Head Toque"
ARCTERYX_VARIANT = "Orca"
ARCTERYX_ID = "bird-head-toque-orca" # Unique ID for our DB

# --- SUPABASE CONFIG ---
SUPABASE_URL = "https://ewkayuxldehgmehrinyy.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Initialize Client
if not SUPABASE_KEY:
    print("CRITICAL ERROR: SUPABASE_KEY not found in environment variables.")
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- NOTIFICATION SETTINGS ---
# Email
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL")

# Discord
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

def load_existing_ids():
    """Loads all existing product IDs from Supabase to check for duplicates."""
    try:
        response = supabase.table("seen_items").select("id").execute()
        return {str(record['id']) for record in response.data}
    except Exception as e:
        print(f"Error loading history from Supabase: {e}")
        return set()

def save_all_belts(items):
    """Upserts (Inserts or Updates) ALL belt items to the database."""
    if not items:
        return

    data_to_upsert = []
    for item in items:
        # Only save Half Sumo items (which have numeric IDs)
        # We skip Arc'teryx items to avoid database ID conflicts
        if 'arcteryx' in item.get('tags', []):
            continue

        variants = item.get('variants', [])
        price = variants[0].get('price') if variants else "0.00"

        data_to_upsert.append({
            "id": item['id'],
            "title": item['title'],
            "price": price,
        })
    
    try:
        if data_to_upsert:
            supabase.table("seen_items").upsert(data_to_upsert).execute()
            print(f"Successfully upserted {len(data_to_upsert)} items to Supabase.")
    except Exception as e:
        print(f"Error saving to Supabase: {e}")

def check_arcteryx_stock():
    """
    Checks Arc'teryx website for specific variant stock using HTML analysis.
    Returns a list with the item IF it is newly in stock.
    """
    print(f"Checking Arc'teryx for '{ARCTERYX_VARIANT}'...")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9'
    }

    is_in_stock = False

    try:
        response = requests.get(ARCTERYX_URL, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 1. Check Global Stock Status (Structured Data check)
        # We look for the JSON string, but a simpler check based on your instructions
        # is to verify if "OutOfStock" is explicitly preventing purchase globally.
        # However, checking the specific button is more reliable for variants.
        
        # 2. Check the specific Variant Button
        # Logic: Find <fieldset>, look for button with 'Orca', check if class has 'no--stock'
        variant_found = False
        fieldset = soup.find('fieldset')
        
        if fieldset:
            buttons = fieldset.find_all('button')
            for btn in buttons:
                # Check button text or aria-label for "Orca"
                btn_text = btn.get_text() or btn.get('aria-label', '')
                
                if ARCTERYX_VARIANT.lower() in btn_text.lower():
                    variant_found = True
                    classes = btn.get('class', [])
                    
                    # Logic: If 'no--stock' is NOT present, it is in stock
                    if 'no--stock' not in classes:
                        is_in_stock = True
                        print(f"Variant '{ARCTERYX_VARIANT}' appears to be IN STOCK.")
                    else:
                        is_in_stock = False
                        print(f"Variant '{ARCTERYX_VARIANT}' is Out of Stock (has no--stock class).")
                    break
        
        if not variant_found:
            print(f"Warning: Could not find button for variant '{ARCTERYX_VARIANT}'. Layout might have changed.")

        # --- DB SYNC LOGIC ---
        # We now check our new 'arcteryx_tracker' table to see if state changed.
        
        # 1. Get previous state
        prev_in_stock = False
        try:
            res = supabase.table("arcteryx_tracker").select("in_stock").eq("variant_id", ARCTERYX_ID).execute()
            if res.data:
                prev_in_stock = res.data[0]['in_stock']
        except Exception as e:
            print(f"Error fetching Arc'teryx DB state: {e}")

        # 2. Update DB with current state
        upsert_data = {
            "variant_id": ARCTERYX_ID,
            "product_name": ARCTERYX_PRODUCT_NAME,
            "variant_name": ARCTERYX_VARIANT,
            "in_stock": is_in_stock,
            "last_checked": datetime.now().isoformat()
        }
        supabase.table("arcteryx_tracker").upsert(upsert_data).execute()

        # 3. Decide to Alert
        # Alert ONLY if: Currently In Stock AND (Previously Out of Stock OR First run)
        if is_in_stock and not prev_in_stock:
            return [{
                'id': ARCTERYX_ID,
                'title': f"🔥 RESTOCK: {ARCTERYX_PRODUCT_NAME} ({ARCTERYX_VARIANT})",
                'variants': [{'price': 'Check Site'}],
                'handle': 'bird-head-toque',
                'link': ARCTERYX_URL,
                'tags': ['arcteryx']
            }]

    except Exception as e:
        print(f"Arc'teryx Check Failed: {e}")
    
    return []

def send_email_notification(new_items):
    """Sends an email with the list of NEW items found."""
    if not (SENDER_EMAIL and SENDER_PASSWORD and RECEIVER_EMAIL):
        return

    print("Sending Email notification...")
    subject = f"Alert: {len(new_items)} Items of Interest Found!"
    
    body = "The following items were found:\n\n"
    for item in new_items:
        title = item.get('title')
        price = item.get('variants', [{}])[0].get('price', 'N/A')
        
        # Logic to handle different URL structures
        if 'link' in item:
            link = item['link']
        else:
            link = f"https://halfsumo.com/products/{item.get('handle')}"
            
        body += f"- {title} (${price})\n  Link: {link}\n\n"

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
        server.quit()
        print("Email notification sent.")
    except Exception as e:
        print(f"Failed to send email: {e}")

def send_discord_notification(new_items):
    """Sends a rich Discord notification."""
    if not DISCORD_WEBHOOK_URL:
        return

    print(f"Sending Discord notification...")
    
    fields = []
    for item in new_items:
        title = item.get('title')
        price = item.get('variants', [{}])[0].get('price', 'N/A')
        
        if 'link' in item:
            link = item['link']
        else:
            link = f"https://halfsumo.com/products/{item.get('handle')}"
        
        fields.append({
            "name": f"{title} - ${price}",
            "value": f"[View Product]({link})",
            "inline": False
        })

    payload = {
        "content": "🚨 **Stock Alert!**",
        "embeds": [
            {
                "title": f"Found {len(new_items)} items of interest",
                "color": 5763719, # Greenish/Blue
                "fields": fields[:25], 
                "footer": { "text": "Stock Monitor Bot" }
            }
        ]
    }

    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload)
        print("Discord notification sent.")
    except Exception as e:
        print(f"Failed to send Discord notification: {e}")

def main():
    print(f"[{datetime.now()}] Starting Stock Check...")
    
    found_items = []

    # --- 1. HALF SUMO CHECK ---
    print(f"Checking Half Sumo for '{HALF_SUMO_KEYWORD}'...")
    try:
        response = requests.get(HALF_SUMO_URL, timeout=10)
        response.raise_for_status()
        products = response.json().get("products", [])
        
        existing_ids = load_existing_ids()
        
        # Check for NEW belts
        for product in products:
            title = product.get("title", "").lower()
            str_id = str(product.get("id"))
            
            # Save ALL belts to DB to keep prices updated
            if HALF_SUMO_KEYWORD in title:
                save_all_belts([product]) # Upsert logic
                
                # Only alert if it's NEW (not in DB)
                if str_id not in existing_ids:
                    found_items.append(product)
                    
    except Exception as e:
        print(f"Half Sumo API Error: {e}")

    # --- 2. ARCTERYX CHECK ---
    # This now checks the new table and only alerts on status change
    arcteryx_items = check_arcteryx_stock()
    found_items.extend(arcteryx_items)

    # --- 3. NOTIFICATIONS ---
    if found_items:
        print(f"Total interesting items found: {len(found_items)}")
        send_email_notification(found_items)
        send_discord_notification(found_items)
    else:
        print("No new belts or Arc'teryx stock found.")

if __name__ == "__main__":
    main()