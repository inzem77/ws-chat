import asyncio
import json
import time
import os

import aiohttp
from aiohttp import web
from aiopg.pool import create_pool
from aiohttp import web
from aiohttp_session import get_session, session_middleware, SimpleCookieStorage
from aiohttp_session.cookie_storage import EncryptedCookieStorage
from aiohttp_session.redis_storage import RedisStorage
import aiohttp_session
import aioredis
import aiohttp_jinja2
import jinja2

queues = []
history = {}
channels = []

import asyncio
from aiopg.pool import create_pool

loop = asyncio.get_event_loop()
#dsn = 'dbname=ws_chat user=ws_chat password=ws_chat host=localhost port=5432'
#pool = yield from create_pool(dsn)

ROOT = os.path.abspath(os.path.dirname(__file__))
path = lambda *args: os.path.join(ROOT, *args)

async def admin(request, host=""):
    form_data = await request.post()
    host = request.match_info.get('host', None)
    session = await get_session(request)
    session['last_visit'] = time.time()
    session['nickname'] = 'admin'
    if host:
        h = history[host] if host in history else {}
        return aiohttp_jinja2.render_template('admin.html', request, {'host': host, 'history': history})

    return aiohttp_jinja2.render_template('admin.html', request, {'history': history})

async def chat_page_admin(request):
    return aiohttp_jinja2.render_template('admin/chat.html', request, {})

async def hello(request):
    form_data = await request.post()
    session = await get_session(request)
    session['last_visit'] = time.time()
    host = request.host[:request.host.find(':')] # host
    if 'nickname' in form_data and form_data['nickname'] != 'admin':
        session['nickname'] = form_data['nickname']
        return web.HTTPFound(app.router['chat_page'].url(parts={'host':host})) # get chat.html

    return aiohttp_jinja2.render_template('home.html', request, {})


async def chat_page(request):
    return aiohttp_jinja2.render_template('chat.html', request, {})


async def new_msg(request):
    global loop
    session = await get_session(request)
    nickname = session['nickname']
    form_data = await request.post()
    host = request.host[:request.host.find(':')] # host
    read_task = loop.create_task(request.content.read())
    message = (await read_task).decode('utf-8')
    await send_message(host, nickname, message)
    return web.Response(body=b'OK')


async def send_message(host, nickname, message):
    print('{}: {}'.format(nickname, message).strip())
    if host not in history.keys():
        history[host]= {}
    if nickname not in history[host].keys():
        history[host][nickname] = []
    history[host][nickname].append('{}: {}'.format(nickname, message))
    if len(history[host][nickname]) > 20:
        del history[host][nickname][:-10]
    for queue in queues:
        await queue.put((host, nickname, message))


class WebSocketResponse(web.WebSocketResponse):
    # As of this writing, aiohttp's WebSocketResponse doesn't implement
    # "async for" yet. (Python 3.5.0 has just been releaset)
    # Let's add the protocol.
    async def __aiter__(self):
        return self

    async def __anext__(self):
        return (await self.receive())


async def websocket_handler(request):
    global loop
    host = request.host[:request.host.find(':')] # host
    session = await get_session(request)
    nickname = request.match_info.get('nickname', None)
    if not nickname:
        nickname = session['nickname']
    ws = WebSocketResponse()
    await ws.prepare(request)
    await send_message(host, 'system', 'We are connected to {} host!'.format(host))

    if host in history:
        if nickname in history[host]:
            for message in list(history[host][nickname]):
                ws.send_str(message)

    echo_task = loop.create_task(echo_loop(ws))

    async for msg in ws:

        if msg.tp == aiohttp.MsgType.close:
            print('websocket connection closed')
            break
        elif msg.tp == aiohttp.MsgType.error:
            print('ws connection closed with exception %s' % ws.exception())
            break
        else:
            print('ws connection received unknown message type %s' % msg.tp)

    await send_message('system', '{} left!'.format(nickname))
    echo_task.cancel()
    await echo_task
    return ws


async def echo_loop(ws):
    queue = asyncio.Queue()
    queues.append(queue)
    try:
        while True:
            host, name, message = await queue.get()
            ws.send_str('{}: {}: {}'.format(host, name, message))
    finally:
        queues.remove(queue)


async def create_redis_pool(host, port):
    await aioredis.create_pool((host, port), loop=loop)
    #redis_pool = await aioredis.create_connection(('localhost', 6379), loop=loop)

#redis_pool = create_redis_pool('localhost', 6379)

#app = web.Application(middlewares=[session_middleware(
#        SimpleCookieStorage())])
#app = web.Application(middlewares=[session_middleware(
#        EncryptedCookieStorage(b'W3mS53c4452Z2t64W3mS53c4452Z2t64'))])
#app = web.Application(middlewares=[session_middleware(
#        RedisStorage(create_redis_pool('localhost', 6379)))])

redis = create_redis_pool('localhost', 6379)
storage = aiohttp_session.redis_storage.RedisStorage(redis)
session_middleware = aiohttp_session.session_middleware(storage)
app = aiohttp.web.Application(middlewares=[session_middleware])

aiohttp_jinja2.setup(app,
    loader=jinja2.FileSystemLoader(path('templates')))
app.router.add_route('GET', '/admin/', admin)
app.router.add_route('GET', '/admin/{host}/', admin)
app.router.add_route('GET', '/admin/{host}/{nickname}/', chat_page_admin)
app.router.add_route('POST', '/admin/{host}/{nickname}/', new_msg)
app.router.add_route('GET', '/admin/{host}/{nickname}/ws/', websocket_handler)
app.router.add_route('GET', '/', hello)
app.router.add_route('POST', '/', hello)
app.router.add_route('GET', '/{host}/', chat_page, name='chat_page')
app.router.add_route('POST', '/{host}/', new_msg)
app.router.add_route('GET', '/{host}/ws/', websocket_handler)

handler = app.make_handler()
f = loop.create_server(handler, '0.0.0.0', 8080)
srv = loop.run_until_complete(f)

async def end():
    await handler.finish_connections(1.0)
    srv.close()
    await srv.wait_closed()
    await app.finish()
    redis_pool.close()

print('serving on', srv.sockets[0].getsockname())
try:
    loop.run_forever()
except KeyboardInterrupt:
    pass
finally:
    loop.run_until_complete(end())
loop.close()
