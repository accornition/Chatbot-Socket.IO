"""
chatbox/events.py

Contains the necessary functions for handling events on the Server side.

This uses Socket.IO for handling polling socket events,
and uses a Redis Store as a temporary cache for a persistent DB.
"""

import os
from itertools import zip_longest
from threading import Event # Wait for an event to occur

from django.db import transaction, IntegrityError
from decouple import Config, RepositoryEnv, UndefinedValueError
from redis import StrictRedis, WatchError
import socketio

from .chatbot import room_to_chatbot_user, ChatBotUser
from .serializers import ChatBoxMessageSerializer
from .models import ChatRoom

# Redis Server Options
DOTENV_FILE = os.path.join(os.getcwd(), 'chatbox_socketio', '.env')
env_config = Config(RepositoryEnv(DOTENV_FILE))

HOST = env_config.get('REDIS_SERVER_HOST')

try:
    PASSWORD = env_config.get('REDIS_SERVER_PASSWORD')
except UndefinedValueError:
    PASSWORD = None

PORT = env_config.get('REDIS_SERVER_PORT')

if PASSWORD is None:
    REDIS_CONNECTION = StrictRedis(host=HOST, port=PORT)
else:
    REDIS_CONNECTION = StrictRedis(host=HOST, password=PASSWORD, port=PORT)

try:
    CHATBOX_DEMO_APPLICATION = env_config.get('CHATBOX_DEMO_APPLICATION', cast=bool)
except UndefinedValueError:
    CHATBOX_DEMO_APPLICATION = False


# The event object, which the background thread waits on. Update the DB when the event is set
event = Event()


def get_user():
    """
        Gets the user related credentials from the client side
    """
    # TODO: Get the user name for the session info from the client
    return 'AnonymousUser'


def fetch_redis_batch(redis_iterable, batch_size):
    """
        Get a batch of keys from the redis store, as a list of iterators
    """
    # Fetch all the keys and values in a batch
    keys = [iter(redis_iterable)] * batch_size
    return zip_longest(*keys)


def fetch_recent_history(room_name, recent_msg_id, history):
    """
        Get last history msgs from redis)
        from [recent_msg_id ... recent_msg_id - history]
    """
    global REDIS_CONNECTION
    msgs = None

    # TODO: Optimize this function
    for key in REDIS_CONNECTION.scan_iter(f"{room_name}_history"):
        if key is None:
            break
        msgs = REDIS_CONNECTION.hgetall(key)
    return msgs


def get_last_state_from_redis(room_name):
    """
        Get the most recent state from the redis store
    """
    # TODO: Retrieve the last state stored in the DB
    return 1


def flush_session(room_name, batch_size):
    """
        Deletes the messages related to the session on the redis cache
    """
    # Flush the contents of the redis cache for this session
    global REDIS_CONNECTION
    for key_batch in fetch_redis_batch(
            REDIS_CONNECTION.scan_iter(f"{room_name}_*"), batch_size
        ):
        for key in key_batch:
            if key is None:
                break
            REDIS_CONNECTION.delete(key)


def update_session_redis(room_name, msg_number, content):
    """
        Sets the key-value fields for a message on the redis store
    """
    global REDIS_CONNECTION
    REDIS_CONNECTION.hmset(room_name + "_" + str(msg_number), content)


def update_session_db(room_name):
    """
        Updates the database with the session data from the stored cache in redis
    """
    global REDIS_CONNECTION

    # TODO: Eliminate this inefficient scanning and do something much better
    for key_batch in fetch_redis_batch(REDIS_CONNECTION.scan_iter(room_name + '_*'), 500):
        for key in key_batch:
            if key is None:
                break
            content = REDIS_CONNECTION.hgetall(key)
            content = {key.decode('utf-8'): value.decode('utf-8') for key, value in content.items()}

            #if isinstance(content['room_id'], uuid.UUID):
            #    content['room_id'] = uuid.UUID(content['room_id'])

            print(f"Content: {content}")

            # Using a serializer here, as otherwise, getting the instance of
            # ChatRoom and passing it to the ChatboxMsg instance is painful
            serializer = ChatBoxMessageSerializer(data=content)

            try:
                if serializer.is_valid():
                    with transaction.atomic():
                        serializer.save()
            except IntegrityError:
                print('PK for ChatRoomMessage is already there in DB!')


def background_handler():
    """
        The background worker, which periodically updates the cache and the Database.
    """
    # TODO: Make this update the DB after certain intervals
    while True:
        event.wait() # Wait for the flag to become True
        event.clear() # Clear the flag


def atomic_set(key, value):
    """
        Atomically sets {key: value} on the redis store
    """
    global REDIS_CONNECTION
    with REDIS_CONNECTION.pipeline() as pipe:
        try:
            pipe.watch(key)
            pipe.multi()
            pipe.set(key, value)
            pipe.get(key)
            return pipe.execute()[-1], False
        except WatchError:
            return pipe.get(key), True


def atomic_get(key):
    """
        Atomically gets the most recent {key : value} pair from the redis store
    """
    global REDIS_CONNECTION
    with REDIS_CONNECTION.pipeline() as pipe:
        try:
            pipe.watch(key)
            pipe.multi()
            pipe.get(key)
            return pipe.execute()[-1], False
        except WatchError:
            return pipe.get(key), True


def create_room(user, content):
    """
        Creates a new room on the persistent Database and returns the ID of the room
    """
    print(f"Creating room for user {user}")
    instance = ChatRoom(**content)
    try:
        with transaction.atomic():
            instance.save()
        return instance.uuid
    except IntegrityError:
        print('Room already there in DB!')


def update_msgcount(room_name, num_msgs):
    """
        Updates the message count shared variable atomically on the redis cache
    """
    while True:
        # Set the current message atomically
        num_msgs, error = atomic_set(f"curr_msg_{room_name}", num_msgs)
        if not error:
            break
        else:
            # Someone else has updated this first
            num_msgs += 1
    return int(num_msgs)


def get_msgcount(room_name):
    """
        Get the message count shared variable atomically from the redis cache
    """
    while True:
        num_msgs, error = atomic_get(f"curr_msg_{room_name}")
        if not error:
            break
    return int(num_msgs)


class TemplateNamespace(socketio.Namespace):
    """
        The template chatbot routes go here
    """
    def on_connect(self, sid, environ):
        """
            Method call when connected to the socket
        """
        print("Connected to Namespace template!")


    def on_enter_room(self, sid, message):
        """
            Method call when entering a room
        """
        global REDIS_CONNECTION

        user = get_user()

        room_name = message['room'].strip()

        with transaction.atomic():
            try:
                instance = ChatRoom.objects.get(room_name=room_name)
            except ChatRoom.DoesNotExist:
                instance = None

        if instance is not None:
            room_id = instance.uuid
            num_msgs = instance.num_msgs
            # Display the recent chat history
            for msg in fetch_recent_history(room_name, num_msgs, history=5):
                pass
        else:
            room_id = create_room(user, content={
                'room_name': room_name,
                'current_state': -1,
                'num_msgs': 0,
            })
            num_msgs = 0

            print(f"Created room with id = {room_id}")

        print(f"Entered room {room_name}")

        self.enter_room(sid, room=room_name)
        current_state = get_last_state_from_redis(room_name)

        chatbot_user = room_to_chatbot_user[room_name]

        with self.session(sid) as session:
            session['chatbot'] = ChatBotUser(
                chatbot_user,
                os.path.join(os.getcwd(), "chatbox/templates/chatbox/" + chatbot_user + ".json"),
                REDIS_CONNECTION
            )

            session['curr_state'] = current_state
            session['room_name'] = room_name
            session['room_id'] = room_id
            session['num_msgs'] = get_msgcount(room_name)



    def on_exit_room(self, sid, message):
        """
            Method call when exiting a room
        """
        room_name = message['data'].strip()
        with self.session(sid) as session:
            room_name = None if session['room_name'] != room_name else room_name
        if room_name is not None:
            self.leave_room(sid, room=room_name)
            print(f"Exited room {room_name}")


    def on_message(self, sid, message):
        """
            Method call when a socket receives a message
        """
        room_name = message['room']

        print(f"Sending {message}")

        with self.session(sid) as session:
            room_id = session['room_id']
            num_msgs = get_msgcount(room_name)


        if room_name is None:
            self.emit('message', {'data': message['data']}, room=sid)
        else:
            user = get_user()
            msg_content = message['data']
            num_msgs = update_msgcount(room_name, num_msgs)

            # TODO: Make this a background task
            update_session_redis(room_name, num_msgs + 1, {
                'chat_room': room_name,
                'user_name': str(user),
                'message': msg_content,
                'msg_num': num_msgs + 1,
                'room_id': str(room_id),
            })
            num_msgs += 1

            if CHATBOX_DEMO_APPLICATION:
                self.emit('message', {'data': msg_content}, room=room_name)


            if msg_content == 'dbupdate':
                update_session_db(room_name)

            if msg_content == 'admin':
                # Go to admin livechat
                self.emit('livechat', {'data': "Redirecting to admin chat...."}, room=room_name)
                with self.session(sid) as session:
                    session['num_msgs'] = update_msgcount(room_name, num_msgs)
                self.on_disconnect(sid)

            with self.session(sid) as session:
                if session['curr_state'] != -1:
                    # TODO: Change this! Get the user from the headers
                    user = get_user()
                    reply, curr_state, msg_type = session['chatbot'].process_message(
                        msg_content, session['curr_state'], user
                    )

                    print(f'Returned with reply {reply} with type = {msg_type}')

                    if isinstance(reply, tuple):
                        msg_type = reply[2]
                        curr_state = reply[1]
                        reply = reply[0]

                    if msg_type is None:
                        msg_type = 'None'

                    # Sending the reply
                    print(f"Emitting to room {room_name}")

                    self.emit('message', {
                        'type': 'chat_message_to_client',
                        'room_name': room_name,
                        'data': reply,
                        'message_type': msg_type,
                        }, room=room_name)

                    session['curr_state'] = curr_state
                    num_msgs = update_msgcount(room_name, num_msgs)
                    print(f"num_msgs = {num_msgs}")

                    # TODO: Make this a background task
                    update_session_redis(room_name, num_msgs + 1, {
                        'chat_room': room_name,
                        'user_name': room_to_chatbot_user[room_name],
                        'message': reply,
                        'msg_num': num_msgs + 1,
                        'room_id': str(room_id),
                    })
                    num_msgs += 1
                    num_msgs = update_msgcount(room_name, num_msgs)
                    session['num_msgs'] = num_msgs
                else:
                    pass


    def on_disconnect(self, sid):
        """
           Method call when a socket disconnects
        """
        print("Disconnecting from Namespace")
        with self.session(sid) as session:
            print(f"Updating DB for {session['room_id']}...")
            # TODO: Update current state
            with transaction.atomic():
                # Update the current state in the database
                obj = ChatRoom.objects.get(pk=session['room_id'])
                obj.current_state = session['curr_state']
                obj.num_msgs = session['num_msgs']
                obj.save()
                # Now finally, update the session
                update_session_db(session['room_name'])
        print('Done!')
        print('Flushing contents of the redis session...')
        flush_session(session['room_name'], batch_size=10)
        print('Done!')
        # Added call to self.disconnect()
        self.disconnect(sid)
        print("Disconnected successfully.")


class AdminNamespace(socketio.Namespace):
    """
        The Admin LiveChat routes go here
    """
    def on_connect(self, sid, environ):
        """
            Method call when the livechat socket gets connected
        """
        print("Connected to Namespace admin!")


    def on_enter_room(self, sid, message):
        """
            Method call when someone enters the livechat room
        """
        room_name = message['room'].strip()
        with transaction.atomic():
            try:
                instance = ChatRoom.objects.get(room_name=room_name)
            except ChatRoom.DoesNotExist:
                instance = None

        if instance is not None:
            print(f"Entered room {room_name}")
            self.enter_room(sid, room=room_name)

            with self.session(sid) as session:
                session['room_name'] = room_name
                session['room_id'] = instance.uuid
                session['user'] = get_user()
        else:
            print(f"Room {message['room']} not found in the Database. Disconnecting...")
            self.disconnect(sid)


    def on_exit_room(self, sid, message):
        """
            Method call when the livechat socket disconnects
        """
        room_name = message['data'].strip()

        with self.session(sid) as session:
            room_id = session['room_id']
        if room_id is not None:
            self.leave_room(sid, room=room_name)
            print(f"Exited room {room_name}")
        else:
            print(f"Room {message['room']} not found in the Database. Disconnecting...")
            self.disconnect(sid)


    def on_message(self, sid, message):
        """
            Method call when the livechat socket receives a msg.
            This is a simple method, which broadcasts the msg.
        """
        room_name = message['room']

        print(f"Sending {message}")
        print(f"Emitting to room {room_name}")
        self.emit('message', {'data': message['data']}, room=room_name)

        msg_content = message['data']

        with self.session(sid) as session:
            room_id = session['room_id']
            num_msgs = get_msgcount(room_name)

            # TODO: Make this a backgrounded task so that we can update the
            # redis session immediately after we send a message
            update_session_redis(room_name, num_msgs + 1, {
                'chat_room': room_name,
                'user_name': str(session['user']),
                'message': msg_content,
                'msg_num': num_msgs + 1,
                'room_id': str(room_id),
            })
            num_msgs += 1
            num_msgs = update_msgcount(room_name, num_msgs)

    def on_disconnect(self, sid):
        """
            Method call when the livechat socket disconnects.
            This saves the session contents to the DB and exits.
        """
        print("Disconnecting from Namespace")

        try:
            with self.session(sid) as session:
                print(f"Updating DB for {session['room_id']}...")
                obj = ChatRoom.objects.get(pk=session['room_id'])
                while True:
                    obj.num_msgs, error = atomic_get(f"curr_msg_{session['room_name']}")
                    if not error:
                        break
                with transaction.atomic():
                    # Update the current state in the database
                    obj.save()
                    # Now finally, update the session
                    update_session_db(session['room_name'])
            print('Done!')
            print('Flushing contents of the redis session...')
            flush_session(session['room_name'], batch_size=10)
            print('Done!')
            # Added call to self.disconnect()
            self.disconnect(sid)
            print("Disconnected successfully.")
        except KeyError:
            pass
