from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_bootstrap import Bootstrap
import tweepy
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import random
import time
from urllib.parse import urlparse

app = Flask(__name__)
app.config.from_object('config.Config')
db = SQLAlchemy(app)
Bootstrap(app)

# Twitter API setup
auth = tweepy.OAuthHandler(app.config['TWITTER_CONSUMER_KEY'], app.config['TWITTER_CONSUMER_SECRET'])
api = tweepy.API(auth, wait_on_rate_limit=True)

# Scheduler setup
scheduler = BackgroundScheduler()
scheduler.start()

# Helper functions
def add_log(message):
    with app.app_context():
        log = Log(message=message)
        db.session.add(log)
        db.session.commit()

def get_config(key, default=None):
    config = Config.query.filter_by(key=key).first()
    return config.value if config else default

def set_config(key, value):
    config = Config.query.filter_by(key=key).first()
    if config:
        config.value = str(value)
    else:
        db.session.add(Config(key=key, value=str(value)))
    db.session.commit()

# Routes
@app.route('/')
def index():
    if 'twitter_oauth_token' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/login')
def login():
    try:
        redirect_url = auth.get_authorization_url()
        session['request_token'] = auth.request_token
        return redirect(redirect_url)
    except tweepy.TweepError as e:
        return f"Error during login: {e}", 500

@app.route('/callback')
def callback():
    verifier = request.args.get('oauth_verifier')
    try:
        auth.request_token = session.pop('request_token', None)
        auth.get_access_token(verifier)
        session['twitter_oauth_token'] = auth.access_token
        session['twitter_oauth_secret'] = auth.access_token_secret
        return redirect(url_for('dashboard'))
    except tweepy.TweepError as e:
        return f"Error during callback: {e}", 500

@app.route('/dashboard')
def dashboard():
    if 'twitter_oauth_token' not in session:
        return redirect(url_for('index'))
    auth.set_access_token(session['twitter_oauth_token'], session['twitter_oauth_secret'])
    api.auth = auth
    try:
        user = api.me()
        logs = Log.query.order_by(Log.timestamp.desc()).limit(10).all()
        stats = {
            'followers': user.followers_count,
            'following': user.friends_count,
            'followed_users': FollowedUser.query.count()
        }
        whitelist = Whitelist.query.all()
        return render_template('dashboard.html', username=user.screen_name, logs=logs, stats=stats,
                               target_profile=get_config('target_profile', ''),
                               daily_follow_limit=get_config('daily_follow_limit', '100'),
                               unfollow_delay=get_config('unfollow_delay', '7'),
                               filter_active=get_config('filter_active', 'false') == 'true',
                               whitelist=whitelist)
    except tweepy.TweepError as e:
        add_log(f"Authentication failed: {e}")
        session.pop('twitter_oauth_token', None)
        session.pop('twitter_oauth_secret', None)
        return redirect(url_for('index'))

@app.route('/set_target', methods=['POST'])
def set_target():
    target_profile = request.form.get('target_profile')
    daily_follow_limit = request.form.get('daily_follow_limit')
    unfollow_delay = request.form.get('unfollow_delay')
    filter_active = 'true' if request.form.get('filter_active') else 'false'

    parsed = urlparse(target_profile)
    if parsed.netloc != 'twitter.com' or not parsed.path.strip('/'):
        add_log("Invalid Twitter URL provided")
        return "Invalid Twitter URL", 400
    username = parsed.path.strip('/').split('/')[0]

    set_config('target_profile', username)
    set_config('daily_follow_limit', daily_follow_limit)
    set_config('unfollow_delay', unfollow_delay)
    set_config('filter_active', filter_active)
    add_log(f"Settings updated: Target @{username}, Limit {daily_follow_limit}, Delay {unfollow_delay} days")
    return redirect(url_for('dashboard'))

@app.route('/add_whitelist', methods=['POST'])
def add_whitelist():
    screen_name = request.form.get('whitelist_user')
    try:
        user = api.get_user(screen_name=screen_name)
        if not Whitelist.query.filter_by(user_id=str(user.id)).first():
            db.session.add(Whitelist(user_id=str(user.id), screen_name=user.screen_name))
            db.session.commit()
            add_log(f"Added @{user.screen_name} to whitelist")
    except tweepy.TweepError as e:
        add_log(f"Error adding to whitelist: {e}")
    return redirect(url_for('dashboard'))

@app.route('/remove_whitelist/<user_id>', methods=['POST'])
def remove_whitelist(user_id):
    whitelist_entry = Whitelist.query.filter_by(user_id=user_id).first()
    if whitelist_entry:
        db.session.delete(whitelist_entry)
        db.session.commit()
        add_log(f"Removed @{whitelist_entry.screen_name} from whitelist")
    return redirect(url_for('dashboard'))

@app.route('/run_now/<task>')
def run_now(task):
    if task == 'follow':
        follow_task()
        return "Follow task triggered", 200
    elif task == 'unfollow':
        unfollow_task()
        return "Unfollow task triggered", 200
    return "Invalid task", 400

# Tasks
def follow_task():
    with app.app_context():
        target_username = get_config('target_profile')
        if not target_username:
            return
        daily_limit = int(get_config('daily_follow_limit', 100))
        filter_active = get_config('filter_active', 'false') == 'true'
        cursor_value = int(get_config('cursor', '-1'))

        try:
            cursor_obj = tweepy.Cursor(api.followers, screen_name=target_username, count=100, cursor=cursor_value)
            page = next(cursor_obj.pages())
            users = page
            next_cursor = cursor_obj.next_cursor if cursor_obj.next_cursor != 0 else -1
            set_config('cursor', next_cursor)

            to_follow = []
            for user in users:
                if filter_active:
                    recent_tweet = api.user_timeline(user_id=user.id, count=1)
                    if not recent_tweet or (datetime.utcnow() - recent_tweet[0].created_at).days > 30:
                        continue
                to_follow.append(user)
            verified = [user for user in to_follow if user.verified]
            non_verified = [user for user in to_follow if not user.verified]
            to_follow = verified + non_verified

            followed_count = 0
            for user in to_follow:
                if followed_count >= daily_limit:
                    break
                if not FollowedUser.query.filter_by(user_id=str(user.id)).first():
                    try:
                        api.create_friendship(user.id)
                        db.session.add(FollowedUser(user_id=str(user.id), screen_name=user.screen_name))
                        add_log(f"Followed @{user.screen_name}")
                        followed_count += 1
                        time.sleep(random.randint(5, 15))  # Random delay for safety
                    except tweepy.TweepError as e:
                        add_log(f"Error following @{user.screen_name}: {e}")
            db.session.commit()
        except tweepy.TweepError as e:
            add_log(f"Follow task error for @{target_username}: {e}")

def unfollow_task():
    with app.app_context():
        unfollow_delay = int(get_config('unfollow_delay', 7))
        cutoff_date = datetime.utcnow() - timedelta(days=unfollow_delay)
        
        try:
            our_followers = set(str(f.id) for f in api.get_followers(count=200))
            followed_users = FollowedUser.query.all()
            for user in followed_users:
                user.followed_back = user.user_id in our_followers
            db.session.commit()

            whitelist_ids = set(w.user_id for w in Whitelist.query.all())
            to_unfollow = FollowedUser.query.filter(
                FollowedUser.followed_date < cutoff_date,
                FollowedUser.followed_back == False,
                ~FollowedUser.user_id.in_(whitelist_ids)
            ).all()
            for user in to_unfollow:
                try:
                    api.destroy_friendship(user.user_id)
                    db.session.delete(user)
                    add_log(f"Unfollowed @{user.screen_name}")
                    time.sleep(random.randint(5, 15))
                except tweepy.TweepError as e:
                    add_log(f"Error unfollowing @{user.screen_name}: {e}")
            db.session.commit()
        except tweepy.TweepError as e:
            add_log(f"Unfollow task error: {e}")

scheduler.add_job(follow_task, 'cron', hour=10, minute=0, max_instances=1)
scheduler.add_job(unfollow_task, 'cron', hour=11, minute=0, max_instances=1)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=5000)