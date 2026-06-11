from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import string
import time

app = Flask(__name__)
app.config['SECRET_KEY'] = 'audioman-secret-key-2024'
CORS(app, origins="*")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# In-memory store: {connection_id: {username, socket_id, created_at}}
sessions = {}
# Map socket_id -> connection_id for cleanup
socket_to_session = {}

def generate_connection_id():
    """Generate an 8-character alphanumeric connection ID."""
    while True:
        chars = string.ascii_letters + string.digits
        conn_id = ''.join(random.choices(chars, k=8))
        # Ensure at least one digit and one letter
        if any(c.isdigit() for c in conn_id) and any(c.isalpha() for c in conn_id):
            if conn_id not in sessions:
                return conn_id

def cleanup_old_sessions():
    """Remove sessions older than 2 hours."""
    now = time.time()
    expired = [cid for cid, s in sessions.items() if now - s['created_at'] > 7200]
    for cid in expired:
        del sessions[cid]

# ── REST endpoints ──────────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'sessions': len(sessions)})

@app.route('/api/register', methods=['POST'])
def register():
    """Register a new username and get a connection ID."""
    cleanup_old_sessions()
    data = request.get_json()
    username = (data or {}).get('username', '').strip()

    if not username:
        return jsonify({'error': 'Username is required'}), 400
    if len(username) > 30:
        return jsonify({'error': 'Username too long (max 30 chars)'}), 400

    conn_id = generate_connection_id()
    sessions[conn_id] = {
        'username': username,
        'socket_id': None,
        'created_at': time.time(),
        'connected': False
    }

    return jsonify({
        'connection_id': conn_id,
        'username': username,
        'message': 'Session created. Share your Connection ID with the PC listener.'
    }), 201

@app.route('/api/session/<conn_id>', methods=['GET'])
def get_session(conn_id):
    """Check if a session/connection ID exists."""
    session = sessions.get(conn_id)
    if not session:
        return jsonify({'error': 'Connection ID not found'}), 404
    return jsonify({
        'connection_id': conn_id,
        'username': session['username'],
        'connected': session['connected']
    })

# ── WebSocket signaling ─────────────────────────────────────────────────────

@socketio.on('connect')
def on_connect():
    print(f'[WS] Client connected: {request.sid}')

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    conn_id = socket_to_session.pop(sid, None)
    if conn_id and conn_id in sessions:
        sessions[conn_id]['socket_id'] = None
        sessions[conn_id]['connected'] = False
        # Notify anyone in the room
        emit('peer_disconnected', {'connection_id': conn_id}, room=conn_id)
        leave_room(conn_id)
    print(f'[WS] Client disconnected: {sid}')

@socketio.on('join_as_sender')
def on_join_sender(data):
    """Android sender joins with their connection ID."""
    conn_id = data.get('connection_id')
    if not conn_id or conn_id not in sessions:
        emit('error', {'message': 'Invalid connection ID'})
        return
    sid = request.sid
    sessions[conn_id]['socket_id'] = sid
    sessions[conn_id]['connected'] = True
    socket_to_session[sid] = conn_id
    join_room(conn_id)
    emit('joined', {'connection_id': conn_id, 'username': sessions[conn_id]['username']})
    # Notify listener if already in room
    emit('sender_ready', {'username': sessions[conn_id]['username']}, room=conn_id, include_self=False)
    print(f'[WS] Sender joined room {conn_id}: {sessions[conn_id]["username"]}')

@socketio.on('join_as_listener')
def on_join_listener(data):
    """PC listener joins with a connection ID to receive audio."""
    conn_id = data.get('connection_id')
    if not conn_id or conn_id not in sessions:
        emit('error', {'message': 'Connection ID not found. Ask the sender to register first.'})
        return
    sid = request.sid
    socket_to_session[sid] = conn_id
    join_room(conn_id)
    session = sessions[conn_id]
    emit('listener_joined', {
        'connection_id': conn_id,
        'username': session['username'],
        'sender_ready': session['connected']
    })
    # Tell sender that a listener is waiting
    emit('listener_waiting', {}, room=conn_id, include_self=False)
    print(f'[WS] Listener joined room {conn_id}')

# WebRTC signaling relay
@socketio.on('offer')
def on_offer(data):
    conn_id = socket_to_session.get(request.sid)
    if conn_id:
        emit('offer', {'sdp': data.get('sdp')}, room=conn_id, include_self=False)

@socketio.on('answer')
def on_answer(data):
    conn_id = socket_to_session.get(request.sid)
    if conn_id:
        emit('answer', {'sdp': data.get('sdp')}, room=conn_id, include_self=False)

@socketio.on('ice_candidate')
def on_ice_candidate(data):
    conn_id = socket_to_session.get(request.sid)
    if conn_id:
        emit('ice_candidate', {'candidate': data.get('candidate')}, room=conn_id, include_self=False)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
