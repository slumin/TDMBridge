import requests
import telebot
import threading
import asyncio
import discord
import queue
import time
import hashlib
from nio import AsyncClient, RoomMessageText

# Configuration values (hardcoded)
TELEGRAM_BOT_TOKEN = "Token"
DISCORD_BOT_TOKEN = "Token"
MATRIX_HOMESERVER = "https://matrix.org"
MATRIX_USER_ID = "@username:matrix.org"
MATRIX_ACCESS_TOKEN = "Token"

WEBHOOK_URLS = {
    -SupergrouIDhere: {  # Telegram Constant Channel ID
        threadid: "https://discord.com/api/webhooks",  # Bugs
        threadid: "https://discord.com/api/webhooks",  # Help
    }
}
DEFAULT_WEBHOOK = "https://discord.com/api/webhooks" # General

# Add Matrix room IDs to the mappings
DISCORD_TO_TELEGRAM_MAPPINGS = {
    Discord Channel ID: Telegram Channel ID,  # Discord Channel ID -> Telegram Channel ID (General)
    Discord Channel ID: Telegram Channel ID,  # Discord Channel ID -> Telegram Channel ID (Bugs)
    Discord Channel ID: Telegram Channel ID,  # Discord Channel ID -> Telegram Channel ID (Help)
}

# Matrix mappings
MATRIX_ROOM_GENERAL = "!matrixroomid:matrix.org"

MATRIX_TO_DISCORD_MAPPINGS = {
    MATRIX_ROOM_GENERAL: Matrix Room ID,  # Matrix Room ID -> Discord Channel ID (General)
}

MATRIX_TO_TELEGRAM_MAPPINGS = {
    MATRIX_ROOM_GENERAL: Matrix Room ID,  # Matrix Room ID -> Telegram Channel ID
}

DISCORD_TO_MATRIX_MAPPINGS = {v: k for k, v in MATRIX_TO_DISCORD_MAPPINGS.items()}
TELEGRAM_TO_MATRIX_MAPPINGS = {
    Matrix Room ID: {
        None: MATRIX_ROOM_GENERAL,  # Default room (General)
    }
}

# Queue for passing messages from Telegram to Matrix
matrix_message_queue = queue.Queue()

# Message tracking to prevent loops - stores message hashes
recent_messages = set()
MESSAGE_EXPIRY = 60  # seconds to keep messages in tracking set

# Initialize clients
intents = discord.Intents.none()
intents.guilds = True
intents.message_content = True
intents.messages = True
intents.members = True
intents.presences = True

discord_client = discord.Client(intents=intents)
telegram_bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
matrix_client = AsyncClient(MATRIX_HOMESERVER, MATRIX_USER_ID)

def generate_message_hash(platform, sender, content):
    """Generate a hash for message deduplication"""
    message_string = f"{platform}:{sender}:{content}"
    return hashlib.md5(message_string.encode()).hexdigest()

def is_message_duplicate(platform, sender, content):
    """Check if a message has been processed recently"""
    message_hash = generate_message_hash(platform, sender, content)
    
    # Clean up old messages
    current_time = time.time()
    global recent_messages
    recent_messages = {msg for msg in recent_messages if msg[1] > current_time - MESSAGE_EXPIRY}
    
    # Check if message is in recent messages
    for stored_hash, _ in recent_messages:
        if stored_hash == message_hash:
            return True
    
    # Add message to recent messages
    recent_messages.add((message_hash, current_time))
    return False

def send_to_discord(webhook_url, username, message, origin=None):
    """Send message to Discord with origin tracking"""
    # Check if this is a message we already processed
    if origin and is_message_duplicate(origin, username, message):
        print(f"Skipping duplicate message to Discord: {message}")
        return
        
    payload = {
        "username": username,
        "content": message
    }
    try:
        response = requests.post(webhook_url, json=payload)
        response.raise_for_status()
        print(f"Sent to Discord: {message}")
    except requests.exceptions.RequestException as e:
        print(f"Error sending to Discord: {e}")

async def send_to_matrix(room_id, message, origin=None):
    """Send message to Matrix with origin tracking"""
    # Check if this is a message we already processed
    if origin and is_message_duplicate(origin, room_id, message):
        print(f"Skipping duplicate message to Matrix: {message}")
        return
        
    try:
        await matrix_client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": message
            }
        )
        print(f"Sent to Matrix room {room_id}: {message}")
    except Exception as e:
        print(f"Error sending to Matrix: {e}")

def send_to_telegram(chat_id, message, thread_id=None, origin=None):
    """Send message to Telegram with origin tracking"""
    # Check if this is a message we already processed
    if origin and is_message_duplicate(origin, str(chat_id), message):
        print(f"Skipping duplicate message to Telegram: {message}")
        return
    
    try:
        if thread_id:
            telegram_bot.send_message(chat_id, message, message_thread_id=thread_id)
        else:
            telegram_bot.send_message(chat_id, message)
        print(f"Sent to Telegram: {message}")
    except Exception as e:
        print(f"Error sending to Telegram: {e}")

@telegram_bot.message_handler(func=lambda message: True)
def handle_telegram_message(message):
    if message.from_user.is_bot:
        return  # Ignore messages from bots to prevent loops
    
    print(f"Received message from Telegram: {message.text}")
    chat_id = message.chat.id
    thread_id = message.message_thread_id if hasattr(message, 'message_thread_id') else None

    first_name = message.from_user.first_name or ""
    last_name = message.from_user.last_name or ""
    sender_name = f"{first_name} {last_name}".strip()
    formatted_message = f"<Telegram: {sender_name}>: {message.text}"
    
    # Track this message to avoid loops
    message_hash = generate_message_hash("telegram", sender_name, message.text)
    recent_messages.add((message_hash, time.time()))
    
    # Send to Discord
    webhook_url = DEFAULT_WEBHOOK  # Default to General
    if chat_id in WEBHOOK_URLS and thread_id in WEBHOOK_URLS[chat_id]:
        webhook_url = WEBHOOK_URLS[chat_id][thread_id]
    
    username = f"Telegram: {sender_name}"
    send_to_discord(webhook_url, username, message.text, origin="telegram")
    
    # Add to Matrix message queue
    if chat_id in TELEGRAM_TO_MATRIX_MAPPINGS:
        if thread_id in TELEGRAM_TO_MATRIX_MAPPINGS[chat_id]:
            matrix_room_id = TELEGRAM_TO_MATRIX_MAPPINGS[chat_id][thread_id]
        else:
            matrix_room_id = TELEGRAM_TO_MATRIX_MAPPINGS[chat_id].get(None)
            
        if matrix_room_id:
            matrix_message_queue.put((matrix_room_id, formatted_message, "telegram"))

@discord_client.event
async def on_ready():
    print(f"Discord bot logged in as {discord_client.user}")

@discord_client.event
async def on_message(message):
    if message.author == discord_client.user or message.webhook_id:
        return  # Ignore messages from the bot itself and webhooks to prevent loops

    display_name = message.author.display_name
    formatted_message = f"<Discord: {display_name}>: {message.content}"
    
    # Track this message to avoid loops
    message_hash = generate_message_hash("discord", display_name, message.content)
    recent_messages.add((message_hash, time.time()))
    
    # Send to Telegram
    if message.channel.id in DISCORD_TO_TELEGRAM_MAPPINGS:
        telegram_chat_id = DISCORD_TO_TELEGRAM_MAPPINGS[message.channel.id]
        send_to_telegram(telegram_chat_id, formatted_message, origin="discord")
    
    # Send to Matrix
    if message.channel.id in DISCORD_TO_MATRIX_MAPPINGS:
        matrix_room_id = DISCORD_TO_MATRIX_MAPPINGS[message.channel.id]
        await send_to_matrix(matrix_room_id, formatted_message, origin="discord")

async def matrix_message_callback(room, event):
    if event.sender == MATRIX_USER_ID:
        return  # Ignore own messages to prevent loops
    
    if isinstance(event, RoomMessageText):
        room_id = room.room_id
        sender = event.sender
        content = event.body
        
        # Get sender display name
        try:
            user_info = await matrix_client.get_joined_members(room_id)
            display_name = user_info.members[sender].display_name or sender
        except:
            display_name = sender
        
        # Skip if it looks like a bridged message
        if "<Telegram:" in content or "<Discord:" in content:
            print(f"Skipping already bridged message: {content}")
            return
            
        formatted_message = f"<Matrix: {display_name}>: {content}"
        
        # Track this message to avoid loops
        message_hash = generate_message_hash("matrix", display_name, content)
        recent_messages.add((message_hash, time.time()))
        
        # Send to Discord
        if room_id in MATRIX_TO_DISCORD_MAPPINGS:
            discord_channel_id = MATRIX_TO_DISCORD_MAPPINGS[room_id]
            
            # Determine webhook based on Discord channel
            webhook_url = DEFAULT_WEBHOOK
            if discord_channel_id == discord_channel_id:  # Bugs
                webhook_url = WEBHOOK_URLS.get(telegramsupergroupID, {}).get(threadID, DEFAULT_WEBHOOK)
            elif discord_channel_id == discord_channel_id:  # Help
                webhook_url = WEBHOOK_URLS.get(telegramsupergroupID, {}).get(threadID, DEFAULT_WEBHOOK)
            
            send_to_discord(webhook_url, f"Matrix: {display_name}", content, origin="matrix")
        
        # Send to Telegram
        if room_id in MATRIX_TO_TELEGRAM_MAPPINGS:
            telegram_chat_id = MATRIX_TO_TELEGRAM_MAPPINGS[room_id]
            
            # Find appropriate thread ID if needed
            thread_id = None
            for tg_chat, rooms in TELEGRAM_TO_MATRIX_MAPPINGS.items():
                for thread, matrix_room in rooms.items():
                    if matrix_room == room_id and thread is not None:
                        thread_id = thread
                        break
            
            send_to_telegram(telegram_chat_id, formatted_message, thread_id, origin="matrix")

async def process_matrix_queue():
    """Process the matrix message queue in the main asyncio thread"""
    while True:
        try:
            # Check if there are messages to process
            while not matrix_message_queue.empty():
                room_id, message, origin = matrix_message_queue.get()
                await send_to_matrix(room_id, message, origin=origin)
                matrix_message_queue.task_done()
            
            # Sleep to avoid hogging CPU
            await asyncio.sleep(0.1)
        except Exception as e:
            print(f"Error in Matrix queue processor: {e}")
            await asyncio.sleep(1)  # Sleep longer if there's an error

async def init_matrix_client():
    # Log in to Matrix using access token
    matrix_client.access_token = MATRIX_ACCESS_TOKEN
    
    # Verify connection works with a sync call
    resp = await matrix_client.sync(timeout=30000)
    if isinstance(resp, Exception):
        print(f"Failed to connect to Matrix: {resp}")
        return False
    
    print(f"Connected to Matrix as {MATRIX_USER_ID}")
    
    # Register callback for message events
    matrix_client.add_event_callback(matrix_message_callback, RoomMessageText)
    
    # Join rooms if not already joined
    for room_id in set(MATRIX_TO_DISCORD_MAPPINGS.keys()) | set(MATRIX_TO_TELEGRAM_MAPPINGS.keys()):
        try:
            # Check if already in room
            if room_id in matrix_client.rooms:
                print(f"Already in Matrix room: {room_id}")
                continue
                
            await matrix_client.join(room_id)
            print(f"Joined Matrix room: {room_id}")
        except Exception as e:
            print(f"Error joining Matrix room {room_id}: {e}")
    
    return True

async def start_matrix():
    try:
        if await init_matrix_client():
            await matrix_client.sync_forever(timeout=30000)
    except Exception as e:
        print(f"Matrix client failed: {e}")

def start_telegram():
    try:
        telegram_bot.infinity_polling()
    except Exception as e:
        print(f"Telegram bot failed to start: {e}")

async def start_discord():
    try:
        await discord_client.start(DISCORD_BOT_TOKEN)
    except Exception as e:
        print(f"Discord bot failed to start: {e}")

async def clean_message_cache():
    """Periodically clean up the message cache"""
    while True:
        try:
            # Clean up messages older than MESSAGE_EXPIRY
            current_time = time.time()
            global recent_messages
            recent_messages = {msg for msg in recent_messages if msg[1] > current_time - MESSAGE_EXPIRY}
            await asyncio.sleep(MESSAGE_EXPIRY / 2)  # Clean up twice during the expiry period
        except Exception as e:
            print(f"Error in message cache cleaner: {e}")
            await asyncio.sleep(5)

async def main():
    # Start the Matrix queue processor
    queue_processor = asyncio.create_task(process_matrix_queue())
    
    # Start the message cache cleaner
    cache_cleaner = asyncio.create_task(clean_message_cache())
    
    # Start Matrix and Discord clients
    matrix_task = asyncio.create_task(start_matrix())
    discord_task = asyncio.create_task(start_discord())
    
    # Start Telegram in a separate thread
    telegram_thread = threading.Thread(target=start_telegram)
    telegram_thread.daemon = True
    telegram_thread.start()
    
    # Wait for discord and matrix tasks
    await asyncio.gather(matrix_task, discord_task, queue_processor, cache_cleaner)

if __name__ == "__main__":
    asyncio.run(main())


















