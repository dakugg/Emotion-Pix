from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
import json
import os
from flask_session import Session
import supabase
import config
from werkzeug.security import generate_password_hash, check_password_hash
import requests
import cv2
import numpy as np
import sqlite3
from datetime import datetime, timedelta
from fer import FER
from email_validator import validate_email, EmailNotValidError
from supabase import SupabaseException
from gotrue.errors import AuthApiError
import time
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'default_secret_key')

USER_DATA_FILE = 'users.json'
supabase_client = supabase.create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
detector = FER()
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

def load_users():
    if os.path.exists(USER_DATA_FILE):
        with open(USER_DATA_FILE, 'r') as f:
            return json.load(f)
    return {}

DATABASE = 'movie_cache.db'

def init_db():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS movie_cache (
            genre TEXT PRIMARY KEY,
            movies TEXT,
            timestamp TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS search_cache (
            search_query TEXT PRIMARY KEY,
            results TEXT,
            timestamp TEXT
        )
    ''')
    conn.commit()
    conn.close()

def get_cached_movies(genre):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('SELECT movies, timestamp FROM movie_cache WHERE genre = ?', (genre,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        movies_json = row[0]
        timestamp = datetime.strptime(row[1], '%Y-%m-%d %H:%M:%S')
        if datetime.now() - timestamp < timedelta(hours=1):
            return json.loads(movies_json)
    return None

def store_cached_movies(genre, movies):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('''
        REPLACE INTO movie_cache (genre, movies, timestamp) 
        VALUES (?, ?, ?)
    ''', (genre, json.dumps(movies), datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()

def save_users(users):
    with open(USER_DATA_FILE, 'w') as f:
        json.dump(users, f)

def is_valid_email(email):
    try:
        validate_email(email)
        return True
    except EmailNotValidError:
        return False

def create_user(email, password):
    users = load_users()
    if email in users:
        return False
    hashed_password = generate_password_hash(password)
    users[email] = hashed_password
    save_users(users)
    return True

def validate_user(email, password):
    users = load_users()
    if email in users and check_password_hash(users[email], password):
        return True
    return False

@app.route('/resend_confirmation', methods=['POST'])
def resend_confirmation():
    email = request.form['email']
    try:
        response = supabase_client.auth.api.resend_confirmation(email)
        if "error" in response:
            flash("Error sending confirmation email: " + response["error"]["message"], "danger")
        else:
            flash("A new confirmation email has been sent. Please check your inbox.", "success")
    except supabase.exceptions.SupabaseAPIError as e:
        flash(f"Error: {e.message}", "danger")
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        if not is_valid_email(email):
            flash('Invalid email format. Please enter a valid email.', 'danger')
            return render_template('register.html')

        response = safe_supabase_sign_up(email, password)
        
        if response and "error" in response:
            flash("Error: " + response["error"]["message"], "danger")
        else:
            flash('Registration successful! Check your email to verify.', 'success')
            return redirect(url_for('login'))

    return render_template('register.html')

def safe_supabase_sign_up(email, password):
    retries = 3
    for _ in range(retries):
        try:
            return supabase_client.auth.sign_up({"email": email, "password": password})
        except SupabaseException as e:
            flash(f"Supabase Error: {str(e)}", 'danger')
            time.sleep(2)  
    flash('Failed to connect to the server. Please try again later.', 'danger')
    return None

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        if not email or not password:
            flash("Email and password are required!", "danger")
            return render_template('login.html')

        try:
            response = supabase_client.auth.sign_in_with_password({
                "email": email,
                "password": password
            })

            if hasattr(response, 'error') and response.error:
                error_message = response.error.get("message", "Unknown error")
                if "Email not confirmed" in error_message:
                    flash("Your email is not confirmed. Please check your inbox.", "warning")
                    return redirect(url_for('login'))  
                flash(f"Login failed: {error_message}", "danger")
            else:
                user = response.user 
                session['user'] = {
                    'id': user.id,
                    'email': user.email,
                    'user_metadata': user.user_metadata,
                }
                flash('Login successful!', 'success')
                return redirect(url_for('home'))

        except Exception as e:
            flash(f"An unexpected error occurred: {str(e)}", 'danger')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user', None) 
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

emotion_to_genre = {
    "happy": "Comedy",
    "sadness": "Drama",
    "anger": "Action",
    "surprise": "Adventure",
    "neutral": "Drama",
    "fear": "Horror"
}

RAPIDAPI_KEY = os.getenv('RAPIDAPI_KEY')
RAPIDAPI_HOST = os.getenv('RAPIDAPI_HOST')

def detect_emotion(image_data):
    nparr = np.frombuffer(image_data, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    emotion, score = detector.top_emotion(img)
    if emotion:
        return emotion
    return "neutral"  

def get_movie_recommendations(genre):
    cached_movies = get_cached_movies(genre)
    if cached_movies:
        return cached_movies

    url = "https://imdb236.p.rapidapi.com/imdb/search"
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST
    }
    params = {
        "type": "movie",
        "genre": genre,
        "rows": 100,
        "sortField": "startYear",
        "sortOrder": "DESC",
        "countriesOfOrigin": ["IN"],
        "spokenLanguages": ["hi"],
        "averageRatingFrom": "7",
        "averageRatingTo": "10",
        "numVotesFrom": "1000",
        "numVotesTo": "1000000",
        "startYearFrom": "1970",
        "startYearTo": "2025",
    }

    response = requests.get(url, headers=headers, params=params)
    movies = response.json().get('results', [])

    store_cached_movies(genre, movies.copy())
    return movies

@app.route('/detect_emotion', methods=['POST'])
def detect_emotion_from_image():
    image_file = request.files.get('image')
    if not image_file:
        return jsonify({'success': False, 'message': 'No image provided'}), 400
    if not image_file.filename.lower().endswith(('.png', '.jpg', '.jpeg')):  
        return jsonify({'success': False, 'message': 'Invalid image format. Only PNG, JPG, or JPEG allowed.'}), 400
    
    image_data = image_file.read()
    emotion = detect_emotion(image_data)
    if emotion:
        return jsonify({'success': True, 'emotion': emotion})
    else:
        return jsonify({'success': False, 'message': 'Emotion detection failed'})

@app.route('/get_movies', methods=['GET'])
def get_movies():
    emotion = request.args.get('emotion', 'happy')  
    genre = emotion_to_genre.get(emotion, 'Comedy')
    response = get_movie_recommendations(genre)
    movies = []

    for movie in response:
        description = movie.get('description', 'No description available.')
        primary_image = movie.get('primaryImage', None)
        trailer_url = movie.get('trailerUrl', None)
        if description and len(description) > 100: 
            description = description[:97] + '...' 
        if primary_image and description:
            movies.append({
                'primaryTitle': movie['primaryTitle'],
                'description': description,
                'primaryImage': primary_image,
                "trailerUrl": f"https://www.youtube.com/results?search_query={movie['primaryTitle']}+trailer"
            })
    return jsonify({'movies': movies})

def get_cached_search_results(search_query):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('SELECT results, timestamp FROM search_cache WHERE search_query = ?', (search_query,))
    row = cursor.fetchone()
    conn.close()

    if row:
        results_json = row[0]
        timestamp = datetime.strptime(row[1], '%Y-%m-%d %H:%M:%S')
        if datetime.now() - timestamp < timedelta(hours=1):
            return json.loads(results_json)
    return None

def store_cached_search_results(search_query, results):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('''
        REPLACE INTO search_cache (search_query, results, timestamp) 
        VALUES (?, ?, ?)
    ''', (search_query, json.dumps(results), datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()

@app.route('/search_movie', methods=['GET'])
def search_movie():
    search_query = request.args.get('query', '').strip()
    if not search_query:
        return jsonify({'movies': []})
    
    cached_results = get_cached_search_results(search_query)
    if cached_results:
        return jsonify({'movies': cached_results})

    url = "https://imdb236.p.rapidapi.com/imdb/search"
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST
    }

    params =  {
        "originalTitle": search_query,
        "type": "movie",
        "rows": "25",
        "sortOrder": "ASC",
        "sortField": "id",
    }

    response = requests.get(url, headers=headers, params=params)
    data = response.json()

    movies = []
    if 'results' in data:
        for movie in data['results']:
            description = movie.get('description', 'No description available.')
            primary_image = movie.get('primaryImage', None)
            trailer_url = movie.get('trailerUrl', None)

            if description and len(description) > 100:
                description = description[:97] + '...' 

            if primary_image and description:
                movies.append({
                    'primaryTitle': movie['primaryTitle'],
                    'description': description,
                    'primaryImage': primary_image,
                    'trailerUrl': trailer_url or f"https://www.youtube.com/results?search_query={movie['primaryTitle']}+trailer"
                })
    
    store_cached_search_results(search_query, movies)

    return jsonify({'movies': movies})

@app.route('/')
def home_check():
    if 'user' in session:
        return redirect("/home")
    else:
        return redirect('/login')

@app.route('/home')
def home():
    if 'user' not in session: 
        flash("You need to log in first.", "warning")
        return redirect(url_for('login'))
    return render_template('home.html', user=session['user'])

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)  
