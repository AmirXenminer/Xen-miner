import os
import json
import time
import hmac
import hashlib
import sqlite3

from urllib.parse import parse_qsl
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)

CORS(
    app,
    resources={r"/api/*": {"origins": [
        "https://amirxenminer.github.io"
    ]}}
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_NAME = os.getenv("DB_PATH", "xenminer.db")

REWARDS = {
    1: 1,
    2: 2,
    3: 4,
    4: 6,
    5: 9
}

MINING_TIME = 24 * 60 * 60


def database():
    con = sqlite3.connect(DB_NAME)
    con.row_factory = sqlite3.Row
    return con


def setup_database():
    con = database()

    con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            mining_started INTEGER DEFAULT 0
        )
    """)

    con.commit()
    con.close()


def verify_telegram(init_data):
    if not BOT_TOKEN or not init_data:
        return None

    try:
        data = dict(parse_qsl(
            init_data,
            keep_blank_values=True
        ))

        received_hash = data.pop("hash")
        auth_date = int(data["auth_date"])

        if time.time() - auth_date > 86400:
            return None

        check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(data.items())
        )

        secret = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret,
            check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            return None

        user = json.loads(data["user"])
        return int(user["id"])

    except Exception:
        return None


def get_user():
    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        ""
    )

    return verify_telegram(init_data)


@app.route("/api/status")
def status():
    user_id = get_user()

    if user_id is None:
        return jsonify({"error": "Unauthorized"}), 401

    con = database()

    con.execute(
        "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
        (user_id,)
    )

    user = con.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    con.commit()
    con.close()

    now = int(time.time())
    started = user["mining_started"]

    remaining = (
        max(0, started + MINING_TIME - now)
        if started else 0
    )

    return jsonify({
        "balance": user["balance"],
        "level": user["level"],
        "mining_started": started,
        "remaining": remaining,
        "reward": REWARDS[user["level"]],
        "ready": bool(started and remaining == 0)
    })


@app.route("/api/start", methods=["POST"])
def start_mining():
    user_id = get_user()

    if user_id is None:
        return jsonify({"error": "Unauthorized"}), 401

    con = database()
    con.execute("BEGIN IMMEDIATE")

    user = con.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if user is None:
        con.execute(
            "INSERT INTO users (user_id) VALUES (?)",
            (user_id,)
        )

        user = con.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()

    if user["mining_started"] and (
        time.time() < user["mining_started"] + MINING_TIME
    ):
        con.rollback()
        con.close()

        return jsonify({
            "error": "Mining already active"
        }), 400

    con.execute(
        "UPDATE users SET mining_started = ? WHERE user_id = ?",
        (int(time.time()), user_id)
    )

    con.commit()
    con.close()

    return jsonify({
        "message": "Mining started"
    })


@app.route("/api/claim", methods=["POST"])
def claim_reward():
    user_id = get_user()

    if user_id is None:
        return jsonify({"error": "Unauthorized"}), 401

    con = database()
    con.execute("BEGIN IMMEDIATE")

    user = con.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if user is None or not user["mining_started"]:
        con.rollback()
        con.close()

        return jsonify({
            "error": "Mining not started"
        }), 400

    if time.time() < user["mining_started"] + MINING_TIME:
        con.rollback()
        con.close()

        return jsonify({
            "error": "Mining is not finished"
        }), 400

    reward = REWARDS[user["level"]]

    con.execute(
        """
        UPDATE users
        SET balance = balance + ?, mining_started = 0
        WHERE user_id = ?
        """,
        (reward, user_id)
    )

    con.commit()
    con.close()

    return jsonify({
        "message": "Reward claimed",
        "reward": reward
    })


@app.route("/")
def home():
    return jsonify({
        "app": "XEN MINER",
        "status": "online"
    })


if __name__ == "__main__":
    setup_database()

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
  )
