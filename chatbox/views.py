# Views.py
from django.shortcuts import render

async_mode = None

import os
from .chatbot import room_to_chatbot_user, ChatBotUser
from .serializers import ChatRoomSerializer, ChatBoxMessageSerializer
from django.db import IntegrityError
from decouple import Config, RepositoryEnv, UndefinedValueError
from redis import StrictRedis
from itertools import zip_longest
from django.db import transaction
from .models import ChatRoom, ChatboxMessage
import uuid

from threading import Event # Wait for an event to occur

import socketio

sio = socketio.Server(async_mode=async_mode)
thread = None

# Tracks the total number of users using the admin channel
num_users = 0

# Maximum number of members in a group
threshold = 4

sid_to_room = dict()

# TODO: Current Message Number. Must be read from somewhere in the future
msg_num = 0

# The event object, which the background thread waits on. Update the DB when the event is set
event = Event()

# Redis Server Options
DOTENV_FILE = os.path.join(os.getcwd(), 'chatbox_socketio', '.env')
env_config = Config(RepositoryEnv(DOTENV_FILE))

HOST = env_config.get('REDIS_SERVER_HOST')

try:
    PASSWORD = env_config.get('REDIS_SERVER_PASSWORD')
except UndefinedValueError:
    PASSWORD = None

PORT = env_config.get('REDIS_SERVER_PORT')
        
if PASSWORD == None:
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


def get_last_state_from_redis(room_name):
	# TODO: Retrieve the last state stored in the DB
	return 1


# Updates the database with the session data, from the stored cache in redis
# TODO: Wrap around @database_sync_to_async and make this faster
def update_session_db(room_name):
	global redis_connection

	# TODO: Eliminate this inefficient scanning and do something much better
	for key_batch in fetch_redis_batch(redis_connection.scan_iter(room_name + '_*'), 500):
		for key in key_batch:
			if key is None:
				break
			content = redis_connection.hgetall(key)
			content = { key.decode('utf-8') : value.decode('utf-8') for key, value in content.items() }
			
			#if isinstance(content['room_id'], uuid.UUID):
			#	content['room_id'] = uuid.UUID(content['room_id'])

			print(f"Content: {content}")
			
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
	#	thread = sio.start_background_task(background_handler)
	return render(request, 'chatbox/index.html', {})


def background_handler():
	# TODO: Make this update the DB after certain intervals
	while True:
		event.wait() # Wait for the flag to become True
		update_session_db()
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


def update_session_redis(room_name, msg_num, content):
	global redis_connection
	redis_connection.hmset(room_name + "_" + str(msg_num), content)


def create_room(user, content):
	print(f"Creating room for user {user}")
	serializer = ChatRoomSerializer(data=content)
	try:
		if serializer.is_valid():
			with transaction.atomic():
				instance = serializer.save()
				return instance.uuid
	except IntegrityError:
		print('Room already there in DB!')


class TemplateNamespace(socketio.Namespace):
	"""
		The template chatbot routes go here
	"""
	def on_connect(self, sid, environ):
		global sid_to_room
		print(f"sid to room = {sid_to_room}")
		print(f"Connected to Namespace template!")

	
	def on_enter_room(self, sid, message):
		global sid_to_room
		global redis_connection
		
		room_name = message['room'].strip()
		
		user = get_user()

		# TODO: Map Room IDs (NOT sids) with Room Names
		if sid in sid_to_room:
			if sid_to_room[sid] != room_name:
				try:
					room_id = create_room(user, content={
						#'uuid': room_name,
						'room_name': room_name,
						'current_state': -1,
					})

					print(f"Created room with id = {room_id}")
				except IntegrityError:
					pass
		else:
			room_id = create_room(user, content={
				'room_name': room_name,
				'current_state': -1,
			})

			print(f"Created room with id = {room_id}")

		sid_to_room[sid] = room_name
		
		print(f"Entered room {room_name}")
		
		self.enter_room(sid, room=room_name)
		current_state = get_last_state_from_redis(room_name)

		chatbot_user = room_to_chatbot_user[room_name]

		with self.session(sid) as session:
			session['chatbot'] = ChatBotUser(chatbot_user, os.path.join(os.getcwd(), "chatbox/templates/chatbox/" + chatbot_user + ".json"), redis_connection)
			session['curr_state'] = get_last_state_from_redis(room_name)
			session['room_name'] = room_name
			session['room_id'] = room_id
	

	def on_exit_room(self, sid, message):
		global sid_to_room
		room_name = message['data'].strip()
		room_name = None if sid not in sid_to_room else sid_to_room[sid]
		if room_name is not None:
			self.leave_room(sid, room=room_name)
			del sid_to_room[sid]
			print(f"Exited room {room_name}")
	

	def on_message(self, sid, message):
		global msg_num
		room_name = message['room']
		
		print(f"Sending {message}")
		
		with self.session(sid) as session:
			room_id = session['room_id']

		if room_name is None:
			self.emit('message', {'data': message['data']}, room=sid)
		else:
			user = get_user()
			msg_content = message['data']

			# TODO: Make this a background task
			update_session_redis(room_name, msg_num + 1, {
				'chat_room': room_name,
				'user_name': str(user),
				'message': msg_content,
				'msg_num': msg_num + 1,
				'room_id': str(room_id),
				#'room_id': str(room_name),
			})
			msg_num += 1

			if CHATBOX_DEMO_APPLICATION == True:
				self.emit('message', {'data': msg_content}, room=room_name)
			

			if msg_content == 'dbupdate':
				update_session_db(room_name)

			if msg_content == 'admin':
				# Go to admin livechat
				self.emit('livechat', {'data': f"Redirecting to admin chat...."}, room=room_name)
				self.disconnect(sid)

			with self.session(sid) as session:
				if session['curr_state'] != -1:
					# TODO: Change this! Get the user from the headers
					user = get_user()
					reply, curr_state, msg_type = session['chatbot'].process_message(msg_content, session['curr_state'], user)

					print(f'Returned with reply {reply} with type = {msg_type}')

					if isinstance(reply, tuple):
						msg_type = reply[2]
						curr_state = reply[1]
						reply = reply[0]

					if msg_type == None:
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

					# TODO: Make this a background task
					update_session_redis(room_name, msg_num + 1, {
						'chat_room': room_name,
						'user_name': room_to_chatbot_user[room_name],
						'message': reply,
						'msg_num': msg_num + 1,
						'room_id': str(room_id),
						#'room_id': str(room_name),
					})
					msg_num += 1
				else:
					pass


	def on_disconnect(self, sid):
		print(f"Disconnecting from Namespace")
		try:
			with self.session(sid) as session:
				print(f"Updating DB for {session['room_id']}...")
				# TODO: Update current state
				obj = ChatRoom.objects.get(pk=session['room_id'])
				serializer = ChatRoomSerializer(data={
					'current_state': session['curr_state'],
				}, instance=obj)
				try:
					if serializer.is_valid():
						with transaction.atomic():
							serializer.save()
				except IntegrityError:
					print('PK for ChatRoomMessage is already there in DB!')
				update_session_db(session['room_name'])
			print('Done!')
		except KeyError:
			pass
		curr_session = None


class AdminNamespace(socketio.Namespace):
	"""
		The Admin LiveChat routes go here
	"""
	def on_connect(self, sid, environ):
		global sid_to_room
		print(f"Connected to Namespace admin!")
		try:
			room_name = message['room'].strip()
			print(f"Room name {room_name}")
			sid_to_room[sid] = room_name
		except:
			room_name = None

	
	def on_enter_room(self, sid, message):
		global sid_to_room
		room_name = message['room'].strip()
		sid_to_room[sid] = room_name
		print(f"Entered room {room_name}")
		self.enter_room(sid, room=room_name)
	

	def on_exit_room(self, sid, message):
		global sid_to_room
		room_name = message['data'].strip()
		room_name = None if sid not in sid_to_room else sid_to_room[sid]
		if room_name is not None:
			self.leave_room(sid, room=room_name)
			del sid_to_room[sid]
			print(f"Exited room {room_name}")
	

	def on_message(self, sid, message):
		room_name = message['room']
		print(f"Sending {message}")
		if sid not in sid_to_room:
			self.emit('message', {'data': message['data']}, room=sid)
		else:
			print(f"Emitting to room {room_name}")
			self.emit('message', {'data': message['data']}, room=room_name)

	def on_disconnect(self, sid):
		print(f"Disconnecting from Namespace")
		self.disconnect(sid)

# Register the namespaces
sio.register_namespace(TemplateNamespace('/chat'))
sio.register_namespace(AdminNamespace('/admin'))