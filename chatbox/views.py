# Views.py
import os
from itertools import zip_longest
from threading import Event # Wait for an event to occur

from django.shortcuts import render
from django.db import transaction, IntegrityError

from decouple import Config, RepositoryEnv, UndefinedValueError
from redis import StrictRedis, WatchError

import socketio

from .chatbot import room_to_chatbot_user, ChatBotUser
from .serializers import ChatBoxMessageSerializer
from .models import ChatRoom


async_mode = None

sio = socketio.Server(async_mode=async_mode)
thread = None

# Tracks the total number of users using the admin channel
num_users = 0

# Maximum number of members in a group
threshold = 4

ROOM_TO_ID = dict()

# The event object, which the background thread waits on. Update the DB when the event is set
event = Event()

# TODO: Cache the last N messages for instant display
# The idea is to store it in the redis session, so that it loads the messages whenever the same
# user comes to the same room
last_N_messages = 4

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
    redis_connection = StrictRedis(host=HOST, port=PORT)
else:
    redis_connection = StrictRedis(host=HOST, password=PASSWORD, port=PORT)

try:
    CHATBOX_DEMO_APPLICATION = env_config.get('CHATBOX_DEMO_APPLICATION', cast=bool)
except UndefinedValueError:
    CHATBOX_DEMO_APPLICATION = False


def fetch_redis_batch(redis_iterable, batch_size):
    # Fetch all the keys and values in a batch
    keys = [iter(redis_iterable)] * batch_size
    return zip_longest(*keys)


def fetch_recent_history(room_name, recent_msg_id, num_msgs):
    # Get last N msgs from the database (or redis)
    # from [recent_msg_id ... recent_msg_id - num_msgs]
    global redis_connection
    msgs = []
    for key_batch in fetch_redis_batch(
        redis_connection.scan_iter(f"{room_name}_"), min(500, num_msgs)
    ):
        for key in key_batch:
            if key is None:
                break
    return msgs


def get_last_state_from_redis(room_name):
    # TODO: Retrieve the last state stored in the DB
    return 1


def flush_session(room_name, batch_size):
    # Flush the contents of the redis cache for this session
    global redis_connection
    for key_batch in fetch_redis_batch(
        redis_connection.scan_iter(f"{room_name}_*"), batch_size
    ):
        for key in key_batch:
            if key is None:
                break
            redis_connection.delete(key)


# Updates the database with the session data, from the stored cache in redis
def update_session_db(room_name):
    global redis_connection

    # TODO: Eliminate this inefficient scanning and do something much better
    for key_batch in fetch_redis_batch(redis_connection.scan_iter(room_name + '_*'), 500):
        for key in key_batch:
            if key is None:
                break
            content = redis_connection.hgetall(key)
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


def index(request):
    #global thread
    #if thread is None:
    #    thread = sio.start_background_task(background_handler)
    return render(request, 'chatbox/index.html', {})


def background_handler():
    # TODO: Make this update the DB after certain intervals
    while True:
        event.wait() # Wait for the flag to become True
        # update_session_db()
        event.clear() # Clear the flag


def room(request, room_name):
    return render(request, 'chatbox/room.html', {
        'room_name': room_name
    })

def adminroom(request, room_name):
    if request.user.is_authenticated and request.user.is_superuser:
        admin = True
    else:
        admin = False

    context = { 'room_name' : room_name, 'admin': admin }
    return render(request, 'chatbox/admin_room.html', context)


def get_user():
    # TODO: Get the user name for the session info from the client
    return 'AnonymousUser'


def update_session_redis(room_name, msg_number, content):
    global redis_connection
    redis_connection.hmset(room_name + "_" + str(msg_number), content)


def atomic_get_set(key, value):
    global redis_connection
    with redis_connection.pipeline() as pipe:
        try:
            pipe.watch(key)
            pipe.multi()
            pipe.set(key, value)
            pipe.get(key)
            return pipe.execute()[-1], False
        except WatchError:
            return pipe.get(key), True

def atomic_get(key):
    global redis_connection
    with redis_connection.pipeline() as pipe:
        try:
            pipe.watch(key)
            pipe.multi()
            pipe.get(key)
            return pipe.execute()[-1], False
        except WatchError:
            return pipe.get(key), True


def create_room(user, content):
    print(f"Creating room for user {user}")
    #serializer = ChatRoomSerializer(data=content)
    instance = ChatRoom(**content)
    try:
        #if serializer.is_valid():
        with transaction.atomic():
            instance.save()
            return instance.uuid
    except IntegrityError:
        print('Room already there in DB!')


class TemplateNamespace(socketio.Namespace):
    """
        The template chatbot routes go here
    """
    def on_connect(self, sid, environ):
        print(f"Connected to Namespace template!")


    def on_enter_room(self, sid, message):
        global redis_connection

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
            #for msg in fetch_recent_history(num_msgs=last_N_messages):
            #    pass
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
            session['chatbot'] = ChatBotUser(chatbot_user, os.path.join(os.getcwd(),
            "chatbox/templates/chatbox/" + chatbot_user + ".json"), redis_connection)

            session['curr_state'] = current_state
            session['room_name'] = room_name
            session['room_id'] = room_id
            while True:
                num_msgs, error = atomic_get(f"curr_msg_{room_name}")
                if not error:
                    break
            session['num_msgs'] = int(num_msgs)



    def on_exit_room(self, sid, message):
        room_name = message['data'].strip()
        with self.session(sid) as session:
            room_name = None if session['room_name'] != room_name else room_name
        if room_name is not None:
            self.leave_room(sid, room=room_name)
            print(f"Exited room {room_name}")


    def on_message(self, sid, message):
        room_name = message['room']

        print(f"Sending {message}")

        with self.session(sid) as session:
            room_id = session['room_id']
            # num_msgs = session['num_msgs']
            while True:
                num_msgs, error = atomic_get(f"curr_msg_{room_name}")
                if not error:
                    break
            num_msgs = int(num_msgs)


        if room_name is None:
            self.emit('message', {'data': message['data']}, room=sid)
        else:
            user = get_user()
            msg_content = message['data']

            while True:
                # Set the current message atomically
                num_msgs, error = atomic_get_set(f"curr_msg_{room_name}", num_msgs)
                if not error:
                    break
                else:
                    # Someone else has updated this first
                    num_msgs += 1
            
            num_msgs = int(num_msgs)

            # TODO: Make this a background task
            update_session_redis(room_name, num_msgs + 1, {
                'chat_room': room_name,
                'user_name': str(user),
                'message': msg_content,
                'msg_num': num_msgs + 1,
                'room_id': str(room_id),
                #'room_id': str(room_name),
            })
            num_msgs += 1



            if CHATBOX_DEMO_APPLICATION:
                self.emit('message', {'data': msg_content}, room=room_name)


            if msg_content == 'dbupdate':
                update_session_db(room_name)

            if msg_content == 'admin':
                # Go to admin livechat
                self.emit('livechat', {'data': f"Redirecting to admin chat...."}, room=room_name)
                while True:
                    num_msgs, error = atomic_get_set(f"curr_msg_{room_name}", num_msgs)
                    if not error:
                        break
                    else:
                        # Someone else has updated this first
                        num_msgs += 1
                with self.session(sid) as session:
                    session['num_msgs'] = num_msgs
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

                    self.emit('message',
                        {
                            'type': 'chat_message_to_client',
                            'room_name': room_name,
                            'data': reply,
                            'message_type': msg_type,
                        },
                        room=room_name
                    )

                    session['curr_state'] = curr_state

                    while True:
                        # Set the current message atomically
                        num_msgs, error = atomic_get_set(f"curr_msg_{room_name}", num_msgs)
                        if not error:
                            break
                        else:
                            # Someone else has updated this first
                            num_msgs += 1                    
                    num_msgs = int(num_msgs)
                    print(f"num_msgs = {num_msgs}")

                    # TODO: Make this a background task
                    update_session_redis(room_name, num_msgs + 1, {
                        'chat_room': room_name,
                        'user_name': room_to_chatbot_user[room_name],
                        'message': reply,
                        'msg_num': num_msgs + 1,
                        'room_id': str(room_id),
                        #'room_id': str(room_name),
                    })
                    num_msgs += 1
                    while True:
                        # Set the current message atomically
                        num_msgs, error = atomic_get_set(f"curr_msg_{room_name}", num_msgs)
                        if not error:
                            break
                        else:
                            # Someone else has updated this first
                            num_msgs += 1                    
                    num_msgs = int(num_msgs)
                    session['num_msgs'] = num_msgs
                else:
                    pass


    def on_disconnect(self, sid):
        print(f"Disconnecting from Namespace")
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
        print(f"Disconnected successfully.")


class AdminNamespace(socketio.Namespace):
    """
        The Admin LiveChat routes go here
    """
    def on_connect(self, sid, environ):
        print(f"Connected to Namespace admin!")


    def on_enter_room(self, sid, message):
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
        room_name = message['room']

        print(f"Sending {message}")
        print(f"Emitting to room {room_name}")
        self.emit('message', {'data': message['data']}, room=room_name)

        msg_content = message['data']

        with self.session(sid) as session:
            room_id = session['room_id']
            
            while True:
                # Get the current message atomically
                num_msgs, error = atomic_get(f"curr_msg_{room_name}")
                if not error:
                    break
            num_msgs = int(num_msgs)
            
            # TODO: Make this a backgrounded task so that we can update the redis session immediately after
            # we send a message
            update_session_redis(room_name, num_msgs + 1, {
                'chat_room': room_name,
                'user_name': str(session['user']),
                'message': msg_content,
                'msg_num': num_msgs + 1,
                'room_id': str(room_id),
            })
            num_msgs += 1
            
            while True:
                # Set the current message atomically
                num_msgs, error = atomic_get_set(f"curr_msg_{room_name}", num_msgs)
                if not error:
                    break
                else:
                    # Someone else has updated this first
                    num_msgs += 1

    def on_disconnect(self, sid):
        print(f"Disconnecting from Namespace")

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
            print(f"Disconnected successfully.")
        except KeyError:
            pass

# Register the namespaces
sio.register_namespace(TemplateNamespace('/chat'))
sio.register_namespace(AdminNamespace('/admin'))
