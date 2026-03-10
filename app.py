from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
from database import db, GameRoom, Player, GameHistory
from game_master import GameMaster
import uuid
import json
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///game.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Создаем таблицы и очищаем старые данные
with app.app_context():
    db.drop_all()
    db.create_all()
    print("База данных успешно создана!")
    
    # Очищаем все старые записи
    Player.query.delete()
    GameRoom.query.delete()
    GameHistory.query.delete()
    db.session.commit()
    print("База данных очищена от старых записей")

# Хранилище для мастеров игры (ИИ)
game_masters = {}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create_room', methods=['POST'])
def create_room():
    """Создание новой игровой комнаты"""
    room_id = str(uuid.uuid4())[:8]
    room_name = request.json.get('name', 'Новое приключение')
    
    room = GameRoom(id=room_id, name=room_name)
    db.session.add(room)
    db.session.commit()
    
    return jsonify({'room_id': room_id})

@app.route('/join_room', methods=['POST'])
def join_room_api():
    """Вход в комнату (все игроки, мастер - ИИ)"""
    room_id = request.json.get('room_id')
    player_name = request.json.get('player_name')
    character_json = request.json.get('character', '{}')
    
    room = GameRoom.query.get(room_id)
    if not room:
        return jsonify({'error': 'Комната не найдена'}), 404
    
    # Проверяем, есть ли уже игрок с таким именем в комнате
    existing_player = Player.query.filter_by(room_id=room_id, name=player_name).first()
    
    if existing_player:
        # Если игрок уже существует - обновляем его данные
        existing_player.character = character_json
        existing_player.action_ready = False
        existing_player.action = ''
        db.session.commit()
        player_id = existing_player.id
        print(f"Игрок {player_name} обновлен в комнате {room_id}")
    else:
        # Создаем нового игрока
        player = Player(
            room_id=room_id,
            name=player_name,
            character=character_json,
            is_gm=False
        )
        db.session.add(player)
        db.session.commit()
        player_id = player.id
        print(f"Новый игрок {player_name} создан в комнате {room_id}")
    
    session['player_id'] = player_id
    session['room_id'] = room_id
    
    # Если это первый игрок в комнате, создаем для нее GameMaster
    if room_id not in game_masters:
        game_masters[room_id] = GameMaster(os.getenv('DEEPSEEK_API_KEY'))
        
        # Отправляем приветствие от ИИ (только если в комнате нет истории)
        history_count = GameHistory.query.filter_by(room_id=room_id).count()
        if history_count == 0:
            initial_prompt = """Начни игру в таверне "Гнилой пень". Опиши обстановку, кто находится в таверне. 
            Спроси игроков, кто они и как оказались здесь. Не жди ответа, просто создай атмосферное вступление.
            Используй мат и грубые выражения, это уместно в тёмном фэнтези."""
            
            def send_welcome():
                with app.app_context():
                    response = game_masters[room_id].get_response(initial_prompt)
                    history = GameHistory(room_id=room_id, content=response)
                    db.session.add(history)
                    db.session.commit()
                    socketio.emit('new_gm_message', {
                        'message': response,
                        'history': True
                    }, room=room_id)
            
            socketio.start_background_task(send_welcome)
    
    return jsonify({
        'player_id': player_id,
        'room_id': room_id
    })

@app.route('/room/<room_id>')
def room(room_id):
    """Страница комнаты"""
    if 'player_id' not in session:
        return redirect(url_for('index'))
    
    room = GameRoom.query.get(room_id)
    if not room:
        return redirect(url_for('index'))
    
    return render_template('room.html', room_id=room_id)

@socketio.on('connect')
def handle_connect():
    print('Client connected')

@socketio.on('join')
def on_join(data):
    """Игрок заходит в комнату"""
    room = data['room']
    join_room(room)
    
    # Отправляем список игроков (только активных)
    players = Player.query.filter_by(room_id=room).all()
    players_data = [{
        'id': p.id,
        'name': p.name,
        'ready': p.action_ready,
        'character': json.loads(p.character) if p.character else {}
    } for p in players]
    
    emit('players_update', {'players': players_data}, room=room)
    
    # Отправляем историю игры новому игроку
    history = GameHistory.query.filter_by(room_id=room).order_by(GameHistory.created_at).all()
    for h in history:
        emit('new_gm_message', {
            'message': h.content,
            'history': True
        })

@socketio.on('action')
def handle_action(data):
    """Игрок отправил свое действие"""
    player_id = data['player_id']
    action = data['action']
    room_id = data['room']
    
    player = Player.query.get(player_id)
    if player:
        player.action = action
        player.action_ready = True
        db.session.commit()
    
    # Обновляем всех в комнате
    players = Player.query.filter_by(room_id=room_id).all()
    all_ready = all(p.action_ready for p in players)
    
    players_data = [{
        'id': p.id,
        'name': p.name,
        'ready': p.action_ready
    } for p in players]
    
    emit('players_update', {'players': players_data}, room=room_id)
    
    # Если все готовы - автоматически запрашиваем ИИ
    if all_ready and len(players) > 0:
        emit('all_ready', room=room_id)
        # Автоматически запускаем ИИ через 2 секунды
        socketio.sleep(2)
        handle_ai_request({'room': room_id})

@socketio.on('request_ai_response')
def handle_ai_request(data):
    """Запрос к ИИ (автоматический или по кнопке)"""
    room_id = data['room']
    
    # Получаем всех игроков
    players = Player.query.filter_by(room_id=room_id).all()
    
    # Собираем действия и характеристики
    actions = []
    for p in players:
        if p.action:
            # Парсим характеристики
            try:
                char = json.loads(p.character) if p.character else {}
                
                # Формируем строку с характеристиками для воина
                if char.get('class') == 'Воин':
                    stats = f"Сила:{char.get('strength', 18)} Ловк:{char.get('dexterity', 12)} Инт:{char.get('intelligence', 8)} Хар:{char.get('charisma', 10)} Воспр:{char.get('perception', 12)} Удача:{char.get('luck', 10)}"
                # Для мага
                elif char.get('class') == 'Маг':
                    stats = f"Сила:{char.get('strength', 8)} Ловк:{char.get('dexterity', 10)} Инт:{char.get('intelligence', 18)} Хар:{char.get('charisma', 12)} Воспр:{char.get('perception', 12)} Удача:{char.get('luck', 10)}"
                # Для вора
                elif char.get('class') == 'Вор':
                    stats = f"Сила:{char.get('strength', 10)} Ловк:{char.get('dexterity', 18)} Инт:{char.get('intelligence', 12)} Хар:{char.get('charisma', 10)} Воспр:{char.get('perception', 14)} Удача:{char.get('luck', 10)}"
                # Для священника
                elif char.get('class') == 'Священник':
                    stats = f"Сила:{char.get('strength', 12)} Ловк:{char.get('dexterity', 8)} Инт:{char.get('intelligence', 14)} Хар:{char.get('charisma', 16)} Воспр:{char.get('perception', 10)} Удача:{char.get('luck', 10)}"
                else:
                    stats = f"Сила:{char.get('strength', 10)} Ловк:{char.get('dexterity', 10)} Инт:{char.get('intelligence', 10)} Хар:{char.get('charisma', 10)} Воспр:{char.get('perception', 10)} Удача:{char.get('luck', 10)}"
                
                class_name = char.get('class', 'неизвестный класс')
            except Exception as e:
                stats = "Сила:10 Ловк:10 Инт:10 Хар:10 Воспр:10 Удача:10"
                class_name = "неизвестный класс"
            
            actions.append(f"{p.name} ({class_name}) [{stats}]: {p.action}")
    
    # Если нет действий - выходим
    if not actions:
        return
    
    # Получаем последние 5 сообщений из истории
    history = GameHistory.query.filter_by(room_id=room_id).order_by(GameHistory.created_at.desc()).limit(5).all()
    history.reverse()
    
    history_text = ""
    for h in history:
        history_text += f"{h.content}\n\n"
    
    # Получаем мастера для этой комнаты
    gm = game_masters.get(room_id)
    if not gm:
        gm = GameMaster(os.getenv('DEEPSEEK_API_KEY'))
        game_masters[room_id] = gm
    
    # Готовим промт с учетом действий и характеристик
    prompt = f"""
    История игры до этого момента:
    {history_text if history_text else "Игра только начинается. Вы в таверне 'Гнилой пень'. Кругом пьянь, воняет рыбой и потом."}
    
    ДЕЙСТВИЯ ИГРОКОВ В ЭТОМ ХОДУ (с их характеристиками):
    {chr(10).join(actions)}
    
    Опиши, что произошло дальше. Учти действия ВСЕХ игроков - они происходят одновременно.
    
    Для каждого игрока:
    1. Используй его характеристики при бросках кубиков d20
    2. Опиши результат броска (успех/частичный успех/провал)
    3. Укажи, какая характеристика сработала
    4. Обнови статус (HP, MP, выносливость) если нужно
    
    ВАЖНО: НЕ ПРЕДЛАГАЙ ВАРИАНТЫ ДЕЙСТВИЙ. Вообще никаких списков.
    Игроки сами напишут, что делают дальше.
    
    Используй мат и грубые выражения — это таверна, здесь так разговаривают.
    """
    
    # Очищаем действия игроков (сбрасываем готовность)
    Player.query.filter_by(room_id=room_id).update({'action_ready': False, 'action': ''})
    db.session.commit()
    
    def process_ai():
        # Создаем контекст приложения внутри фоновой задачи
        with app.app_context():
            try:
                response = gm.get_response(prompt)
                
                # Сохраняем в историю
                history = GameHistory(room_id=room_id, content=response)
                db.session.add(history)
                db.session.commit()
                
                # Отправляем игрокам
                socketio.emit('new_gm_message', {
                    'message': response,
                    'history': True
                }, room=room_id)
                
                # Обновляем список игроков (сбрасываем лампочки)
                players = Player.query.filter_by(room_id=room_id).all()
                players_data = [{
                    'id': p.id,
                    'name': p.name,
                    'ready': p.action_ready
                } for p in players]
                
                socketio.emit('players_update', {'players': players_data}, room=room_id)
                
            except Exception as e:
                socketio.emit('new_gm_message', {
                    'message': f"⚠️ Ошибка связи с DeepSeek: {str(e)}. Попробуйте еще раз.",
                    'history': True
                }, room=room_id)
    
    socketio.start_background_task(process_ai)

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)