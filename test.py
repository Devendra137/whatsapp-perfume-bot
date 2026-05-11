import gspread
import os
from dotenv import load_dotenv

load_dotenv()

gc = gspread.service_account(filename="service_account.json")

bnib = gc.open_by_key(os.getenv("BNIB_SHEET_ID")).sheet1
decant = gc.open_by_key(os.getenv("DECANT_SHEET_ID")).sheet1

print("BNIB headers:", bnib.row_values(1))
print("Decant headers:", decant.row_values(1))