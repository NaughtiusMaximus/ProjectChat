import socket
import sys


def Main():
	TCP_HOST = '127.0.0.1'
	TCP_PORT = 4201
	BUFFER_SIZE = 1024

	sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	sock.connect((TCP_HOST, TCP_PORT))

	message = raw_input("->")
	while message != 'q':
		sock.send(message)
		data = sock.recv(BUFFER_SIZE)

		print 'Received from server: ' + str(data)
		message = raw_input("->")
	sock.close()

if __name__== '__main__':
	Main()

