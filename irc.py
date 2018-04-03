#!/usr/bin/env python2

from logging import exception, basicConfig, DEBUG
from random import randrange
from socket import socket, AF_INET, SOCK_STREAM, SOL_SOCKET, SO_RCVBUF, SO_SNDBUF, SO_REUSEADDR, timeout
from sys import argv, exc_clear
from threading import Thread, RLock
from time import time, sleep
from uuid import uuid4


### CONSTANTS ##########################################################################################################

basicConfig(level=DEBUG)

BUFFSZ = 1
SERVER_PORT = 6667
LOBBY = 'lobby'
NOROOM = ''
NOBODY = 'nobody'

### SHARED #############################################################################################################

def send_msg(check, sock, command, *args):
    #print 'sending: '+repr(sock)+', '+repr(command)+', '+repr(args)
    buff = join_msg(command, *args)
    while buff and check.should_run:
        try:
            buff = buff[sock.send(buff):]
        except timeout:
            #exception('timeout send_msg')
            exc_clear()
            sleep(0.1)
        except Exception:
            exception('send failed.')
            raise
    #print 'sent: '+repr(sock)
    pass

def join_msg(command, *args):
    #print 'joining: '+repr(command)+', '+repr(args)
    print repr(args)
    args = args if args is not None else []
    parts = [command] + list(args)
    for part in parts:
        assert('\x03' not in part) # End of text. Used to delimit command and args.
        assert('\x04' not in part) # End of transmission. Used to delimit messages.
    msg = '\x03'.join(parts)+'\x04'
    print 'joined: '+repr(msg)
    return msg

def recv_msg(check, sock, buff, buffsz, tmout=-1):
    #print 'receiving: '+repr(sock)
    msg = ''
    end = time() + tmout
    while not msg and check.should_run:
        try:
            newbuff = sock.recv(buffsz)
        except timeout:
            #exception('timeout recv_msg')
            exc_clear()
            sleep(0.1)
        except Exception:
            exception('receive failed.')
            raise
        else:
            if newbuff:
                buff += newbuff
                buff = buff.split('\x04', 1)
                if len(buff) > 1:
                    msg, buff = buff[0], ''.join(buff[1:])
                    command, args = split_msg(msg)
                    #print 'received: '+repr(sock)+', '+repr(command)+', '+repr(args)
                    return buff, (command, args)
                else:
                    buff = ''.join(buff)
            else:
                raise Exception('Remote end hung up.')
        if tmout >= 0 and time() >= end:
            break
    return buff, (None, None)

def split_msg(msg):
    #print 'splitting: '+repr(msg)
    msg = msg if not msg.endswith('\x04') else msg[:-1]
    msg = msg.split('\x03')
    if len(msg) > 1:
        command, args = msg[0], tuple(msg[1:])
    else:
        command, args = msg[0], tuple()
    #print 'split: '+repr(command)+', '+repr(args)
    return command, args

### SERVERS ############################################################################################################

class IrcServer(object):

    def __init__(self, addr='0.0.0.0', port=SERVER_PORT):
        # global state...
        self.should_run = False
        self.nick = 'server_'+str(uuid4())
        self.room_lock = RLock()
        self.rooms = {LOBBY: [self.nick]}
        self.clients_lock = RLock()
        self.clients = {}
        # socket...
        self.addr = addr
        self.port = port
        self.buffsz = BUFFSZ
        self.sock = socket(AF_INET, SOCK_STREAM)
        self.sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
        self.sock.setsockopt(SOL_SOCKET, SO_RCVBUF, self.buffsz)
        self.sock.setsockopt(SOL_SOCKET, SO_SNDBUF, self.buffsz)
        self.sock.settimeout(1)
        self.sock.bind((self.addr, self.port))

    ### Socket related. ###

    def add_client(self, sock, addr):
        print 'adding client: '+repr(sock)+', '+repr(addr)
        thr = Thread(target=self.client_loop, args=(sock, addr))
        with self.clients_lock:
            self.clients[sock] = {'w_sock_lock': RLock(), 'r_sock_lock': RLock(), 'addr': addr, 'thr': thr, 'buff': '',
                                  'nick': NOBODY, 'room': NOROOM, 'rooms': []}
        thr.start()

    def drop_client(self, sock):
        print 'dropping client: '+repr(sock)
        with self.clients_lock:
            client = self.clients[sock]
            with self.room_lock:
                for room in client['rooms']:
                    if client['nick'] in self.rooms.get(room, []): self.rooms[room].pop(self.rooms[room].index(client['nick']))
            if sock in self.clients: self.clients.pop(sock)
        sock.close()

    def send_msg(self, sock, command, *args):
        with self.clients_lock:
            w_sock_lock = self.clients[sock]['w_sock_lock']
        with w_sock_lock:
            send_msg(self, sock, command, *args)

    def recv_msg(self, sock, tmout=-1):
        with self.clients_lock:
            r_sock_lock = self.clients[sock]['r_sock_lock']
        with r_sock_lock:
            with self.clients_lock:
                buff = self.clients[sock]['buff']
            buff, (command, args) = recv_msg(self, sock, buff, self.buffsz, tmout)
            with self.clients_lock:
                self.clients[sock]['buff'] = buff
        return command, args

    ### Server state and commands. ###

    def send_handshake(self, sock):
        print 'sending handshake: '+repr(sock)
        self.send_msg(sock, 'IRCclient', '0.01')

    def recv_handshake(self, sock):
        print 'receiving handshake: '+repr(sock)
        soft, args = self.recv_msg(sock)
        if soft != 'IRCclient' or len(args) != 2:
            raise Exception('Not our client.')
        else:
            if args[0] != '0.01':
                raise Exception('Not our version.')
            try:
                sargs = self.change_nick(sock, args[1])
            except Exception as exc:
                self.send_msg(sock, 'fail', repr(exc))
                raise Exception('Nickname {} in use.'.format(args[1]))
            else:
                self.send_msg(sock, 'succ', *sargs)
            try:
                sargs = self.enter_room(sock, LOBBY)
            except Exception as exc:
                self.send_msg(sock, 'fail', repr(exc))
                raise Exception('Failed to enter the {}.'.format(LOBBY))
            else:
                self.send_msg(sock, 'succ', *sargs)

    def change_nick(self, sock, nick):
        print 'changing nickname: '+repr(sock)+', '+repr(nick)
        if nick == NOBODY:
            raise Exception('You can not be {}.'.format(NOBODY))
        with self.clients_lock:
            client = self.clients[sock]
            for other_client in self.clients.values():
                if nick == other_client['nick']:
                    raise Exception('Another client "{addr}" is using nickname "{nick}".'.format(**other_client))
            with self.room_lock:
                for room in client['rooms']:
                    if client['nick'] in self.rooms.get(room, []):
                        self.rooms[room].pop(self.rooms[room].index(client['nick']))
                        self.rooms[room].append(nick)
            client['nick'] = nick
        return ('Your nickname is now {}.'.format(nick),)

    def display_rooms(self, sock):
        print 'This socket is displaying rooms:' +repr(sock)
        with self.room_lock:
            self.send_msg(sock, 'receive_display_rooms', *self.rooms.keys())
        return ('sent.',)
	
    def enter_room(self, sock, room):
        print 'entering room: '+repr(sock)+', '+repr(room)
        with self.clients_lock:
            client = self.clients[sock]
            with self.room_lock:
                # Create room on-demand.
                if room != LOBBY: 
		    client['room'] = room
		    self.rooms[room] = self.rooms.get(room, [])
                # Rooms track clients.
                if client['nick'] not in self.rooms[room]: 
		    self.rooms[room].append(client['nick'])
                # Clients get a default room.
                if client['room'] == NOROOM or client['rooms'] == []: 
		    client['room'] = room
                # Clients track rooms.
                if room not in client['rooms']: 
		    client['room'] = room
		    client['rooms'].insert(0, room)
		if room == LOBBY:
		    client['room'] = LOBBY
        return ('Welcome, you are now in {}.'.format(room),)

    def leave_room(self, sock, room):
        print 'leaving room: '+repr(sock)+', '+repr(room)
        with self.clients_lock:
            client = self.clients[sock]
            with self.room_lock:
                # Clients track rooms.
                if room in client['rooms']: client['rooms'].pop(client['rooms'].index(room))
                # Clients get a default room.
                if client['room'] == room: client['room'] = NOROOM
                # Clients must have a defualt room and be in at least one room.
                if client['room'] == NOROOM or client['rooms'] == []: self.enter_room(sock, LOBBY)
                # Rooms track clients.
                if client['nick'] in self.rooms.get(room, []): self.rooms[room].pop(self.rooms[room].index(client['nick']))
                # Remove room on-demand.
                if room != LOBBY and len(self.rooms.get(room, [client['nick']])) <= 0: self.rooms.pop(room)
        return ('Goodbye, you have left {}.'.format(room),)

    def message(self, sock, string):
        print 'message from: ' + repr(socket)
        with self.clients_lock:
            client = self.clients[sock]
            with self.room_lock:
                nicks = self.rooms[client['room']]
                for other_sock, other_client in self.clients.items():
                    if other_client['nick'] in nicks:
                        self.send_msg(other_sock, 'receive_message', client['room'], client['nick'], string)
        return ('sent.',)

    #------------------------------------------------------------
    def display_nicks(self, sock):
        with self.clients_lock:
	    test = []
	    for k,v in self.rooms.iteritems():
	        test.extend(v)
	    test.pop(0)
	    index = 0
	    tempString = ''
	    for i in test:
                self.send_msg(sock, 'people in IRC', i)
        return ('sent.',)
    #------------------------------------------------------------

    ### Main loops. ###

    def stop_loop(self):
        print 'stopping server loops'
        self.should_run = False

    def server_loop(self):
        print 'starting server loop'
        self.should_run = True
        try:
            self.sock.listen(25)
            while self.should_run:
                try:
                    sock, addr = self.sock.accept()
                except timeout:
                    exc_clear()
                    sleep(0.01)
                else:
                    sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
                    sock.setsockopt(SOL_SOCKET, SO_RCVBUF, self.buffsz)
                    sock.setsockopt(SOL_SOCKET, SO_SNDBUF, self.buffsz)
                    sock.settimeout(1)
                    self.add_client(sock, addr)
        except BaseException:
            exception('server loop.')
            exc_clear()
        self.should_run = False
        self.sock.close()
        print 'server loop finished'

    def client_loop(self, sock, addr):
        print 'starting client loop: '+repr(sock)+', '+repr(addr)
        try:
            self.send_handshake(sock)
            self.recv_handshake(sock)
            while self.should_run:
                command, args = self.recv_msg(sock)
                try:
                    sargs = getattr(self, command)(sock, *args)
                except AttributeError as exc:
                    exception('client loop command.')
                    self.send_msg(sock, 'fail', repr(exc))
                    raise
                except Exception as exc:
                    exception('client loop command.')
                    exc_clear()
                    self.send_msg(sock, 'fail', repr(exc))
                    sleep(0.01)
                else:
                    self.send_msg(sock, 'succ', *sargs)
                    sleep(0.01)
        except BaseException:
            exception('client loop.')
            exc_clear()
        self.drop_client(sock)
        print 'client loop finished: '+repr(sock)+', '+repr(addr)

### CLIENTS ############################################################################################################

class IrcClient(object):

    def __init__(self, addr, port=SERVER_PORT):
        # global state...
        self.should_run = False # client loop
        self.nick = 'user_'+str(uuid4()) # the client's ID
        self.cmd_buff_lock = RLock() # lock for the client's resources
        self.cmd_buff = [] # parses the client's commands
        self.serv_buff = '' # the empty buffer
        # socket...
        self.serv_addr = addr # the servers address. 
        self.serv_port = port # port from the initial run of client
        self.buffsz = BUFFSZ # the buffer size, which is 1
        self.sock = socket(AF_INET, SOCK_STREAM) # setting up the sockets
        self.sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1) # keeping the same socket, and not reissuing new ones
        self.sock.setsockopt(SOL_SOCKET, SO_RCVBUF, self.buffsz) # the thread for receiving messages
        self.sock.setsockopt(SOL_SOCKET, SO_SNDBUF, self.buffsz) # the thread for sending messages
        self.sock.settimeout(1) # used to time out the socket of the lock, because don't want to hog resources 

    ### Socket related. ###

    def send_msg(self, command, *args): # function defining sending message to the server
        send_msg(self, self.sock, command, *args) # checks then sends the message

    def recv_msg(self, tmout=-1): # receving a message
    	# checks the received message, if it is valid, then parse the message, and the commands
        self.serv_buff, (command, args) = recv_msg(self, self.sock, self.serv_buff, self.buffsz, tmout)
        return command, args # what should the server do next

    ### Client state and commands. ###

    def recv_handshake(self): # the handshake to make sure this is not some outside source trying to join
        print 'receiving handshake' #used when initally joining server
        soft, args = self.recv_msg() # checks the version, and the string
        if soft != 'IRCclient' or len(args) != 1: # handshake check, make sure the same string
            raise Exception('Not our server.')
        else:
            if args[0] != '0.01': 
                raise Exception('Not our version.')

    def send_handshake(self): # sending the handshake when connecting to server
        print 'sending handshake' 
        self.send_msg('IRCclient', '0.01', self.nick) # message sent for the handshake
        status, sargs = self.recv_msg()  
        print repr(status), repr(sargs) #check ther return the received message
        if status != 'succ': # if this is not a success, then throw an exception
            raise Exception(''.join(args))

    def change_nick(self, nick): # setting the name for the user
        self.send_msg('change_nick', nick)

    def display_rooms(self): # used to show the rooms in the main loop
        self.send_msg('display_rooms')

    def enter_room(self, room): # command to enter a certain room
        self.send_msg('enter_room', room)

    def leave_room(self, room): # command to leave a room
        self.send_msg('leave_room', room)

    def message(self, string): # command to send a message
        self.send_msg('message', string)
    
    def display_nicks2(self):
    	self.send_msg('display_nicks')

    ### Main loops. ###

    def stop_loop(self): # function to stop the loop
        print 'stopping client loops'
        self.should_run = False

    def client_loop(self): # starts the client
        print 'starting client loop'
        self.should_run = True # client starts, and the client loop starts
        try:
            Thread(target=self.server_loop).start() # create a thread for the client on the server
            thr = Thread(target=self.user_loop) # thread is used for sending and receiving messages of the client
            thr.daemon = True # start the thread
            thr.start()
            while self.should_run:
                # if need to ping
                # ping
                sleep(0.01) # this allows other clients to send or receive messages
        except BaseException:
            exception('client loop.') 
            exc_clear()
        self.should_run = False # if the user quits, then stop the loops
        print 'client loop finished'

    def user_loop(self): # this is where commands from the client become processed
        print 'starting user loop'
	#self.room # the room their are in
        try:
            while self.should_run: 
                command = '' # empty command string, waiting input
                args = '' # empty arg string, waiting for input
                string = raw_input('cmd > ') # displays this to show where to enter commands
                if string == '': # if nothing is entered, then continue loop
                    continue
                elif string.startswith('/'): # but if something is entered with '/', then parse line
                    command = string[1:].split(None, 1) # the command is after the '/'
                    if len(command) > 1: # parse the line with the command, and the args with it
                        command, args = command[0], command[1]
                    else: # if it has less than one command, then parse the line with an empty string
                        command, args = command[0], ''
                else:
                    command, args = 'message', string # if the line does not start with '/', then send a message
                command = command.lower() # make sure the commands are lower cased
                print repr(command)
                print repr(args)
                with self.cmd_buff_lock: # using a lock
                    if command in ['change-nick', 'cn', 'c']: 
		    	# to change name
                        print 'HERE1'
			# throw 'change_nick' and the args into command buffer
                        self.cmd_buff.append((self.change_nick, (args,))) 
                    elif command in ['display-rooms', 'drm', 'r']: 
		    	# To display rooms
                        print 'HERE2'
			# append 'display_rooms' to the command buffer, to display rooms
                        self.cmd_buff.append((self.display_rooms, ())) 
                    elif command in ['enter-room', 'er', 'e']:
		    	# to enter a room
                        print 'HERE3'
			# 'enter_room' appended to command buffer, along with room name
                        self.cmd_buff.append((self.enter_room, (args,)))
                    elif command in ['leave-room', 'lr', 'l']:
		    	# to leave a room
                        print 'HERE4'
			# 'leave_room' put into command buffer, and the room name that you want to leave
                        self.cmd_buff.append((self.leave_room, (args,)))
                    elif command in ['message', 'msg', 'm']:
		    	# to send a message
                        print 'HERE5'
			# 'message' is appended to the command buffer, and the text you want to send
                        self.cmd_buff.append((self.message, (args,)))
		    elif command in ['q', 'quit', 'disconnect', 'dc']:
		        self.should_run = False
		    elif command in ['all', 'everyone', 'a']:
		    	self.cmd_buff.append((self.display_nicks2, ()))
                    else:
                        print 'wrong!!!!!!!!!!!!!!!!!!!!!!!!!!!'
        except BaseException:
            exception('user loop.')
            exc_clear()
        self.should_run = False
        print 'user loop finished'

    def server_loop(self): # client connecting to the server loop
        print 'starting server loop'
        try:
            self.sock.connect((self.serv_addr, self.serv_port)) # connect to server
            self.recv_handshake() # The server extends the handshake to the client 
            self.send_handshake() # then the client responds
            while self.should_run: 
                status = True # makes sure we run the command once
                while self.should_run and status is not None:
                    status, sargs = self.recv_msg(tmout=1)
                    if status is not None:
                        print repr(status), repr(sargs)
                if len(self.cmd_buff) > 0: # if there are commands
                    with self.cmd_buff_lock: # hold the lock
                        command, args = self.cmd_buff.pop(0) # get the command
                        command(*args) # execute the command in server
                    status = None # break the loop
                    while self.should_run and status not in ['succ', 'fail']:
                        status, sargs = self.recv_msg()
                        print repr(status), repr(sargs)
                sleep(0.01) # pause this client
        except BaseException:
            exception('server loop.')
            exc_clear()
        self.should_run = False
        self.sock.close()
        print 'server loop finished'

### LEARN SOMETHING ####################################################################################################

class MetaAClass(type):

    def __new__(mcls, *args, **kwds):
        print 'MetaAClass.__new__'+repr(mcls), repr(args), (kwds)
        rtn = type.__new__(mcls, *args, **kwds)
        print 'MetaAClass.__new__'+repr(rtn)
        return rtn

    def __init__(cls, *args, **kwds):
        print 'MetaAClass.__init__'+repr(cls), repr(args), (kwds)

        cls.billy = True

        rtn = type.__init__(cls, *args, **kwds)
        print 'MetaAClass.__init__'+repr(rtn)
        return rtn

    def __call__(cls, *args, **kwds):
        print 'MetaAClass.__call__'+repr(cls), repr(args), (kwds)
        rtn = type.__call__(cls, *args, **kwds)
        print 'MetaAClass.__call__'+repr(rtn)
        return rtn

class AClass(object):

    #__metaclass__ = MetaAClass

    def __new__(cls, *args, **kwds):
        print 'AClass.__new__'+repr(cls), repr(args), (kwds)
        rtn = object.__new__(cls, *args, **kwds)
        print 'AClass.__new__'+repr(rtn)
        return rtn

    def __init__(self, *args, **kwds):
        print 'AClass.__init__'+repr(self), repr(args), (kwds)
        rtn = object.__init__(self, *args, **kwds)
        print 'AClass.__init__'+repr(rtn)
        return rtn

    def __call__(*args, **kwds):
        print 'AClass.__call__'+repr(args), (kwds)
        rtn = object.__call__(*args, **kwds)
        print 'AClass.__call__'+repr(rtn)
        return rtn

#a = AClass(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, a=1, b=2, c=3)
#print repr(a.billy)

### MAIN ENTRY #########################################################################################################

if __name__ == '__main__':
    if len(argv) > 1:

        if argv[1].lower() == 'server':
            if len(argv) > 2:
                if len(argv) > 3:
                    print 'server 2 args'
                    server = IrcServer(argv[2], int(argv[3]))
                else:
                    print 'server 1 args'
                    server = IrcServer(argv[2])
            else:
                print 'server 0 args'
                server = IrcServer()
            exit(server.server_loop())

        # client
        if len(argv) > 2:
            print 'client 2 args'
            client = IrcClient(argv[1], int(argv[2]))
        else:
            print 'client 1 args'
            client = IrcClient(argv[1])
        exit(client.client_loop())

    print 'Uh, dude, you need to tell me what to do!'
    exit(1)
