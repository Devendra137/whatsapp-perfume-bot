import json, os
from dotenv import load_dotenv
load_dotenv()

info = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT"))
print(info["client_email"])