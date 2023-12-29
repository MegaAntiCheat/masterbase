from litestar import Litestar, websocket_listener, get

from uuid import uuid4


@get("/session_id")
def get_session_id() -> dict[str, int]:
    session_id = uuid4().int
    return session_id

@websocket_listener("/demos")
async def demo_session(data: dict[str, str]) -> dict[str, str]:
    print(data)




app = Litestar(route_handlers=[demo_session])
