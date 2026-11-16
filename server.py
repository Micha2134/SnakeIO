
import asyncio
import json
import math
import random
import uuid
import os
import secrets
import time

import httpx

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse, RedirectResponse, PlainTextResponse
from pathlib import Path
from urllib.parse import urlencode

# =====================================
# DISCORD SETTINGS
# =====================================

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")

DISCORD_REDIRECT_URI = (
    "https://snakeio-omh4.onrender.com/auth/callback"
)

DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_USER_URL = "https://discord.com/api/users/@me"

# Temporary login states and player sessions
login_states = {}
sessions = {}

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
# DISCORD LOGIN
# =====================================

@app.get("/login")
async def discord_login():

    if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET:
        return PlainTextResponse(
            "Discord login is not configured.",
            status_code=500
        )

    state = secrets.token_urlsafe(32)
    login_states[state] = time.time() + 600

    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",
        "state": state
    }

    url = DISCORD_AUTHORIZE_URL + "?" + urlencode(params)

    return RedirectResponse(url)


@app.get("/auth/callback")
async def discord_callback(code: str = None, state: str = None, error: str = None):

    if error:
        return PlainTextResponse(
            "Discord login was cancelled. Please try again."
        )

    if not code or not state:
        return PlainTextResponse(
            "Missing Discord login information.",
            status_code=400
        )

    expiry = login_states.pop(state, None)

    if expiry is None or expiry < time.time():
        return PlainTextResponse(
            "Login expired. Please try again.",
            status_code=400
        )

    try:
        async with httpx.AsyncClient(timeout=15) as client:

            token_response = await client.post(
                DISCORD_TOKEN_URL,
                data={
                    "client_id": DISCORD_CLIENT_ID,
                    "client_secret": DISCORD_CLIENT_SECRET,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": DISCORD_REDIRECT_URI
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded"
                }
            )

            token_response.raise_for_status()
            token_data = token_response.json()

            user_response = await client.get(
                DISCORD_USER_URL,
                headers={
                    "Authorization": (
                        f"Bearer {token_data['access_token']}"
                    )
                }
            )

            user_response.raise_for_status()
            user_data = user_response.json()

        discord_id = user_data["id"]

        # Prefer Discord display name, fall back to username
        discord_name = (
            user_data.get("global_name")
            or user_data.get("username")
            or "Discord Player"
        )

        session_id = secrets.token_urlsafe(32)

        sessions[session_id] = {
            "discord_id": discord_id,
            "name": discord_name
        }

        response = RedirectResponse("/", status_code=303)

        response.set_cookie(
            key="snake_session",
            value=session_id,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 30
        )

        return response

    except Exception:
        return PlainTextResponse(
            "Discord login failed. Please try again.",
            status_code=400
        )


@app.get("/api/me")
async def get_me(request: Request):
    session_id = request.cookies.get("snake_session")
    user = sessions.get(session_id)

    if not user:
        return {"logged_in": False}

    return {
        "logged_in": True,
        "name": user["name"]
    }


@app.get("/logout")
async def logout():

    response = RedirectResponse("/", status_code=303)

    response.delete_cookie("snake_session")

    return response


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


def create_player(discord_id, discord_name):

    x = random.randint(200, WORLD_WIDTH - 200)
    y = random.randint(200, WORLD_HEIGHT - 200)

    body = [
        {"x": x - i * 5, "y": y}
        for i in range(START_LENGTH)
    ]

    return {
        "id": str(uuid.uuid4())[:8],
        "discord_id": discord_id,
        "name": discord_name,
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

    player["x"] = max(0, min(WORLD_WIDTH, player["x"]))
    player["y"] = max(0, min(WORLD_HEIGHT, player["y"]))

    new_head = {
        "x": player["x"],
        "y": player["y"]
    }

    player["body"].insert(0, new_head)

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

        if (
            player["x"] <= 0
            or player["x"] >= WORLD_WIDTH
            or player["y"] <= 0
            or player["y"] >= WORLD_HEIGHT
        ):
            player["alive"] = False
            continue

        for segment in player["body"][10:]:

            if distance(player, segment) < 10:
                player["alive"] = False
                break

        if not player["alive"]:
            continue

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
                "name": player["name"],
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

    return FileResponse(
        Path(__file__).parent / "index.html"
    )


# =====================================
# WEBSOCKET CONNECTION
# =====================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):

    session_id = websocket.cookies.get("snake_session")
    user = sessions.get(session_id)

    if not user:
        await websocket.close(code=4401)
        return

    await websocket.accept()

    player = create_player(
        user["discord_id"],
        user["name"]
    )

    player_id = player["id"]

    players[player_id] = player
    connections[player_id] = websocket

    try:

        await websocket.send_json({
            "type": "welcome",
            "id": player_id,
            "name": player["name"]
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
