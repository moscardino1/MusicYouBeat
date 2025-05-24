from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
import json
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
import requests
import re

load_dotenv()

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('POSTGRES_URL_NON_POOLING')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.urandom(24)

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class MYB_User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    tokens = db.Column(db.Integer, default=100)
    is_admin = db.Column(db.Boolean, default=False)
    last_bonus = db.Column(db.DateTime)
    # Add relationship with bets
    bets = db.relationship('MYB_Bet', backref='user', lazy=True)

class MYB_Market(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    youtube_url = db.Column(db.String(200), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    target_views = db.Column(db.Integer, nullable=False)
    current_views = db.Column(db.Integer, default=0)
    deadline = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='active')  # active, ended, cancelled
    # Add relationship with bets
    bets = db.relationship('MYB_Bet', backref='market', lazy=True)

class MYB_Bet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('myb__user.id'), nullable=False)
    market_id = db.Column(db.Integer, db.ForeignKey('myb__market.id'), nullable=False)
    prediction = db.Column(db.Boolean, nullable=False)  # True = Yes, False = No
    amount = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
with app.app_context():
    # Create tables if they don't exist
    db.create_all()

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(MYB_User, int(user_id))

@app.route('/')
def index():
    markets = MYB_Market.query.filter_by(status='active').order_by(MYB_Market.created_at.desc()).all()
    return render_template('index.html', markets=markets)

@app.route('/market/<int:market_id>')
def market(market_id):
    market = MYB_Market.query.get_or_404(market_id)
    return render_template('market.html', market=market)

@app.route('/place_bet', methods=['POST'])
@login_required
def place_bet():
    market_id = request.form.get('market_id')
    prediction = request.form.get('prediction') == 'yes'
    amount = int(request.form.get('amount'))
    
    if current_user.tokens < amount:
        flash('Not enough tokens')
        return redirect(url_for('market', market_id=market_id))
        
    bet = MYB_Bet(user_id=current_user.id, market_id=market_id,
                  prediction=prediction, amount=amount)
    current_user.tokens -= amount
    
    db.session.add(bet)
    db.session.commit()
    
    return redirect(url_for('market', market_id=market_id))

@app.route('/admin')
@login_required
def admin():
    if not current_user.is_admin:
        return redirect(url_for('index'))
    return render_template('admin.html')

@app.route('/leaderboard')
def leaderboard():
    users = MYB_User.query.order_by(MYB_User.tokens.desc()).limit(10).all()
    return render_template('leaderboard.html', users=users)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = MYB_User.query.filter_by(username=username).first()
        
        if user and user.password_hash == password:  # In production, use proper password hashing!
            login_user(user)
            return redirect(url_for('index'))
        flash('Invalid username or password')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/create', methods=['GET'])
@login_required
def create_market_page():
    return render_template('create_market.html')

@app.route('/create_market', methods=['POST'])
@login_required
def create_market():
    youtube_url = request.form.get('youtube_url')
    title = request.form.get('title')
    target_views = int(request.form.get('target_views'))
    deadline = datetime.strptime(request.form.get('deadline'), '%Y-%m-%dT%H:%M')
    
    # Get initial view count
    initial_views = get_youtube_views(youtube_url)
    if initial_views is None:
        flash('Could not fetch view count from YouTube. Please check the URL and try again.')
        return redirect(url_for('create_market_page'))
    
    market = MYB_Market(youtube_url=youtube_url,
                       title=title,
                       target_views=target_views,
                       deadline=deadline,
                       current_views=initial_views)
    
    db.session.add(market)
    db.session.commit()
    
    return redirect(url_for('admin'))

@app.route('/daily_bonus')
@login_required
def daily_bonus():
    if not current_user.last_bonus or \
       (datetime.utcnow() - current_user.last_bonus).days >= 1:
        current_user.tokens += 10
        current_user.last_bonus = datetime.utcnow()
        db.session.commit()
        flash('You received your daily bonus of 10 tokens!')
    else:
        flash('You already claimed your daily bonus today!')
    return redirect(url_for('index'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if MYB_User.query.filter_by(username=username).first():
            flash('Username already exists')
            return redirect(url_for('register'))
            
        user = MYB_User(username=username, 
                       password_hash=password,  # In production, use proper password hashing!
                       tokens=100)  # Starting tokens
        db.session.add(user)
        db.session.commit()
        
        login_user(user)
        return redirect(url_for('index'))
        
    return render_template('register.html')

@app.route('/profile')
@login_required
def profile():
    active_bets = MYB_Bet.query.join(MYB_Market).filter(
        MYB_Bet.user_id == current_user.id,
        MYB_Market.status == 'active'
    ).all()
    
    return render_template('profile.html', 
                         active_bets=active_bets,
                         timedelta=timedelta)

# Global cache for view counts
view_count_cache = {}

def extract_video_id(url):
    # Extract video ID from YouTube URL
    video_id = None
    if 'youtube.com/watch?v=' in url:
        video_id = url.split('watch?v=')[1].split('&')[0]
    elif 'youtu.be/' in url:
        video_id = url.split('youtu.be/')[1].split('?')[0]
    return video_id

def get_youtube_views(url):
    try:
        video_id = extract_video_id(url)
        if not video_id:
            print(f"Could not extract video ID from URL: {url}")
            return None
            
        # Check cache first
        if video_id in view_count_cache:
            last_fetch_time, cached_views = view_count_cache[video_id]
            if (datetime.utcnow() - last_fetch_time).total_seconds() < 600:  # 10 minutes
                print(f"Using cached views for {video_id}: {cached_views}")
                return cached_views
            
        print(f"Fetching views for video ID: {video_id}")
        response = requests.get(f'https://www.youtube.com/watch?v={video_id}')
        if response.status_code != 200:
            print(f"Failed to fetch video page. Status code: {response.status_code}")
            return None
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        
        # Try meta tags as fallback
        meta_tags = soup.find_all('meta')
        for tag in meta_tags:
            if tag.get('itemprop') == 'interactionCount':
                views = int(tag.get('content'))
                print(f"Found views in meta tags for {video_id}: {views}")
                # Cache the result
                view_count_cache[video_id] = (datetime.utcnow(), views)
                return views
                
        # Try to find viewCount in the page's JSON data
        view_pattern = r'"viewCount":\s*{\s*"videoViewCountRenderer":\s*{\s*"viewCount":\s*{\s*"simpleText":\s*"([\d,]+)\s+views?"'
        match = re.search(view_pattern, response.text)
        if match:
            view_str = match.group(1).replace(',', '')  # Remove commas from number
            views = int(view_str)
            print(f"Found views using JSON pattern for {video_id}: {views}")
            # Cache the result
            view_count_cache[video_id] = (datetime.utcnow(), views)
            return views
            
    except Exception as e:
        print(f"Error fetching views for {url}: {str(e)}")
        import traceback
        traceback.print_exc()
    return None

def update_market_views():
    with app.app_context():
        active_markets = MYB_Market.query.filter_by(status='active').all()
        for market in active_markets:
            views = get_youtube_views(market.youtube_url)
            if views is not None:
                market.current_views = views
                # Check if market should be ended
                if views >= market.target_views:
                    market.status = 'ended'
                elif market.deadline <= datetime.utcnow():
                    market.status = 'ended'
        db.session.commit()

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(func=update_market_views, trigger="interval", seconds=600)  # Run every 10 minutes
scheduler.start()

@app.route('/update_views')
@login_required
def update_views():
    if current_user.is_admin:
        update_market_views()
        flash('View counts updated')
    return redirect(url_for('admin'))

if __name__ == '__main__':
    app.run(debug=True, port=5001)
