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
from .events import background_handler, TemplateNamespace, AdminNamespace

async_mode = None

sio = socketio.Server(async_mode=async_mode)
thread = None

# Tracks the total number of users using the admin channel
num_users = 0

# Maximum number of members in a group
threshold = 4

# The event object, which the background thread waits on. Update the DB when the event is set
event = Event()

# TODO: Cache the last N messages for instant display
# The idea is to store it in the redis session, so that it loads the messages whenever the same
# user comes to the same room
last_N_messages = 4


def index(request):
    #global thread
    #if thread is None:
    #    thread = sio.start_background_task(background_handler)
    return render(request, 'chatbox/index.html', {})


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


# Register the namespaces
sio.register_namespace(TemplateNamespace('/chat'))
sio.register_namespace(AdminNamespace('/admin'))
