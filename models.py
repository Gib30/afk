from extensions import db
from datetime import datetime

class Log(db.Model):
    id = db.Column(db.INTEGER, primary_key=True)
    timestamp = db.Column(db.DATETIME, default=datetime.utcnow)
    message = db.Column(db.String(255), nullable=False)

class Config(db.Model):
    id = db.Column(db.INTEGER, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.String(255), nullable=False)

class FollowedUser(db.Model):
    id = db.Column(db.INTEGER, primary_key=True)
    user_id = db.Column(db.String(20), unique=True, nullable=False)
    screen_name = db.Column(db.String(50), nullable=False)
    followed_date = db.Column(db.DATETIME, default=datetime.utcnow)
    followed_back = db.Column(db.BOOLEAN, default=False)

class WhitelistedUser(db.Model):
    id = db.Column(db.INTEGER, primary_key=True)
    user_id = db.Column(db.String(20), unique=True, nullable=False)
    screen_name = db.Column(db.String(50), nullable=False)