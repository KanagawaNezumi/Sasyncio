import sasyncio
import socket
import time

def echo():
    sk = socket.socket()
    sk.setblocking(False)
    yield from loop.sock_connect(sk, ('127.0.0.1', 1234))
    yield from loop.sock_send(sk, "hello, world!".encode('utf-8'))
    res = yield from loop.sock_recv_all(sk)
    print(res.decode('utf-8'))

def main(loop):
    for i in range(3):
        yield from loop.create_task(echo())
    
    print("-----------------------")
    sasyncio.wait(*[echo() for i in range(3)])

if __name__ == "__main__":
    loop = sasyncio.get_event_loop()
    loop.create_task(main(loop))
    loop.run_until_complete()
