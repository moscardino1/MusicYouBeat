import sys
import os
import time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import pytest
from app import app, db
from datetime import datetime, timedelta
from flask import abort

@pytest.fixture(scope="module")
def test_context():
    now = int(time.time())
    test_user = f"test_{now}"
    admin_user = f"admin_{now}"
    title = f"Test Market {now}"
    youtube_url = "https://www.youtube.com/watch?v=XGtgSQDePig"
    target_views = 1001
    deadline = (datetime.utcnow() + timedelta(seconds=30)).strftime('%Y-%m-%dT%H:%M')
    return {
        'test_user': test_user,
        'admin_user': admin_user,
        'title': title,
        'youtube_url': youtube_url,
        'target_views': target_views,
        'deadline': deadline,
        'market_id': None
    }

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        with app.app_context():
            db.create_all()
        yield client
        # with app.app_context():
        #     db.drop_all()

def register_and_login(client, username, password, is_admin=False):
    # Register
    client.post('/register', data={'username': username, 'password': password}, follow_redirects=True)
    # Make admin if needed
    if is_admin:
        with client.application.app_context():
            from app import MYB_User
            user = db.session.query(MYB_User).filter_by(username=username).first()
            user.is_admin = True
            db.session.commit()
    # Login
    client.post('/login', data={'username': username, 'password': password}, follow_redirects=True)

# 1. Register and login as admin
def test_register_and_login_admin(client, test_context):
    register_and_login(client, test_context['admin_user'], test_context['admin_user'], is_admin=True)
    # Try accessing admin page to confirm admin status
    response = client.get('/admin', follow_redirects=True)
    assert b"Admin Panel" in response.data

# 2. Create a market
def test_create_market(client, test_context):
    register_and_login(client, test_context['admin_user'], test_context['admin_user'], is_admin=True)
    response = client.post('/create_market', data={
        'youtube_url': test_context['youtube_url'],
        'title': test_context['title'],
        'target_views': test_context['target_views'],
        'deadline': test_context['deadline']
    }, follow_redirects=True)
    assert response.status_code == 200
    # Save market_id for later
    with client.application.app_context():
        from app import MYB_Market
        market = db.session.query(MYB_Market).filter_by(title=test_context['title'], youtube_url=test_context['youtube_url']).order_by(MYB_Market.id.desc()).first()
        assert market is not None
        test_context['market_id'] = market.id

# 3. Register and login as user
def test_register_and_login_user(client, test_context):
    register_and_login(client, test_context['test_user'], test_context['test_user'])
    response = client.get('/profile', follow_redirects=True)
    assert test_context['test_user'].encode() in response.data

# 4. Claim daily bonus
def test_claim_daily_bonus(client, test_context):
    register_and_login(client, test_context['test_user'], test_context['test_user'])
    response = client.get('/daily_bonus', follow_redirects=True)
    assert response.status_code == 200
    assert b"bonus" in response.data or b"already claimed" in response.data

# 5. Place a bet
def test_place_bet(client, test_context):
    register_and_login(client, test_context['test_user'], test_context['test_user'])
    response = client.post('/place_bet', data={
        'market_id': test_context['market_id'],
        'prediction': 'yes',
        'amount': 50
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Bet placed" in response.data or b"Test Market" in response.data

# 6. Check active bet in profile
def test_check_active_bet(client, test_context):
    register_and_login(client, test_context['test_user'], test_context['test_user'])
    response = client.get('/profile')
    assert test_context['title'].encode() in response.data
    assert b"Active Bets" in response.data

# 7. Resolve market (as admin)
def test_resolve_market(client, test_context):
    register_and_login(client, test_context['admin_user'], test_context['admin_user'], is_admin=True)
    response = client.get('/update_views', follow_redirects=True)
    assert response.status_code == 200

# 8. Check bet result in profile
def test_check_bet_result(client, test_context):
    register_and_login(client, test_context['test_user'], test_context['test_user'])
    response = client.get('/profile')
    assert b"Betting History" in response.data
    assert b"Won" in response.data or b"Lost" in response.data

