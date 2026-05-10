import os
import requests
from google import genai  # <-- NEW SDK IMPORT
from fastapi import FastAPI, Request, Query, HTTPException
from dotenv import load_dotenv

# Load the environment variables FIRST
load_dotenv()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
OWNER_PHONE = os.getenv("OWNER_PHONE")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

print(f"DEBUG TOKEN: {str(WHATSAPP_TOKEN)[:15]}...")

# Initialize the NEW Gemini client
client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """
You are a helpful and polite customer service assistant for a premium perfume store.
Keep your answers brief, friendly, and formatted for WhatsApp (use emojis, bullet points).
You provide information about perfumes, notes, and recommendations. 
Do not handle payments or orders directly.
"""

app = FastAPI()
BOT_ACTIVE = True

def send_whatsapp_message(to_number: str, text: str):
    """Sends a text message back to the user via Meta API."""
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
    """
    Simple keyword-based intent classifier. 
    Returns 'handoff' if the user wants to buy or talk to a human.
    """
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
    global BOT_ACTIVE # <-- Allow modifying the global state
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
                    
                    # --- NEW: OWNER CONTROLS ---
                    if sender_phone == OWNER_PHONE:
                        if text.strip().lower() == "#pause":
                            BOT_ACTIVE = False
                            send_whatsapp_message(OWNER_PHONE, "⏸️ Bot is now PAUSED. You are in manual mode.")
                            return {"status": "ok"}
                        elif text.strip().lower() == "#resume":
                            BOT_ACTIVE = True
                            send_whatsapp_message(OWNER_PHONE, "▶️ Bot is now ACTIVE. AI is taking over.")
                            return {"status": "ok"}
                    
                    # --- NEW: CHECK IF BOT IS PAUSED ---
                    if not BOT_ACTIVE:
                        print("Bot is paused. Ignoring message.")
                        return {"status": "ok"}
                    
                    # 1. Classify the intent
                    intent = classify_intent(text)
                    
                    if intent == "handoff":
                        reply_text = "I'll transfer you to a human agent to complete your order. They will message you shortly! 🛍️"
                        
                        # --- NEW: Notify Owner ---
                        send_whatsapp_message(OWNER_PHONE, f"🚨 HANDOFF ALERT: Customer {sender_phone} wants to buy/talk to human.")
                        
                    else:
                        # 3. Generate response
                        prompt = f"{SYSTEM_PROMPT}\nCustomer says: {text}\nRespond as the bot:"
                        response = client.models.generate_content(
                            model='gemini-2.5-flash',
                            contents=prompt,
                        )
                        reply_text = response.text
                    
                    # 4. Send the reply back
                    send_whatsapp_message(sender_phone, reply_text)
                    
    except IndexError:
        pass
    except Exception as e:
        print(f"Error: {e}")

    return {"status": "ok"}

# --- NEW: UPTIME ROBOT ENDPOINT ---
@app.get("/health")
def health_check():
    """Endpoint for UptimeRobot to ping every 14 mins to keep Render awake."""
    return {"status": "alive", "bot_active": BOT_ACTIVE}