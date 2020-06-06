from django.db import models
from django.conf import settings
import uuid
from django.utils.translation import ugettext_lazy as _


class ChatRoom(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    #uuid = models.CharField(primary_key=True, max_length=255)
    created_on = models.DateTimeField(_('chatroom created on'), auto_now_add=True)
    room_name = models.CharField(max_length=1000, null=True)
    current_state = models.IntegerField(default=1, db_column='current_state')

class ChatboxMessage(models.Model):
    # TODO: Maintain a reference to the User model and get user information
    chat_room = models.CharField(max_length=1000)
    room_id = models.ForeignKey('ChatRoom', on_delete=models.CASCADE, db_column='room_id')
    user_name = models.CharField(max_length=1000)
    msg_num = models.IntegerField(primary_key=True)
    message = models.CharField(max_length=1000)