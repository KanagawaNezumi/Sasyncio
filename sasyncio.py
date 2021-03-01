import collections
import selectors
import functools

class Handle:

    def __init__(self, callback, args, loop):
        self._loop = loop
        self._callback = callback
        self._args = args

    def _run(self):
        self._callback(*self._args)

class Future:

    _state = "PENDING"

    def __init__(self, loop):
        self._result = None
        self._callbacks = []
        self._loop = loop

    def add_done_callback(self, fn):
        if self._state != 'PENDING':
            self._loop.call_soon(fn, self)
        else:
            self._callbacks.append(fn)
    
    def _schedule_callbacks(self):
        callbacks = self._callbacks[:]
        self._callbacks = []
        for cb in callbacks:
            self._loop.call_soon(cb, self)

    def set_result(self, result):
        self._result = result
        self._state = "FINISHED"
        self._schedule_callbacks()

    def result(self):
        return self._result

    def __iter__(self):
        yield self
        return self._result


class Task(Future):
    def __init__(self, coro, loop=None):
        super().__init__(loop=loop)
        self.coro = coro
        self._fut_waiter = None
        self._loop.call_soon(self._step)

    def _step(self, exc=None):
        coro = self.coro
        self._fut_waiter = None
        try:
            if not exc:
                result = coro.send(None)
            else:
                result = coro.throw(exc)
        except StopIteration as exc:
            self.set_result(exc.value)
        else:
            result.add_done_callback(self._wakeup)
            self._fut_waiter = result
            if result is None:
                self._loop.call_soon(self._step)
    
    def _wakeup(self, future):
        try:
            future.result()
        except BaseException as exc:
            # This may also be a cancellation.
            self._step(exc)
        else:
            self._step()
        self = None  # Needed to break cycles when an exception occurs.


class Loop:

    _instance = None
    _tasks = []

    def __new__(cls, *args, **kwarags):
        if cls._instance:
            return cls._instance
        cls._instance = super().__new__(cls, *args, **kwarags)
        return cls._instance

    def __init__(self):
        self._ready = collections.deque()
        self._selector = selectors.DefaultSelector()
        self._stoping = False

    def call_soon(self, callback, *args):
        handle = Handle(callback, args, self)
        self._ready.append(handle)
    
    def _add_callback(self, handle):
        self._ready.append(handle)

    def _process_events(self, event_list):
        for key, mask in event_list:
            fileobj, (reader, writer) = key.fileobj, key.data
            if mask & selectors.EVENT_READ and reader is not None:
                self._add_callback(reader)
            if mask & selectors.EVENT_WRITE and writer is not None:
                self._add_callback(writer)

    def _add_reader(self, fd, callback, *args):
        reader = Handle(callback, args, self)
        self._selector.register(fd, selectors.EVENT_READ, (reader, None))
    
    def _add_writer(self, fd, callback, *args):
        writer = Handle(callback, args, self)
        self._selector.register(fd, selectors.EVENT_WRITE, (None, writer))

    def _remove_writer(self, fd):
        self._selector.unregister(fd)

    def _remove_reader(self, fd):
        self._selector.unregister(fd)

    def create_future(self):
        return Future(loop=self)
    
    def create_task(self, coro):
        task = Task(coro, loop=self)
        self._tasks.append(task)
        return task

    def sock_connect(self, sock, addr):
        fut = self.create_future()
        fd = sock.fileno()
        try:
            sock.connect(addr)
        except BlockingIOError:
            def connected(fut):
                print("connected!")
                self._remove_writer(fd)
            fut.add_done_callback(connected)
            self._add_writer(fd, fut.set_result, None)
            return (yield from fut)
        else:
            fut.set_result(None)
    
    def sock_send(self, sock, data):
        fut = self.create_future()
        fd = sock.fileno()
        try:
            sock.send(data)
        except BlockingIOError:
            def sended(fut):
                print("sended!")
                self._remove_writer(fd)
            fut.add_done_callback(sended)
            self._add_writer(fd, fut.set_result, len(data))
            return (yield from fut)
        else:
            fut.set_result(len(data))
        
    def sock_recv(self, sock, n=1):
        fut = self.create_future()
        fd = sock.fileno()
        def recved(fut):
            data = sock.recv(n)
            fut.set_result(data)
            self._remove_reader(fd)
        self._add_reader(fd, recved, fut)
        return (yield from fut)

    def sock_recv_all(self, sock):
        chunk = yield from self.sock_recv(sock)
        rs = []
        while chunk:
            rs.append(chunk)
            chunk = yield from self.sock_recv(sock)
        r = b''.join(rs)
        return r

    def _run_once(self):
        try:
            event_list = self._selector.select() #取出已就绪的事件
            self._process_events(event_list) #处理这些事件
        except:
            pass

        ntodo = len(self._ready) # 确定有n个需要执行的任务
        for i in range(ntodo): #将之依次出队，并执行
            handle = self._ready.popleft()
            handle._run()

    def run_forever(self):
        while True:
            self._run_once()
            if self._stoping:
                break
    
    def run_until_complete(self):
        while True:
            if len([task for task in self._tasks if task._state == "FINISHED"]) == len(self._tasks):
                break
            self._run_once()

def get_event_loop():
    return Loop()

def wait(*coros):
    loop = get_event_loop()
    for coro in coros:
        task = loop.create_task(coro)