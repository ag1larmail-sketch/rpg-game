from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class GameRoom(db.Model):
    id = db.Column(db.String(50), primary_key=True)
    name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='waiting')
    game_state = db.Column(db.Text, default='')
    
class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.String(50), db.ForeignKey('game_room.id'))
    name = db.Column(db.String(50))
    character = db.Column(db.String(1000), default='{}')
    action = db.Column(db.Text, default='')
    action_ready = db.Column(db.Boolean, default=False)
    is_gm = db.Column(db.Boolean, default=False)
    
class GameHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.String(50), db.ForeignKey('game_room.id'))
    content = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)