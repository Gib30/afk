import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or os.urandom(24).hex()  # Secure random key
    SQLALCHEMY_DATABASE_URI = 'sqlite:///twitter_bot.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TWITTER_CONSUMER_KEY = 'DanMq0hpNHlDyHvgEEqBGJjQf'  # Replace with your key
    TWITTER_CONSUMER_SECRET = '9hLUXprKxohNccE8VtnTorC0KtdWnsuL7dOydz1OAH2KOxqbBE'  # Replace with your secret
    