import requests
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from supabase import create_client, Client

# --- CONFIGURATION ---
URL = "https://halfsumo.com/collections/jiu-jitsu/products.json?limit=250"
KEYWORD = "belt"

# --- SUPABASE CONFIG ---
SUPABASE_URL = "https://ewkayuxldehgmehrinyy.supabase.co"
# Using the Service Role Key allows bypassing RLS policies if needed
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImV3a2F5dXhsZGVoZ21laHJpbnl5Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2NDYyNDY4NiwiZXhwIjoyMDgwMjAwNjg2fQ.fd3uFsOLGeWzdq2DfxQhnatOxrPpIqhYD8XqxmAgH7Q"

# Initialize Client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- EMAIL SETTINGS ---
# Pulls from GitHub Actions Secrets (or system env vars)
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "your_email@gmail.com")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "your_app_password")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "your_email@gmail.com")

def load_existing_ids():
    """Loads all existing product IDs from Supabase to check for duplicates."""
    try:
        # We only need the IDs to determine what is 'new'
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
        # Extract price safely (Shopify structure)
        variants = item.get('variants', [])
        price = variants[0].get('price') if variants else "0.00"

        data_to_upsert.append({
            "id": item['id'],       # Primary Key
            "title": item['title'],
            "price": price,
            # 'created_at' will auto-fill on insert, or stay same on update depending on DB config. 
            # If you want to track 'last_seen', you could add a column for that.
        })
    
    try:
        # .upsert() will insert new records OR update existing ones if the ID matches
        supabase.table("seen_items").upsert(data_to_upsert).execute()
        print(f"Successfully upserted {len(items)} belt items to Supabase.")
    except Exception as e:
        print(f"Error saving to Supabase: {e}")

def send_notification(new_items):
    """Sends an email with the list of NEW items found."""
    subject = f"Alert: {len(new_items)} New Belts Found!"
    
    body = "New belts found on Half Sumo:\n\n"
    for item in new_items:
        title = item.get('title')
        handle = item.get('handle')
        price = item.get('variants', [{}])[0].get('price', 'N/A')
        link = f"https://halfsumo.com/products/{handle}"
        
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

def main():
    print(f"[{datetime.now()}] Checking Half Sumo for '{KEYWORD}'...")

    # 1. Fetch Data from Shopify
    try:
        response = requests.get(URL, timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"API Error: {e}")
        return

    products = data.get("products", [])
    
    # 2. Filter for Belts
    all_belt_items = []
    for product in products:
        title = product.get("title", "").lower()
        if KEYWORD in title:
            all_belt_items.append(product)

    if not all_belt_items:
        print("No belt items found on the website.")
        return

    # 3. Check against Database
    existing_ids = load_existing_ids()
    new_finds = []

    for item in all_belt_items:
        str_id = str(item['id'])
        if str_id not in existing_ids:
            new_finds.append(item)

    # 4. Handle Actions
    # Always save/update ALL found belts to ensure DB is current
    save_all_belts(all_belt_items)

    # Only email if we actually found something NEW
    if new_finds:
        print(f"Found {len(new_finds)} NEW items! Sending email...")
        send_notification(new_finds)
    else:
        print("No NEW belts found (database updated with current stock).")

if __name__ == "__main__":
    main()