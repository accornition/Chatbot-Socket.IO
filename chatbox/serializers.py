# Serializer

from . import models
from rest_framework import serializers

class ChatRoomSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.ChatRoom
        fields = '__all__'


class ChatBoxMessageSerializer(serializers.ModelSerializer):
    def __init__(self, *args, **kwargs):
        print(f"Ser args {args}")
        print(f"Ser kwargs {kwargs}")
        super(ChatBoxMessageSerializer, self).__init__(*args, **kwargs)
    class Meta:
        model = models.ChatboxMessage
        fields = '__all__'