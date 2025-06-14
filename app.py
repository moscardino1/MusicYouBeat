from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import os
import json
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
import requests
import re
import sqlalchemy.exc

load_dotenv()

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('POSTGRES_URL_NON_POOLING')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.urandom(24)

# Add SQLAlchemy connection pool settings
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 10,  # Maximum number of connections to keep
    'pool_timeout': 30,  # Seconds to wait before giving up on getting a connection
    'pool_recycle': 1800,  # Recycle connections after 30 minutes
    'pool_pre_ping': True,  # Enable connection health checks
    'max_overflow': 5  # Maximum number of connections that can be created beyond pool_size
}

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # Add relationship with bets
    bets = db.relationship('MYB_Bet', backref='user', lazy=True)
    bet_history = db.relationship('MYB_BetHistory', backref='user', lazy=True)

class MYB_Market(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    youtube_url = db.Column(db.String(200), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    target_views = db.Column(db.Integer, nullable=False)
    current_views = db.Column(db.Integer, default=0)
    deadline = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    status = db.Column(db.String(20), default='active')  # active, ended, cancelled
    total_yes_bets = db.Column(db.Integer, default=0)
    total_no_bets = db.Column(db.Integer, default=0)
    total_volume = db.Column(db.Integer, default=0)
    # Add relationship with bets
    bets = db.relationship('MYB_Bet', backref='market', lazy=True)
    bet_history = db.relationship('MYB_BetHistory', backref='market', lazy=True)

class MYB_Bet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('myb__user.id'), nullable=False)
    market_id = db.Column(db.Integer, db.ForeignKey('myb__market.id'), nullable=False)
    prediction = db.Column(db.Boolean, nullable=False)  # True = Yes, False = No
    amount = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class MYB_BetHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('myb__user.id'), nullable=False)
    market_id = db.Column(db.Integer, db.ForeignKey('myb__market.id'), nullable=False)
    prediction = db.Column(db.Boolean, nullable=False)  # True = Yes, False = No
    amount = db.Column(db.Integer, nullable=False)
    outcome = db.Column(db.Boolean, nullable=False)  # True = Won, False = Lost
    tokens_won = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

with app.app_context():
    # Drop all tables and recreate them
    # db.drop_all() # Uncomment this to drop all tables and recreate them
    db.create_all()
    print("DB Initialized")

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
    
    market = MYB_Market.query.get_or_404(market_id)
    
    if market.status != 'active':
        flash('This market is no longer accepting bets')
        return redirect(url_for('market', market_id=market_id))
    
    if current_user.tokens < amount:
        flash('Not enough tokens')
        return redirect(url_for('market', market_id=market_id))
        
    bet = MYB_Bet(user_id=current_user.id, market_id=market_id,
                  prediction=prediction, amount=amount)
    
    # Update market totals
    if prediction:
        market.total_yes_bets += amount
    else:
        market.total_no_bets += amount
    market.total_volume = market.total_yes_bets + market.total_no_bets
    
    # Update user's tokens
    current_user.tokens -= amount
    
    try:
        db.session.add(bet)
        db.session.commit()
        flash('Bet placed successfully!')
    except Exception as e:
        db.session.rollback()
        flash('Error placing bet. Please try again.')
        print(f"Error placing bet: {str(e)}")
    
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
    
    # Parse the datetime directly as UTC
    deadline = datetime.strptime(request.form.get('deadline'), '%Y-%m-%dT%H:%M')
    deadline = deadline.replace(tzinfo=timezone.utc)
    
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
    
    bet_history = MYB_BetHistory.query.filter_by(
        user_id=current_user.id
    ).order_by(MYB_BetHistory.created_at.desc()).all()
    
    return render_template('profile.html', 
                         active_bets=active_bets,
                         bet_history=bet_history,
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
        print("\n=== Starting market views update ===")
        active_markets = MYB_Market.query.filter_by(status='active').all()
        print(f"Found {len(active_markets)} active markets")
        
        for market in active_markets:
            print(f"\nProcessing market {market.id} - {market.title}")
            print(f"Current status: {market.status}")
            print(f"Deadline: {market.deadline} UTC")
            print(f"Current time: {datetime.utcnow()} UTC")
            print(f"Time until deadline: {(market.deadline - datetime.utcnow()).total_seconds()} seconds")
            
            views = get_youtube_views(market.youtube_url)
            if views is not None:
                print(f"Updated views: {views} (target: {market.target_views})")
                market.current_views = views
                market.updated_at = datetime.utcnow()
                
                # Check if market should be ended
                if views >= market.target_views:
                    print(f"Market {market.id} ended due to reaching target views")
                    market.status = 'ended'
                    process_market_bets(market)
                elif market.deadline <= datetime.utcnow():
                    print(f"Market {market.id} ended due to deadline")
                    market.status = 'ended'
                    process_market_bets(market)
                else:
                    print(f"Market {market.id} still active")
            else:
                print(f"Failed to fetch views for market {market.id}")
                
        try:
            db.session.commit()
            print("\nSuccessfully committed all changes")
        except Exception as e:
            print(f"\nError committing changes: {str(e)}")
            db.session.rollback()
        print("=== Finished market views update ===\n")

def process_market_bets(market):
    # Calculate total volume and odds
    total_yes = sum(bet.amount for bet in market.bets if bet.prediction)
    total_no = sum(bet.amount for bet in market.bets if not bet.prediction)
    total_volume = total_yes + total_no
    
    # Update market totals
    market.total_yes_bets = total_yes
    market.total_no_bets = total_no
    market.total_volume = total_volume
    
    # Determine outcome (True if views >= target)
    outcome = market.current_views >= market.target_views
    
    # Process each bet
    for bet in market.bets:
        # Calculate winnings
        if bet.prediction == outcome:
            # Winner gets proportional share of losing side's tokens
            if bet.prediction:  # Yes bet
                winning_pool = total_no
                winning_share = (bet.amount / total_yes) if total_yes > 0 else 0
            else:  # No bet
                winning_pool = total_yes
                winning_share = (bet.amount / total_no) if total_no > 0 else 0
            
            tokens_won = int(bet.amount + (winning_pool * winning_share))
        else:
            tokens_won = 0
        
        # Create bet history record
        history = MYB_BetHistory(
            user_id=bet.user_id,
            market_id=market.id,
            prediction=bet.prediction,
            amount=bet.amount,
            outcome=(bet.prediction == outcome),
            tokens_won=tokens_won
        )
        
        # Update user's tokens
        user = db.session.get(MYB_User, bet.user_id)
        if bet.prediction == outcome:
            user.tokens += tokens_won
        
        db.session.add(history)
    
    # Delete the original bets
    for bet in market.bets:
        db.session.delete(bet)

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

@app.route('/debug/market/<int:market_id>')
@login_required
def debug_market(market_id):
    if not current_user.is_admin:
        return "Unauthorized", 403
        
    market = MYB_Market.query.get_or_404(market_id)
    current_time = datetime.utcnow()
    
    debug_info = {
        'market_id': market.id,
        'title': market.title,
        'status': market.status,
        'deadline': market.deadline.strftime('%Y-%m-%d %H:%M:%S UTC'),
        'current_time': current_time.strftime('%Y-%m-%d %H:%M:%S UTC'),
        'time_diff': (market.deadline - current_time).total_seconds(),
        'current_views': market.current_views,
        'target_views': market.target_views,
        'total_yes_bets': market.total_yes_bets,
        'total_no_bets': market.total_no_bets,
        'total_volume': market.total_volume,
        'last_updated': market.updated_at.strftime('%Y-%m-%d %H:%M:%S UTC')
    }
    
    return jsonify(debug_info)

@app.errorhandler(sqlalchemy.exc.OperationalError)
def handle_db_error(error):
    """Handle database connection errors"""
    db.session.rollback()  # Roll back any failed transaction
    flash('Database connection error. Please try again.', 'error')
    return redirect(url_for('index'))

@app.errorhandler(sqlalchemy.exc.SQLAlchemyError)
def handle_sqlalchemy_error(error):
    """Handle other SQLAlchemy errors"""
    db.session.rollback()  # Roll back any failed transaction
    flash('A database error occurred. Please try again.', 'error')
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True, port=5001)
