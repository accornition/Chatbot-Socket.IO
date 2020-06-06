"""
WSGI config for django_example project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/1.11/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application
import socketio

from chatbox.views import sio

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "chatbox_socketio.settings")
django_app = get_wsgi_application()
application = socketio.Middleware(sio, django_app)

import eventlet
import eventlet.wsgi
eventlet.wsgi.server(eventlet.listen(('', 8000)), application)


#django_app = get_wsgi_application()
#application = socketio.WSGIApp(sio, django_app)