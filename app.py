from flask import Flask, render_template, request, jsonify
import speech_recognition as sr
from pydub import AudioSegment
from io import BytesIO
import chess
import re

app = Flask(__name__)

# Initialize recognizer
recognizer = sr.Recognizer()

# Global chess board
board = chess.Board()
current_player = 'white'  # white or black

PIECE_NAMES = {
    'pawn': 'p',
    'knight': 'n',
    'bishop': 'b',
    'rook': 'r',
    'queen': 'q',
    'king': 'k',
    # Portuguese
    'peão': 'p',
    'cavalo': 'n',
    'bispo': 'b',
    'torre': 'r',
    'rainha': 'q',
    'rei': 'k'
}

# Maps for converting words to numbers and letters
WORD_TO_NUMBER = {
    'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4',
    'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
    # Portuguese
    'zero': '0', 'um': '1', 'dois': '2', 'três': '3', 'quatro': '4',
    'cinco': '5', 'seis': '6', 'sete': '7', 'oito': '8', 'nove': '9',
}

# Maps letters that sound like words
WORD_TO_LETTER = {
    # English
    'ay': 'a', 'bee': 'b', 'see': 'c', 'dee': 'd', 'ee': 'e', 'eff': 'f',
    'gee': 'g', 'aitch': 'h', 'eye': 'i', 'jay': 'j', 'kay': 'k', 'ell': 'l',
    'em': 'm', 'en': 'n', 'oh': 'o', 'pee': 'p', 'queue': 'q', 'ar': 'r',
    'ess': 's', 'tee': 't', 'you': 'u', 'vee': 'v', 'double-you': 'w',
    # Portuguese - common phonetic spellings
    'á': 'a', 'é': 'e', 'i': 'i', 'ó': 'o', 'u': 'u',
    'a': 'a', 'e': 'e', 'o': 'o',
}

def get_legal_moves_for_position(square):
    """Get all legal moves that start from a given square"""
    square_index = chess.parse_square(square)
    legal_moves = [move for move in board.legal_moves if move.from_square == square_index]
    return legal_moves

def find_piece_by_name(piece_name, from_square=None):
    """
    Find a piece by its name and optional starting square
    Returns the piece and its squares, or None
    """
    piece_name = piece_name.lower().strip()
    piece_symbol = PIECE_NAMES.get(piece_name)
    
    if not piece_symbol:
        return None
    
    # Determine if we're looking for white or black piece
    is_white = (current_player == 'white')
    piece = chess.Piece(chess.PIECE_NAMES.index(piece_symbol), is_white)
    
    squares = []
    for square in chess.SQUARES:
        if board.piece_at(square) == piece:
            squares.append(square)
    
    return piece, squares

def normalize_text_to_coordinates(text):
    """
    Convert spoken words to chess coordinates
    E.g., "é dois é quatro" → "e2e4" → ["e2", "e4"]
    """
    text = text.lower().strip()
    
    # First, try to find coordinates directly (e.g., "e2 e4")
    squares = re.findall(r'[a-h][1-8]', text)
    if len(squares) >= 2:
        return squares
    
    # Convert words to letters and numbers
    words = re.findall(r'\b\w+\b', text)
    converted = []
    
    for word in words:
        # Try to convert word to letter
        if word in WORD_TO_LETTER:
            converted.append(WORD_TO_LETTER[word])
        # Try to convert word to number
        elif word in WORD_TO_NUMBER:
            converted.append(WORD_TO_NUMBER[word])
        # Keep original if it's a single letter
        elif len(word) == 1 and word in 'abcdefgh12345678':
            converted.append(word)
    
    # Now try to form valid chess coordinates from converted text
    result = []
    for i in range(len(converted) - 1):
        current = converted[i]
        next_char = converted[i + 1]
        
        # Check if current is a letter and next is a number
        if current in 'abcdefgh' and next_char in '12345678':
            result.append(current + next_char)
    
    return result

def parse_move_from_speech(text):
    """
    Parse voice command and extract chess move
    Handles formats like:
    - "e2 to e4"
    - "pawn e2 to e4"
    - "move pawn to e4"
    - "e2 e4"
    - "move knight from e2 to e4"
    - "rook e4"
    Also handles speech recognition errors:
    - "é dois é quatro" → "e2 e4"
    """
    text = text.lower().strip()
    
    # Extract piece name if mentioned
    piece_name = None
    for pname in PIECE_NAMES.keys():
        if pname in text:
            piece_name = pname
            break
    
    # Try to extract squares using the coordinate normalization
    squares = normalize_text_to_coordinates(text)
    
    if not squares:
        return None, None, None
    
    # Determine from and to squares based on what we found
    if len(squares) >= 2:
        from_square = squares[0]
        to_square = squares[1]
    elif len(squares) == 1:
        # If only one square given and piece name known, try to find the piece
        to_square = squares[0]
        from_square = None
        
        if piece_name:
            piece, possible_squares = find_piece_by_name(piece_name) or (None, [])
            if possible_squares:
                # Try to find which piece can move to the target square
                target_index = chess.parse_square(to_square)
                for square in possible_squares:
                    moves = [m for m in board.legal_moves if m.from_square == square and m.to_square == target_index]
                    if moves:
                        from_square = chess.square_name(square)
                        break
    else:
        return None, None, None
    
    return from_square, to_square, piece_name

def move_to_uci(from_square, to_square):
    """Convert square names to UCI move format"""
    try:
        move = chess.Move.from_uci(f"{from_square}{to_square}")
        return move
    except:
        return None

@app.route('/recognize', methods=['POST'])
def recognize_speech():
    """
    Receive audio data, convert to text, and process chess move
    """
    global current_player, board
    
    try:
        # Get the audio file and language from the request
        if 'audio' not in request.files:
            return jsonify({'error': 'No audio file provided'}), 400
        
        language = request.form.get('language', 'en-US')
        audio_file = request.files['audio']
        
        # Read the audio data
        audio_data = audio_file.read()
        
        # Convert webm to wav format if needed
        try:
            audio = AudioSegment.from_file(BytesIO(audio_data), format="webm")
            wav_io = BytesIO()
            audio.export(wav_io, format="wav")
            wav_io.seek(0)
            audio_data = wav_io.read()
        except:
            # If conversion fails, assume it's already in the right format
            pass
        
        # Convert to audio data for speech recognition
        audio_file_obj = sr.AudioFile(BytesIO(audio_data))
        
        with audio_file_obj as source:
            audio = recognizer.record(source)
        
        # Try to recognize the speech
        try:
            text = recognizer.recognize_google(audio, language=language)
            
            # Parse the move from speech
            from_square, to_square, piece_name = parse_move_from_speech(text)
            
            move_result = {
                'success': True,
                'text': text,
                'board': None,  # Will be set after move is made
                'current_player': current_player,
                'move': None,
                'error': None,
                'feedback': None
            }
            
            if to_square:
                # If from_square provided, try directly. Otherwise try to infer from legal moves.
                target_index = chess.parse_square(to_square)

                # If from_square given, validate and try move
                if from_square:
                    from_square_index = chess.parse_square(from_square)
                    piece = board.piece_at(from_square_index)

                    if piece is None:
                        move_result['error'] = f"No piece on {from_square}"
                        move_result['board'] = board.fen()
                    elif piece.color == chess.WHITE and current_player != 'white':
                        move_result['error'] = f"It's Black's turn! You're playing White."
                        move_result['board'] = board.fen()
                    elif piece.color == chess.BLACK and current_player != 'black':
                        move_result['error'] = f"It's White's turn! You're playing Black."
                        move_result['board'] = board.fen()
                    else:
                        move = move_to_uci(from_square, to_square)
                        if move and move in board.legal_moves:
                            board.push(move)
                            move_result['move'] = str(move)
                            move_result['feedback'] = f"✓ {piece_name.title() if piece_name else 'Piece'} moved from {from_square} to {to_square}"
                            current_player = 'black' if current_player == 'white' else 'white'
                            move_result['current_player'] = current_player
                            move_result['board'] = board.fen()
                        else:
                            move_result['error'] = f"Illegal move: {from_square} to {to_square}"
                            move_result['board'] = board.fen()
                else:
                    # No from_square provided: find all legal moves that go to target
                    candidate_moves = []
                    for m in board.legal_moves:
                        if m.to_square == target_index:
                            p = board.piece_at(m.from_square)
                            if p and ((p.color == chess.WHITE and current_player == 'white') or (p.color == chess.BLACK and current_player == 'black')):
                                candidate_moves.append(m)

                    # If piece_name provided, filter by piece type
                    if piece_name and candidate_moves:
                        wanted_symbol = PIECE_NAMES.get(piece_name)
                        if wanted_symbol:
                            filtered = [m for m in candidate_moves if board.piece_at(m.from_square).symbol().lower() == wanted_symbol]
                            candidate_moves = filtered

                    if len(candidate_moves) == 1:
                        move = candidate_moves[0]
                        board.push(move)
                        move_result['move'] = str(move)
                        move_result['feedback'] = f"✓ moved to {to_square}"
                        current_player = 'black' if current_player == 'white' else 'white'
                        move_result['current_player'] = current_player
                        move_result['board'] = board.fen()
                    elif len(candidate_moves) > 1:
                        move_result['error'] = "Multiple pieces can move to that square. Please specify the piece (e.g., 'knight c3' or 'b1 c3')."
                        move_result['board'] = board.fen()
                    else:
                        move_result['error'] = f"No legal moves to {to_square}"
                        move_result['board'] = board.fen()
            else:
                move_result['feedback'] = "Didn't detect a valid move. Try: 'e2 e4' or 'pawn from e2 to e4'"
                move_result['board'] = board.fen()

            return jsonify(move_result)
            
        except sr.UnknownValueError:
            return jsonify({
                'success': False,
                'error': 'Could not understand audio. Speak clearly.',
                'board': board.fen(),
                'current_player': current_player
            }), 400
        except sr.RequestError as e:
            return jsonify({
                'success': False,
                'error': f'Network error: {str(e)}',
                'board': board.fen(),
                'current_player': current_player
            }), 500
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Error: {str(e)}',
            'board': board.fen(),
            'current_player': current_player
        }), 500

@app.route('/get-board', methods=['GET'])
def get_board():
    """Get current board state"""
    return jsonify({
        'fen': board.fen(),
        'current_player': current_player,
        'moves': [str(move) for move in board.legal_moves]
    })

@app.route('/reset-board', methods=['POST'])
def reset_board():
    """Reset the board to starting position"""
    global board, current_player
    board = chess.Board()
    current_player = 'white'
    return jsonify({
        'success': True,
        'fen': board.fen(),
        'current_player': current_player
    })

@app.route('/')
def index():
    """Serve the main page"""
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True, port=5000)
