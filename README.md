# ChatBot Demo

This is a sample bi-modal Chatbot, which handles inputs in two ways -
* Using a template `JSON` file (here, for illustration, the template JSON files can be found at `chatbox/templates/chatbox/*.json`)
* Using a live-chat based communication, between an actual administrator user and the end-user.

Initially, the bot is using the template json file to respond to the end-user (in this case, the bot is called `Susan`, so it uses `Susan.json`)
The end-user is anonymous, and does not need to register themselves in order to chat with the template bot.

During the communication, whenever the end-user types `admin`, the chatbot will go into the second mode, where it will redirect the communication to an actual registered admin.

Here, you can create and register an admin in `localhost:PORT_NO/admin`, using standard Django authentication procedures.

Now, the chat will be between the actual admin and the end-user. I've assumed that any admin can come and join this live-chat, but only one client can chat in the room.

I've also placed a limit on the number of users in the room (refer the `threshold` variable in `chat/consumers.py`)

## About the Template JSON File
The id's of the nodes in the template json file need *not* be ordered. There is suitable logic to handle this, using a hashmap to map these unordered id's into an ordered list. As long as the id's belong to those in the file, they need not be sequential.

## About handling Websocket connections
In the first mode, the Javascript client uses Socket.io to create a socket, and handle events on that socket object. Once the bot switches to the second mode, the namespace is changed from `/chat` to `/admin`, for maintaining events corresponding to the admin livechat.

Here onwards, `chatbox/chatbot.py` will no longer be of use, since it is a live admin user who is talking to the end-user.

## Instructions for running the server
1. Go to `settings.py` and add your server machine's IP address to `ALLOWED_HOSTS`.
2. Go to `livechat/.env` and change it accordingly, to add your suitable redis server credentials. The database credentials need *NOT* be used. If your redis server doesn't have a password, you must remove the `REDIS_SERVER_PASSWORD ` field.
3. TO run the server, ideally create some admins first, using:
```bash
python manage.py createsuperuser
```

Add as many admins as you wish

Finally, make migrations using:
```bash
python manage.py makemigrations
python manage.py migrate
```

4. Run the server (on port 8000) using:
```bash
python manage.py runserver 0.0.0.0:8000
```

5. The lobby chatbot room (Susan) is located at: `localhost:8000/chatbox/lobby`, but you can also go to `localhost:8000/chatbox` and then type the room name as `lobby`.
6. Keep chatting with the chatbot, and if an option is present, you need to type the text in the option, and not the number.
7. Send `admin` whwnever you want to get redirected to the admin livechat.
8. On another session, login as an admin first, and then go to `localhost:8000/chatbox/livechat/lobby`, from the admin side. You must be logged in as a django admin, as otherwise the server won't allow you to chat!
