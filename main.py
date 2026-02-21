import asyncio
from dbus_next.aio import MessageBus, ProxyObject, ProxyInterface
import dataclasses
import websockets
import json
from aiohttp import web
import pathlib

HOST = "0.0.0.0"
PORT_WS = 8765
PORT_ART = 8766

UPDATE_INTERVAL = 5


class MprisMonitor:
    @dataclasses.dataclass
    class MprisPlayer:
        player_obj: ProxyObject
        iface_props: ProxyInterface
        iface_player: ProxyInterface

    @dataclasses.dataclass
    class TrackInfo:
        title: str
        artist: list[str]
        album: str
        artUrl: str
        trackid: str
        length: int
        position: int
        status: str
        loop: str
        shuffle: bool

    def __init__(self):
        self._bus = None
        self._dbus = None

        self._update_callback = None

        self.players: dict[str, MprisMonitor.MprisPlayer] = {}
        self.current_track_infos: dict[str, MprisMonitor.TrackInfo] = {}

    def set_update_callback(self, callback):
        self._update_callback = callback

    async def start(self):
        self._bus = await MessageBus().connect()

        introspection = await self._bus.introspect("org.freedesktop.DBus", "/org/freedesktop/DBus")
        obj = self._bus.get_proxy_object("org.freedesktop.DBus", "/org/freedesktop/DBus", introspection)
        self._dbus = obj.get_interface("org.freedesktop.DBus")

        def name_owner_changed(name, old_owner, new_owner):
            if not name.startswith("org.mpris.MediaPlayer2."):
                return

            if new_owner and not old_owner:
                asyncio.create_task(self._add_player(name))
            elif old_owner and not new_owner:
                asyncio.create_task(self._remove_player(name))

        self._dbus.on_name_owner_changed(name_owner_changed)

        for player_name in await self._get_player_names():
            await self._add_player(player_name)

    async def update_loop(self):
        while True:
            if any(track_info.status == "Playing" for track_info in self.current_track_infos.values()):
                await self.update()
            await asyncio.sleep(UPDATE_INTERVAL)

    async def _get_player_names(self):
        names = await self._dbus.call_list_names()
        player_names = [n for n in names if n.startswith("org.mpris.MediaPlayer2.")]
        return player_names

    def _iface_on_properties_changed(self, interface, changed, invalidated):
        if "PlaybackStatus" in changed or "Metadata" in changed:
            asyncio.create_task(self.update())

    async def _add_player(self, name: str):
        if name in self.players:
            return

        introspection = await self._bus.introspect(name, "/org/mpris/MediaPlayer2")
        player_obj = self._bus.get_proxy_object(name, "/org/mpris/MediaPlayer2", introspection)
        iface_props = player_obj.get_interface("org.freedesktop.DBus.Properties")
        iface_player = player_obj.get_interface("org.mpris.MediaPlayer2.Player")

        iface_props.on_properties_changed(self._iface_on_properties_changed)

        self.players[name] = MprisMonitor.MprisPlayer(player_obj=player_obj, iface_props=iface_props, iface_player=iface_player)
        await self.update()

    async def _remove_player(self, name: str):
        if name in self.players:
            self.players[name].iface_props.off_properties_changed(self._iface_on_properties_changed)
            del self.players[name]
            del self.current_track_infos[name]
            await self.update()

    def _art_url_wrapper(self, artUrl: str, player: str) -> str:
        if not artUrl or artUrl.startswith("file://"):
            return f"http://localhost:{PORT_ART}/art/{player}"  # TODO: add random suffix to avoid caching
        return artUrl

    async def _try(self, func, alt):
        try:
            return await func()
        except Exception:
            return alt

    async def update(self):
        @dataclasses.dataclass
        class MF:
            value: any

        for name, player in self.players.items():
            iface_player = player.iface_player
            metadata = await iface_player.get_metadata()

            self.current_track_infos[name] = MprisMonitor.TrackInfo(
                title=metadata.get("xesam:title", MF("Unknown Title")).value,
                artist=metadata.get("xesam:artist", MF(["Unknown Artist"])).value,
                album=metadata.get("xesam:album", MF("Unknown Album")).value,
                artUrl=self._art_url_wrapper(metadata.get("mpris:artUrl", MF("")).value, name),
                trackid=metadata.get("mpris:trackid", MF("")).value,
                length=int(metadata.get("mpris:length", MF(0)).value / 1_000_000),
                position=int(await self._try(iface_player.get_position, 0) / 1_000_000),
                status=await self._try(iface_player.get_playback_status, "Unknown"),
                loop=(await iface_player.get_loop_status()) if "get_loop_status" in iface_player.__dict__ else "None",
                shuffle=(await iface_player.get_shuffle()) if "get_shuffle" in iface_player.__dict__ else False,
            )

        if self._update_callback:
            await self._update_callback(self.current_track_infos)


class MprisWebSocket:
    def __init__(self):
        self._server = None
        self._clients = set()

        self._connect_callback = None
        self._message_callback = None

    def set_connect_callback(self, callback):
        self._connect_callback = callback

    def set_message_callback(self, callback):
        self._message_callback = callback

    async def start(self):
        self._server = await websockets.serve(self._handle_client, HOST, PORT_WS)

    async def send_all(self, message: str):
        await asyncio.gather(*(client.send(message) for client in self._clients))

    async def _handle_client(self, client):
        self._clients.add(client)
        try:
            if self._connect_callback:
                await self._connect_callback(client)

            async for message in client:
                await self._handle_message(client, message)

            await client.wait_closed()
        finally:
            self._clients.remove(client)

    async def _handle_message(self, client, message):
        data = json.loads(message)
        if self._message_callback:
            await self._message_callback(client, data)


class ArtServer:
    def __init__(self):
        app = web.Application()
        app.router.add_get("/art/{player}", self._handle_art)
        self._runner = web.AppRunner(app)

        self._request_callback = None

    def set_request_callback(self, callback):
        self._request_callback = callback

    async def start(self):
        await self._runner.setup()
        site = web.TCPSite(self._runner, HOST, PORT_ART)
        await site.start()

    async def _handle_art(self, request):
        player = request.match_info["player"]
        if self._request_callback:
            path = await self._request_callback(player)
            if path:
                return web.FileResponse(path=path, headers={"Content-Type": "image/jpeg"})
        return web.Response(status=404)  # TODO: return a placeholder image


async def main():
    websocket = MprisWebSocket()
    art_server = ArtServer()
    monitor = MprisMonitor()

    async def on_client_connect(client):
        print(f"WS client connected: {client.remote_address}")

        await monitor.update()
        await client.send(json.dumps({name: dataclasses.asdict(info) for name, info in monitor.current_track_infos.items()}))

    async def on_message(client, data):
        print(f"WS message from {client.remote_address}: {data}")

        player = data.get("player")
        command = data.get("cmd")
        if player and command:
            mpris_player = monitor.players.get(player)
            track_info = monitor.current_track_infos.get(player)
            if mpris_player:
                iface_player = mpris_player.iface_player
                if command == "play":
                    await iface_player.call_play()
                elif command == "pause":
                    await iface_player.call_pause()
                elif command == "playpause":
                    await iface_player.call_play_pause()
                elif command == "next":
                    await iface_player.call_next()
                elif command == "prev":
                    await iface_player.call_previous()
                elif command == "stop":
                    await iface_player.call_stop()
                elif command == "position":
                    position = data.get("value", 0) * 1_000_000
                    await iface_player.call_set_position(track_info.trackid or "/", position)
                elif command == "seek":
                    offset = data.get("value", 0) * 1_000_000
                    await iface_player.call_seek(offset)

    async def on_art_request(player):
        print(f"Art request for player: {player}")

        mpris_player = monitor.players.get(player)
        if mpris_player:
            artUrl = (await mpris_player.iface_player.get_metadata()).get("mpris:artUrl").value
            if artUrl.startswith("file://"):
                path = pathlib.Path(artUrl[7:])
                if path.exists():
                    return path
        return pathlib.Path("placeholder_art.png")

    async def on_track_update(track_infos):
        print(f"MPRIS update ({len(track_infos)} players):")
        for name, info in track_infos.items():
            print(f" - {name}: {info.title} ({info.album}) by {', '.join(info.artist)} [{info.status}: {info.position}/{info.length}s] {info.artUrl}")

        await websocket.send_all(json.dumps({name: dataclasses.asdict(info) for name, info in track_infos.items()}))

    websocket.set_message_callback(on_message)
    websocket.set_connect_callback(on_client_connect)
    art_server.set_request_callback(on_art_request)
    monitor.set_update_callback(on_track_update)

    await websocket.start()
    await art_server.start()
    await monitor.start()

    await monitor.update_loop()
    await websocket._server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
