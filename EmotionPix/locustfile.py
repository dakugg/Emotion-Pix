from locust import HttpUser, TaskSet, task, between
import os
import json
import requests

# ⚠️ WARNING: Never expose credentials in production
SUPABASE_URL = "https://your-supabase-url.supabase.co"
SUPABASE_API_KEY = "your-supabase-api-key"
EMAIL = "your@email.com"
PASSWORD = "yourpassword"

# Shared access token for all users
ACCESS_TOKEN = None

def get_access_token_once():
    global ACCESS_TOKEN
    if ACCESS_TOKEN is None:
        print("🔐 Logging in once to get access token...")
        res = requests.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            json={"email": EMAIL, "password": PASSWORD},
            headers={
                "apikey": SUPABASE_API_KEY,
                "Content-Type": "application/json"
            }
        )
        if res.status_code == 200:
            ACCESS_TOKEN = res.json().get("access_token")
            print("✅ Login successful")
        else:
            print(f"❌ Login failed: {res.status_code}", res.text)
    return ACCESS_TOKEN

class UserBehavior(TaskSet):
    def on_start(self):
        # Each user gets a shared token (avoid rate-limiting)
        self.access_token = get_access_token_once()

    def auth_headers(self):
        return {
            "Authorization": f"Bearer {self.access_token}"
        } if self.access_token else {}

    @task
    def visit_home(self):
        with self.client.get("/home", headers=self.auth_headers(), catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"❌ Failed to load /home: {response.status_code}")

class WebsiteUser(HttpUser):
    tasks = [UserBehavior]
    wait_time = between(1, 3)
