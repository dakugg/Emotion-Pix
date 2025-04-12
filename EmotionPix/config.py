import os
from dotenv import load_dotenv

load_dotenv()  
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")
print("Supabase URL:", SUPABASE_URL)
print("Supabase Key:", SUPABASE_KEY)
