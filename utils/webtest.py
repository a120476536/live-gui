import asyncio, websockets
async def t():
    try:
        async with websockets.connect("ws://127.0.0.1:8765") as ws:
            print("连接成功，服务端说:", await ws.recv())
    except Exception as e:
        print("连接失败 =>", repr(e))
asyncio.run(t())