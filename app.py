from flask import Flask, render_template, request, redirect, url_for, session, make_response, flash
from flask_bootstrap import Bootstrap
import tweepy
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime as dt
import random
import time
from urllib.parse import urlparse

# Import the shared db instance
from extensions import db
# Import models after db is initialized
from models import Log, Config, FollowedUser, WhitelistedUser

# Initialize Flask app
app = Flask(__name__)
app.config.from_object('config.Config')

# Initialize SQLAlchemy with the app
db.init_app(app)

# Bootstrap setup
Bootstrap(app)

# Helper function to get Tweepy Client instance
def get_tweepy_client():
    if 'twitter_oauth_token' in session and 'twitter_oauth_secret' in session:
        client = tweepy.Client(
            consumer_key=app.config['TWITTER_CONSUMER_KEY'],
            consumer_secret=app.config['TWITTER_CONSUMER_SECRET'],
            access_token=session['twitter_oauth_token'],
            access_token_secret=session['twitter_oauth_secret']
        )
        add_log(f"Client initialized with token: {session['twitter_oauth_token'][:10]}...")
        return client
    add_log("No OAuth tokens in session")
    return None

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

# Function to get all follower IDs for a user using v2 API
def get_all_follower_ids(screen_name):
    client = get_tweepy_client()
    if not client:
        add_log("No authenticated client available")
        return []
    all_ids = []
    try:
        # Get user ID from screen name
        user_response = client.get_user(username=screen_name)
        if user_response.data:
            user_id = user_response.data.id
            add_log(f"Resolved @{screen_name} to user ID: {user_id}")
            pagination_token = None
            while True:
                response = client.get_users_followers(id=user_id, max_results=1000, pagination_token=pagination_token)
                if response.data:
                    all_ids.extend([user.id for user in response.data])
                if response.meta.get('next_token'):
                    pagination_token = response.meta['next_token']
                else:
                    break
                time.sleep(random.randint(5, 10))  # Random delay to respect rate limits
        else:
            raise Exception("User not found")
    except tweepy.TweepyException as e:
        add_log(f"Error fetching follower IDs for @{screen_name}: {e}")
        if e.response:
            add_log(f"Status code: {e.response.status_code}")
            add_log(f"Response text: {e.response.text}")
    return all_ids

# Routes
@app.route('/')
def index():
    if 'twitter_oauth_token' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/login')
def login():
    try:
        auth = tweepy.OAuthHandler(app.config['TWITTER_CONSUMER_KEY'], app.config['TWITTER_CONSUMER_SECRET'])
        redirect_url = auth.get_authorization_url()
        session['request_token'] = auth.request_token
        add_log("OAuth login initiated")
        return redirect(redirect_url)
    except tweepy.TweepyException as e:
        add_log(f"Error during login: {e}")
        return f"Error during login: {e}", 500

@app.route('/callback')
def callback():
    verifier = request.args.get('oauth_verifier')
    try:
        auth = tweepy.OAuthHandler(app.config['TWITTER_CONSUMER_KEY'], app.config['TWITTER_CONSUMER_SECRET'])
        auth.request_token = session.pop('request_token', None)
        auth.get_access_token(verifier)
        session['twitter_oauth_token'] = auth.access_token
        session['twitter_oauth_secret'] = auth.access_token_secret
        add_log(f"OAuth tokens set: {auth.access_token[:10]}...")
        return redirect(url_for('dashboard'))
    except tweepy.TweepyException as e:
        add_log(f"Error during callback: {e}")
        return f"Error during callback: {e}", 500

@app.route('/dashboard')
def dashboard():
    client = get_tweepy_client()
    if not client:
        add_log("No authenticated client available, redirecting to login")
        return redirect(url_for('index'))
    try:
        user_response = client.get_me(user_auth=True)
        if not user_response.data:
            add_log("Failed to fetch user data: No user returned")
            session.pop('twitter_oauth_token', None)
            session.pop('twitter_oauth_secret', None)
            return redirect(url_for('index'))
        user = user_response.data
        logs = Log.query.order_by(Log.timestamp.desc()).limit(10).all()
        stats = {
            'followers': user.public_metrics['followers_count'] if user.public_metrics else 0,
            'following': user.public_metrics['following_count'] if user.public_metrics else 0,
            'followed_users': FollowedUser.query.count()
        }
        target_profile = get_config('target_profile', '')
        daily_follow_limit = get_config('daily_follow_limit', '100')
        unfollow_delay = get_config('unfollow_delay', '7')
        filter_active = get_config('filter_active', 'false') == 'true'
        whitelisted_users = WhitelistedUser.query.all()
        bot_active = get_config('bot_active', 'false') == 'true'
        return render_template('dashboard.html', username=user.username, logs=logs, stats=stats,
                               target_profile=target_profile,
                               daily_follow_limit=daily_follow_limit,
                               unfollow_delay=unfollow_delay,
                               filter_active=filter_active,
                               whitelisted_users=whitelisted_users,
                               bot_active=bot_active)
    except tweepy.TweepyException as e:
        add_log(f"Authentication failed in dashboard: {e}")
        if e.response:
            add_log(f"Status code: {e.response.status_code}")
            add_log(f"Response text: {e.response.text}")
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
    if parsed.netloc != 'x.com' and parsed.netloc != 'twitter.com' or not parsed.path.strip('/'):
        flash("Invalid X URL provided")
        return redirect(url_for('dashboard'))
    username = parsed.path.strip('/').split('/')[0]

    set_config('target_profile', username)
    set_config('daily_follow_limit', daily_follow_limit)
    set_config('unfollow_delay', unfollow_delay)
    set_config('filter_active', filter_active)

    # Fetch followers immediately after saving settings
    client = get_tweepy_client()
    if not client:
        flash("Authentication failed")
        return redirect(url_for('dashboard'))
    try:
        target_user_response = client.get_user(username=username)
        if not target_user_response.data:
            flash("User not found")
            return redirect(url_for('dashboard'))
        target_user = target_user_response.data
        follower_ids = get_all_follower_ids(username)
        add_log(f"Fetched {len(follower_ids)} followers from target profile @{username}")
        for follower_id in follower_ids:
            add_log(f"Follower ID: {follower_id}")
    except tweepy.TweepyException as e:
        add_log(f"Error fetching followers for @{username}: {e}")
        if e.response:
            add_log(f"Status code: {e.response.status_code}")
            add_log(f"Response text: {e.response.text}")
        flash(f"Error fetching followers: {e}")
        return redirect(url_for('dashboard'))

    add_log(f"Settings updated: Target @{username}, Limit {daily_follow_limit}, Delay {unfollow_delay} days")
    return redirect(url_for('dashboard'))

@app.route('/toggle_bot', methods=['POST'])
def toggle_bot():
    current_state = get_config('bot_active', 'false')
    new_state = 'true' if current_state == 'false' else 'false'
    set_config('bot_active', new_state)
    flash(f"Bot {'started' if new_state == 'true' else 'stopped'}")
    return redirect(url_for('dashboard'))

@app.route('/add_whitelisted', methods=['POST'])
def add_whitelisted():
    screen_name = request.form.get('whitelisted_user')
    client = get_tweepy_client()
    if not client:
        flash("Authentication failed")
        return redirect(url_for('dashboard'))
    try:
        user_response = client.get_user(username=screen_name)
        if user_response.data:
            user = user_response.data
            if not WhitelistedUser.query.get(user.id):
                db.session.add(WhitelistedUser(user_id=str(user.id), screen_name=user.name))
                db.session.commit()
                add_log(f"Added @{user.name} to whitelisted")
            flash("User added to whitelisted")
        else:
            flash("User not found")
    except tweepy.TweepyException as e:
        add_log(f"Error adding to whitelisted: {e}")
        if e.response:
            add_log(f"Status code: {e.response.status_code}")
            add_log(f"Response text: {e.response.text}")
        flash("Error adding user to whitelisted")
    return redirect(url_for('dashboard'))

@app.route('/remove_whitelisted/<user_id>', methods=['POST'])
def remove_whitelisted(user_id):
    whitelisted_entry = WhitelistedUser.query.get(user_id)
    if whitelisted_entry:
        db.session.delete(whitelisted_entry)
        db.session.commit()
        add_log(f"Removed @{whitelisted_entry.screen_name} from whitelisted")
        flash("User removed from whitelisted")
    else:
        flash("User not found in whitelisted")
    return redirect(url_for('dashboard'))

# Scheduler tasks
def follow_task():
    with app.app_context():
        if get_config('bot_active', 'false') == 'false':
            add_log("Bot is not active")
            return
        target_username = get_config('target_profile')
        if not target_username:
            add_log("No target profile set")
            return
        daily_limit = int(get_config('daily_follow_limit', 100))
        filter_active = get_config('filter_active', 'false') == 'true'

        client = get_tweepy_client()
        if not client:
            add_log("No authenticated client available")
            return

        try:
            follower_ids = get_all_follower_ids(target_username)
            add_log(f"Fetched {len(follower_ids)} followers from target profile @{target_username}")

            # Get user objects for these IDs in batches
            users = []
            for i in range(0, len(follower_ids), 100):
                batch_ids = follower_ids[i:i+100]
                try:
                    batch_users_response = client.get_users(ids=batch_ids)
                    if batch_users_response.data:
                        users.extend(batch_users_response.data)
                    time.sleep(random.randint(5, 10))  # Random delay to respect rate limits
                except tweepy.TweepyException as e:
                    add_log(f"Error getting users batch: {e}")
                    if e.response:
                        add_log(f"Status code: {e.response.status_code}")
                        add_log(f"Response text: {e.response.text}")
                    continue

            # Filter verified users first
            verified_users = [user for user in users if user.verified]
            non_verified_users = [user for user in users if not user.verified]
            all_users_to_follow = verified_users + non_verified_users

            # Get existing followed user IDs
            existing_followed_user_ids = {fu.user_id for fu in FollowedUser.query.all()}

            # Filter users to follow: not already followed
            users_to_follow = [user for user in all_users_to_follow if str(user.id) not in existing_followed_user_ids]

            # If filter_active is True, filter users who are active (last tweet within 30 days)
            if filter_active:
                def is_active(user):
                    try:
                        tweets_response = client.get_users_tweets(id=user.id, max_results=1)
                        if tweets_response.data:
                            last_tweet_date = tweets_response.data[0].created_at
                            return (dt.utcnow() - last_tweet_date).days <= 30
                        return False
                    except tweepy.TweepyException:
                        return False

                active_users_to_follow = [user for user in users_to_follow if is_active(user)]
            else:
                active_users_to_follow = users_to_follow

            # Follow up to daily limit, prioritizing verified users
            followed_count = 0
            for user in active_users_to_follow:
                if followed_count >= daily_limit:
                    break
                try:
                    client.follow_user(target_user_id=user.id)
                    db.session.add(FollowedUser(user_id=str(user.id), screen_name=user.name))
                    add_log(f"Followed @{user.name}")
                    followed_count += 1
                    time.sleep(random.randint(5, 15))  # Random delay for safety
                except tweepy.TweepyException as e:
                    add_log(f"Error following @{user.name}: {e}")
                    if e.response:
                        add_log(f"Status code: {e.response.status_code}")
                        add_log(f"Response text: {e.response.text}")
            db.session.commit()
        except tweepy.TweepyException as e:
            add_log(f"Follow task error for @{target_username}: {e}")
            if e.response:
                add_log(f"Status code: {e.response.status_code}")
                add_log(f"Response text: {e.response.text}")

def unfollow_task():
    with app.app_context():
        if get_config('bot_active', 'false') == 'false':
            add_log("Bot is not active")
            return
        unfollow_delay = int(get_config('unfollow_delay', 7))
        cutoff_date = dt.utcnow() - dt.timedelta(days=unfollow_delay)

        client = get_tweepy_client()
        if not client:
            add_log("No authenticated client available")
            return

        try:
            # Get followed users who are candidates for unfollowing
            followed_users_to_check = FollowedUser.query.filter(FollowedUser.followed_date < cutoff_date).all()
            user_ids_to_check = [user.user_id for user in followed_users_to_check]

            # Get my followers' IDs
            my_user = client.get_me().data
            if not my_user:
                add_log("Failed to fetch current user data")
                return
            my_follower_ids = get_all_follower_ids(my_user.username)
            my_follower_ids = set(str(id) for id in my_follower_ids)

            # Update followed_back for these users
            for user in followed_users_to_check:
                user.followed_back = user.user_id in my_follower_ids
            db.session.commit()

            # Get users to unfollow: those who haven't followed back
            to_unfollow = [user for user in followed_users_to_check if not user.followed_back]

            # Unfollow them
            for user in to_unfollow:
                try:
                    client.unfollow_user(target_user_id=user.user_id)
                    db.session.delete(user)
                    add_log(f"Unfollowed @{user.screen_name}")
                    time.sleep(random.randint(5, 15))  # Random delay for safety
                except tweepy.TweepyException as e:
                    add_log(f"Error unfollowing @{user.screen_name}: {e}")
                    if e.response:
                        add_log(f"Status code: {e.response.status_code}")
                        add_log(f"Response text: {e.response.text}")
            db.session.commit()
        except tweepy.TweepyException as e:
            add_log(f"Unfollow task error: {e}")
            if e.response:
                add_log(f"Status code: {e.response.status_code}")
                add_log(f"Response text: {e.response.text}")

# Scheduler setup
scheduler = BackgroundScheduler()
scheduler.add_job(follow_task, 'interval', hours=24, start_date=dt.utcnow().replace(hour=10, minute=0, second=0))
scheduler.add_job(unfollow_task, 'interval', hours=24, start_date=dt.utcnow().replace(hour=11, minute=0, second=0))
scheduler.start()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=5000)