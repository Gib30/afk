import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or os.urandom(24).hex()  # Secure random key
    SQLALCHEMY_DATABASE_URI = 'sqlite:///twitter_bot.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TWITTER_CONSUMER_KEY = 'your_twitter_consumer_key'  # Replace with your key
    TWITTER_CONSUMER_SECRET = 'your_twitter_consumer_secret'  # Replace with your secret