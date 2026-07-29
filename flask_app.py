"""
小言 AI 群聊 - Flask Web 版本 (PythonAnywhere 适配)
"""
import os, sys, json, re, threading

from flask import Flask, render_template, request, jsonify, session, Response
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from main import (
    run_agent_with_tools, run_agent_with_rag, save_message, load_recent_history,
    generate_room_code, DEFAULT_PERSONA, init_db
)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "xiaoyan-flask-secret-2026")

_init_done = threading.Event()

def _do_init():
    if _init_done.is_set():
        return
    init_db()
    print("[Flask] 数据库初始化完成")
    _init_done.set()

_do_init()


# ===================== 页面路由 =====================

@app.route("/")
def index():
    """主页：加入/创建房间"""
    return render_template("chat.html")


# ===================== API 路由 =====================

@app.route("/api/chat", methods=["POST"])
def api_chat():
    """发送消息并获取 AI 回复"""
    data = request.get_json(force=True)
    user_input = data.get("message", "").strip()
    room_id = data.get("room_id", "default")
    user_name = data.get("user_name", "匿名")
    use_rag = data.get("use_rag", True)

    if not user_input:
        return jsonify({"error": "消息不能为空"}), 400

    # 保存用户消息
    save_message("user", user_input, room_id=room_id, user_name=user_name)

    # 调用 AI
    try:
        if use_rag:
            result = run_agent_with_rag(user_input, room_id=room_id, user_name=user_name)
        else:
            result = run_agent_with_tools(user_input, room_id=room_id, user_name=user_name)
        reply = result.get("reply", "小言走神了...")
        emotion = result.get("emotion", "neutral")
        action = result.get("action", "none")
    except Exception as e:
        reply = f"抱歉，小言遇到问题了：{type(e).__name__}"
        emotion = "neutral"
        action = "none"
        save_message("assistant", reply, room_id=room_id)

    return jsonify({
        "reply": reply,
        "emotion": emotion,
        "action": action,
        "user_name": user_name,
        "message": user_input
    })


@app.route("/api/history")
def api_history():
    """获取聊天历史"""
    room_id = request.args.get("room_id", "default")
    history = load_recent_history(80, room_id=room_id)
    return jsonify(history)


@app.route("/api/new_room")
def api_new_room():
    """生成新房间号"""
    return jsonify({"room_code": generate_room_code()})


@app.route("/api/clear", methods=["POST"])
def api_clear():
    """清空房间聊天记录"""
    import sqlite3
    from main import DB_NAME
    data = request.get_json(force=True)
    room_id = data.get("room_id", "")
    if room_id:
        conn = sqlite3.connect(DB_NAME)
        conn.execute("DELETE FROM messages WHERE room_id = ?", (room_id,))
        conn.commit()
        conn.close()
    return jsonify({"status": "ok"})


# ===================== 清理函数 =====================

def clean_reply(text):
    text = re.sub(r'\[emoji:\w+\]', '', text)
    text = re.sub(r'<img\s+[^>]*/?>', '', text, flags=re.IGNORECASE)
    return text.strip()


# ===================== WSGI 入口 =====================
application = app

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
