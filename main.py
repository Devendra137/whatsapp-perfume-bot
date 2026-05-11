import os
import time
import requests
import gspread
from rapidfuzz import fuzz, process
from google import genai
from fastapi import FastAPI, Request, Query, HTTPException
from dotenv import load_dotenv
import json
from google.oauth2.service_account import Credentials

load_dotenv()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
OWNER_PHONE = os.getenv("OWNER_PHONE")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
BNIB_SHEET_ID = os.getenv("BNIB_SHEET_ID")
DECANT_SHEET_ID = os.getenv("DECANT_SHEET_ID")

print(f"DEBUG TOKEN: {str(WHATSAPP_TOKEN)[:15]}...")

# Initialize Gemini
client = genai.Client(api_key=GEMINI_API_KEY)

# Initialize Google Sheets — two separate files


service_account_info = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT"))
creds = Credentials.from_service_account_info(
    service_account_info,
    scopes=[
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
)
gc = gspread.authorize(creds)

bnib_sheet = gc.open_by_key(BNIB_SHEET_ID).sheet1
decant_sheet = gc.open_by_key(DECANT_SHEET_ID).sheet1

# -------------------------------------------------------
# CACHE — refreshes every 5 mins to avoid rate limits
# -------------------------------------------------------
_cache = {"bnib": [], "decants": [], "last_updated": 0}
CACHE_TTL = 300

UNAVAILABLE_MARKERS = {"---", "—", ""}

def is_unavailable(val: str) -> bool:
    val = val.strip()
    if val in UNAVAILABLE_MARKERS:
        return True
    if val == "-":           # single dash
        return True
    if "❌" in val:          # catches "740❌", "❌", "240❌" etc
        return True
    return False

def refresh_cache():
    now = time.time()
    if now - _cache["last_updated"] > CACHE_TTL:
        try:
            _cache["bnib"] = bnib_sheet.get_all_records()
            _cache["decants"] = decant_sheet.get_all_records()
            _cache["last_updated"] = now
            print("✅ Sheet cache refreshed")
        except Exception as e:
            print(f"❌ Cache refresh error: {e}")

def search_inventory(query: str) -> str:
    """
    Fuzzy searches both sheets for the perfume name.
    Returns a context string to inject into the Gemini prompt.
    """
    refresh_cache()
    results = []

    # --- Search BNIB sheet ---
    # Headers: brand | perfume | prices | inspired by
    bnib_names = [
        f"{r.get('BRAND', '')} {r.get('PERFUME', '')}".strip()
        for r in _cache["bnib"]
    ]
    bnib_match = process.extractOne(
        query, bnib_names, scorer=fuzz.partial_ratio, score_cutoff=65
    )
    if bnib_match:
        idx = bnib_names.index(bnib_match[0])
        r = _cache["bnib"][idx]
        price = str(r.get("PRICE", "")).strip()
        inspired = r.get("INSPIRE BY", "")

        if is_unavailable(price):
            results.append(
                f"BNIB | {bnib_match[0]} (inspired by {inspired}) | OUT OF STOCK"
            )
        else:
            results.append(
                f"BNIB | {bnib_match[0]} (inspired by {inspired}) | Price: {price} | IN STOCK"
            )

    # --- Search Decant sheet ---
    # Headers: decants | 8ml price | 20ml price
    decant_names = [
        str(r.get("Decants", "")).strip()
        for r in _cache["decants"]
    ]
    decant_match = process.extractOne(
        query, decant_names, scorer=fuzz.partial_ratio, score_cutoff=65
    )
    if decant_match:
        idx = decant_names.index(decant_match[0])
        r = _cache["decants"][idx]
        p8  = str(r.get("8ml Price",  "")).strip()
        p20 = str(r.get("20ml Price", "")).strip()

        avail_8  = not is_unavailable(p8)
        avail_20 = not is_unavailable(p20)

        if avail_8 or avail_20:
            parts = []
            if avail_8:  parts.append(f"8ml: {p8}")
            if avail_20: parts.append(f"20ml: {p20}")
            results.append(
                f"Decant | {decant_match[0]} | {', '.join(parts)} | IN STOCK"
            )
        else:
            results.append(
                f"Decant | {decant_match[0]} | OUT OF STOCK"
            )

    return "\n".join(results) if results else ""


# -------------------------------------------------------
# SYSTEM PROMPT
# -------------------------------------------------------
SYSTEM_PROMPT = """
You are Zara, a human shop assistant at a premium perfume boutique. You text customers on WhatsApp like a real person — casual, warm, and genuinely knowledgeable about fragrances.

FORMATTING RULES (strict):
- NO bullet points, NO bold text. Ever.
- Numbered lists are ONLY allowed when recommending multiple perfumes. Nowhere else.
- Everything else is plain flowing sentences.
- Max ONE emoji per message, or none at all.
- Never say "Great question!", "Absolutely!", "Hope that helps!", "Of course!" or any corporate filler. Ever.
- Never end with a sign-off line. Just stop naturally.

STOCK & PRICE RULES:
- If INVENTORY CONTEXT is provided below, use it to mention price and availability naturally in your reply.
- If the item is OUT OF STOCK, say so honestly but casually. Suggest a decant alternative if BNIB is out, or vice versa.
- If no inventory context is provided, do NOT make up prices or stock status. Just answer normally.
- Mention prices conversationally, not like a price tag. Example: "the 8ml decant is X, pretty good deal honestly"

RESPONSE TYPES:

1. RECOMMENDATIONS (when asked "suggest me", "what should I try", "perfumes for X"):
   - Start with one short casual line, then list at least 5 perfumes.
   - Each perfume on a new line, numbered, with a dash and one casual sentence about it.
   - After the list, add one line of your personal opinion on which you'd pick.

   GOOD EXAMPLE:
   "for summer i'd go with something like these

   1. Nishane Hacivat - green and fresh, really hard to beat in heat
   2. Sedley by Parfums de Marly - minty citrus, super clean
   3. Rasasi Hawas - leans sweet but works well in warm weather
   4. Mancera Cedrat Boise - fruity and light, very safe choice
   5. Amouage Reflection Man - smells expensive without trying too hard

   personally i always recommend Hacivat first, it just works on everyone"

   BAD EXAMPLE (never do this):
   "Getting into summer scents is such a great idea! Nishane Hacivat is amazing, it's a wonderfully
   green chypre. Rasasi Hawas is a fun aquatic choice that screams summer fun. Hope that helps!"

2. COMPARISON (when asked "which is better", "compare X and Y"):
   - Compare like you're talking to a friend, not writing a review.
   - Naturally cover: scent profile, longevity, occasion, and who it suits.
   - No lists, no tables. Just casual flowing sentences. 4-6 lines max.

3. SINGLE PERFUME QUERY (when asked "how is X", "tell me about X", "is X good"):
   - 3-5 lines max. Cover: what it smells like, who it's for, one honest opinion.
   - Be real — if it's overhyped, say so nicely.

4. GENERAL CHAT:
   - 2-3 sentences max. Sound like a real person.

PERSONALITY:
- You know perfumes deeply but never show off about it.
- Be honest and slightly opinionated — say what YOU would pick and why.
- Sound like a knowledgeable friend, not a salesperson.
- Never offer to handle payments, orders, or bookings — redirect those to a human.
"""


# -------------------------------------------------------
# APP
# -------------------------------------------------------
app = FastAPI()
BOT_ACTIVE = True


def send_whatsapp_message(to_number: str, text: str):
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": text}
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code != 200:
        print(f"Failed to send message: {response.text}")


def classify_intent(text: str) -> str:
    handoff_keywords = ["buy", "pay", "order", "purchase", "human", "agent", "refund", "complaint"]
    if any(keyword in text.lower() for keyword in handoff_keywords):
        return "handoff"
    return "bot"


@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token")
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook")
async def receive_message(request: Request):
    global BOT_ACTIVE
    body = await request.json()

    try:
        if body.get("object") == "whatsapp_business_account":
            entry = body.get("entry", [])[0]
            changes = entry.get("changes", [])[0]
            value = changes.get("value", {})

            if "messages" in value:
                message = value["messages"][0]
                sender_phone = message.get("from")
                message_type = message.get("type")

                if message_type == "text":
                    text = message["text"]["body"]
                    print(f"[{sender_phone}] says: {text}")

                    # Owner controls
                    if sender_phone == OWNER_PHONE:
                        if text.strip().lower() == "#pause":
                            BOT_ACTIVE = False
                            send_whatsapp_message(OWNER_PHONE, "⏸️ Bot is now PAUSED. You are in manual mode.")
                            return {"status": "ok"}
                        elif text.strip().lower() == "#resume":
                            BOT_ACTIVE = True
                            send_whatsapp_message(OWNER_PHONE, "▶️ Bot is now ACTIVE. AI is taking over.")
                            return {"status": "ok"}

                    if not BOT_ACTIVE:
                        print("Bot is paused. Ignoring message.")
                        return {"status": "ok"}

                    # Classify intent
                    intent = classify_intent(text)

                    if intent == "handoff":
                        reply_text = "i'll get a human to help you with that, they'll message you shortly"
                        send_whatsapp_message(OWNER_PHONE, f"🚨 HANDOFF: Customer {sender_phone} wants to buy/talk to human.\nMessage: {text}")

                    else:
                        # Search inventory
                        inventory_context = search_inventory(text)

                        if inventory_context:
                            prompt = f"""{SYSTEM_PROMPT}

INVENTORY CONTEXT (use this to answer the customer):
{inventory_context}

Customer says: {text}
Respond as Zara:"""
                        else:
                            prompt = f"""{SYSTEM_PROMPT}

Customer says: {text}
Respond as Zara:"""

                        response = client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=prompt,
                        )
                        reply_text = response.text

                    send_whatsapp_message(sender_phone, reply_text)

    except IndexError:
        pass
    except Exception as e:
        print(f"Error: {e}")

    return {"status": "ok"}

# --- NEW: UPTIME ROBOT ENDPOINT ---
@app.get("/health")
@app.head("/health")
def health_check():
    """Endpoint for UptimeRobot to ping every 14 mins to keep Render awake."""
    return {"status": "alive", "bot_active": BOT_ACTIVE}