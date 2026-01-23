# MPRIS WebSocket

A WebSocket which exposes MPRIS player metadata and status. Also accepts controls in return.

Recommended to use in combination with:

- [mpris-ws-ha](https://github.com/Yanndroid/mpris-ws-ha)
- [mpris-frontend](https://github.com/Yanndroid/mpris-frontend)

### Features

- updates on every change and every 5 seconds
- player can be controlled from client
- Local art cover paths are available via a http server
- multiple clients
- multiple players (can be added/removed dynamically)

WebSocket port: `8765`  
Art cover port: `8766`

### JSON format

#### Updates

```json
{
  "org.mpris.MediaPlayer2.spotifyd.instance23234": {
    "title": "The Flute Tune - Soulpride Remix",
    "artist": ["Jaycut", "Kolt Siewerts", "Soulpride"],
    "album": "The Flute Tune (Soulpride Remix)",
    "artUrl": "https://i.scdn.co/image/ab67616d0000b273f43f13dd1c3bc2461ad1b943",
    "trackid": "/spotify/track/35qUFKihoQDonhKJP0uakH",
    "length": 323,
    "position": 94,
    "status": "Playing",
    "loop": "None",
    "shuffle": false
  }
}
```

#### Controls

```json
{ "player": "org.mpris.MediaPlayer2.spotifyd.instance23234", "cmd": "seek", "value": 10 }
```

Commands: `play`, `pause`, `playpause`, `next`, `prev`, `stop`, `postion`, `seek`  
\*note: `postion` and `seek` require the value field in seconds
