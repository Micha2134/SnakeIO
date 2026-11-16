
import asyncio
import json
import math
import random
import uuid

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pathlib import Path

# =====================================
# GAME SETTINGS
# =====================================

WORLD_WIDTH = 2000
WORLD_HEIGHT = 1200

START_LENGTH = 25
FOOD_COUNT = 100

NORMAL_SPEED = 4
BOOST_SPEED = 7

TICK_RATE = 1 / 30
SEND_RATE = 1 / 15

app = FastAPI()

players = {}
food = []
connections = {}


# =====================================
# HELPERS
# =====================================

def distance(a, b):
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def random_food():
    return {
        "x": random.randint(20, WORLD_WIDTH - 20),
        "y": random.randint(20, WORLD_HEIGHT - 20),
        "size": random.randint(4, 8)
    }


def create_food():
    return [random_food() for _ in range(FOOD_COUNT)]


def create_player():
    x = random.randint(200, WORLD_WIDTH - 200)
    y = random.randint(200, WORLD_HEIGHT - 200)

    body = [
        {"x": x - i * 5, "y": y}
        for i in range(START_LENGTH)
    ]

    return {
        "id": str(uuid.uuid4())[:8],
        "x": x,
        "y": y,
        "angle": 0,
        "body": body,
        "length": START_LENGTH,
        "score": 0,
        "alive": True,
        "boost": False,
        "color": random.choice([
            "#ff4545",
            "#45aaff",
            "#48d878",
            "#ffcc33",
            "#bd70ff",
            "#ff70bd",
            "#ffffff"
        ])
    }


food = create_food()


# =====================================
# GAME MOVEMENT
# =====================================

def move_player(player):
    if not player["alive"]:
        return

    speed = BOOST_SPEED if player["boost"] else NORMAL_SPEED

    player["x"] += math.cos(player["angle"]) * speed
    player["y"] += math.sin(player["angle"]) * speed

    # Keep snake inside the arena
    player["x"] = max(0, min(WORLD_WIDTH, player["x"]))
    player["y"] = max(0, min(WORLD_HEIGHT, player["y"]))

    new_head = {
        "x": player["x"],
        "y": player["y"]
    }

    player["body"].insert(0, new_head)

    # Boost costs a little length
    if player["boost"] and player["length"] > 10:
        player["length"] = max(10, player["length"] - 1)

    player["body"] = player["body"][:player["length"]]


# =====================================
# FOOD COLLISIONS
# =====================================

def check_food(player):
    global food

    for item in food[:]:
        if distance(player, item) < 18:
            food.remove(item)

            player["score"] += 1
            player["length"] += 5

            food.append(random_food())


# =====================================
# SNAKE COLLISIONS
# =====================================

def check_collisions():
    active_players = [
        player for player in players.values()
        if player["alive"]
    ]

    for player in active_players:

        # Wall collision
        if (
            player["x"] <= 0
            or player["x"] >= WORLD_WIDTH
            or player["y"] <= 0
            or player["y"] >= WORLD_HEIGHT
        ):
            player["alive"] = False
            continue

        # Own body collision
        for segment in player["body"][10:]:
            if distance(player, segment) < 10:
                player["alive"] = False
                break

        if not player["alive"]:
            continue

        # Other snake collision
        for other in active_players:
            if other["id"] == player["id"]:
                continue

            for segment in other["body"][1:]:
                if distance(player, segment) < 12:
                    player["alive"] = False
                    break

            if not player["alive"]:
                break


# =====================================
# GAME UPDATE
# =====================================

def update_game():
    for player in players.values():
        move_player(player)

    for player in players.values():
        if player["alive"]:
            check_food(player)

    check_collisions()


# =====================================
# SEND GAME STATE
# =====================================

def get_game_state():
    return {
        "width": WORLD_WIDTH,
        "height": WORLD_HEIGHT,
        "players": [
            {
                "id": player["id"],
                "x": player["x"],
                "y": player["y"],
                "body": player["body"],
                "score": player["score"],
                "alive": player["alive"],
                "color": player["color"]
            }
            for player in players.values()
        ],
        "food": food
    }


async def broadcast():
    if not connections:
        return

    message = get_game_state()

    disconnected = []

    for player_id, websocket in list(connections.items()):
        try:
            await websocket.send_json(message)
        except Exception:
            disconnected.append(player_id)

    for player_id in disconnected:
        connections.pop(player_id, None)
        players.pop(player_id, None)


# =====================================
# MAIN GAME LOOP
# =====================================

async def game_loop():
    send_counter = 0

    while True:
        update_game()

        send_counter += 1

        if send_counter >= 2:
            await broadcast()
            send_counter = 0

        await asyncio.sleep(TICK_RATE)


@app.on_event("startup")
async def startup():
    app.state.game_task = asyncio.create_task(game_loop())


@app.on_event("shutdown")
async def shutdown():
    app.state.game_task.cancel()


# =====================================
# WEB PAGE
# =====================================

@app.get("/")
async def home():
    return FileResponse(Path(__file__).parent / "index.html")


# =====================================
# WEBSOCKET CONNECTION
# =====================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    player = create_player()
    player_id = player["id"]

    players[player_id] = player
    connections[player_id] = websocket

    try:
        await websocket.send_json({
            "type": "welcome",
            "id": player_id
        })

        while True:
            message = await websocket.receive_text()

            try:
                data = json.loads(message)

                if "angle" in data:
                    player["angle"] = float(data["angle"])

                if "boost" in data:
                    player["boost"] = bool(data["boost"])

            except (ValueError, TypeError, json.JSONDecodeError):
                pass

    except WebSocketDisconnect:
        pass

    finally:
        connections.pop(player_id, None)
        players.pop(player_id, None)
