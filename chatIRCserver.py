import socket
import sys

def Main():
	TCP_HOST = '127.0.0.1'
	TCP_PORT = 4201
	BUFFER_SIZE = 1024

	sockbock = socket.socket()
	sockbock.bind((TCP_HOST, TCP_PORT))

	sockbock.listen(1)

	c, addr = sockbock.accept()
	print "Connection from: " + str(addr)
	while True:
		data = c.recv(BUFFER_SIZE)
		if not data:
			break
		print "From connected user: " + str(data)
		data = str(data).upper()
		print "sending: " + str(data)
		c.send(data)

	sockbock.close()

if __name__ == '__main__':
	Main()
