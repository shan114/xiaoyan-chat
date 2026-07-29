import streamlit as st
import streamlit.components.v1 as components
import re
import time
import sqlite3
from main import (
    run_agent_with_rag,
    run_agent_with_tools,
    load_recent_history,
    save_message,
    init_app,
    generate_room_code,
    DEFAULT_PERSONA,
    get_rag_engine,
    skill_manager
)

st.set_page_config(
    page_title="小言 · AI 群聊",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ===================== 移动端响应式 CSS =====================
st.markdown("""
<style>
/* 全局：移动优先 */
:root {
    --primary: #ff6b6b;
    --bg: #f5f5f5;
    --card-bg: #ffffff;
    --text: #333333;
    --secondary: #888888;
    --bubble-user: #e3f2fd;
    --bubble-ai: #fff3e0;
}

/* 深色模式适配 */
@media (prefers-color-scheme: dark) {
    :root {
        --bg: #1a1a2e;
        --card-bg: #16213e;
        --text: #e0e0e0;
        --secondary: #aaaaaa;
        --bubble-user: #1a3a5c;
        --bubble-ai: #3d2e1e;
    }
}

/* 隐藏 Streamlit 默认元素 */
#MainMenu {display: none !important;}
header[data-testid="stHeader"] {display: none !important;}
footer {display: none !important;}
.stDeployButton {display: none !important;}
div[data-testid="stToolbar"] {display: none !important;}

/* 主容器 */
.main .block-container {
    padding: 0.5rem !important;
    max-width: 100% !important;
}

/* 标题栏 - 固定顶部 */
.app-header {
    position: sticky;
    top: 0;
    z-index: 100;
    background: var(--card-bg);
    padding: 10px 16px;
    border-bottom: 1px solid rgba(0,0,0,0.1);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
}
.app-header .title {
    font-size: 18px;
    font-weight: 700;
    color: var(--text);
    white-space: nowrap;
}
.app-header .room-badge {
    font-size: 12px;
    color: var(--secondary);
    background: rgba(0,0,0,0.05);
    padding: 4px 10px;
    border-radius: 12px;
    cursor: pointer;
    user-select: all;
}
.app-header .room-badge:hover {
    background: rgba(255,107,107,0.15);
}

/* 聊天消息气泡 */
.chat-bubble {
    max-width: 85%;
    margin: 6px 0;
    padding: 10px 14px;
    border-radius: 18px;
    font-size: 15px;
    line-height: 1.55;
    word-break: break-word;
    animation: fadeInUp 0.25s ease-out;
}
.chat-bubble.user {
    margin-left: auto;
    background: var(--bubble-user);
    border-bottom-right-radius: 4px;
}
.chat-bubble.ai {
    margin-right: auto;
    background: var(--bubble-ai);
    border-bottom-left-radius: 4px;
}
.chat-sender {
    font-size: 12px;
    color: var(--secondary);
    margin-bottom: 2px;
}
.chat-sender.user-sender {
    text-align: right;
}

/* 底部输入栏 - 固定 */
.chat-input-bar {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    background: var(--card-bg);
    padding: 8px 12px;
    padding-bottom: max(8px, env(safe-area-inset-bottom));
    border-top: 1px solid rgba(0,0,0,0.1);
    z-index: 100;
    display: flex;
    gap: 8px;
    align-items: center;
}

/* 消息列表区域 - 留出顶部和底部空间 */
.chat-messages {
    padding: 10px 12px;
    padding-bottom: 140px;
    padding-top: 5px;
}

/* 按钮样式 */
.mobile-btn {
    border: none;
    border-radius: 20px;
    padding: 8px 16px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
    background: var(--primary);
    color: white;
    white-space: nowrap;
}
.mobile-btn:active {
    transform: scale(0.96);
    opacity: 0.85;
}
.mobile-btn.secondary {
    background: transparent;
    color: var(--primary);
    border: 1.5px solid var(--primary);
}

/* 加入页面 - 全屏居中 */
.join-container {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 80vh;
    padding: 20px;
    text-align: center;
}
.join-card {
    background: var(--card-bg);
    border-radius: 20px;
    padding: 30px 24px;
    width: 100%;
    max-width: 400px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.08);
}
.join-card h2 {
    font-size: 28px;
    margin-bottom: 6px;
}
.join-card .subtitle {
    font-size: 14px;
    color: var(--secondary);
    margin-bottom: 24px;
}

/* 功能展示网格 */
.feature-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
    margin-top: 24px;
    text-align: left;
}
.feature-item {
    padding: 10px;
    border-radius: 12px;
    background: rgba(0,0,0,0.03);
    font-size: 13px;
}
.feature-item .emoji {
    font-size: 20px;
    display: block;
    margin-bottom: 4px;
}

/* 模态弹窗 */
.modal-overlay {
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.5);
    z-index: 200;
    display: flex;
    align-items: center;
    justify-content: center;
    animation: fadeIn 0.2s;
}
.modal-card {
    background: var(--card-bg);
    border-radius: 20px;
    padding: 24px;
    width: 90%;
    max-width: 360px;
}
.modal-card h3 {
    margin-top: 0;
}

/* 动画 */
@keyframes fadeInUp {
    from {opacity: 0; transform: translateY(10px);}
    to {opacity: 1; transform: translateY(0);}
}
@keyframes fadeIn {
    from {opacity: 0;}
    to {opacity: 1;}
}
@keyframes pat {
    0%,100%{transform:translateY(0)}
    50%{transform:translateY(-8px)}
}
.pat-hand {
    font-size: 36px;
    animation: pat 0.5s ease-in-out 3;
    display: inline-block;
}

/* 滚动条美化 */
::-webkit-scrollbar {width: 4px;}
::-webkit-scrollbar-track {background: transparent;}
::-webkit-scrollbar-thumb {background: rgba(0,0,0,0.15); border-radius: 4px;}

/* 打字指示器 */
.typing-dots {
    display: flex; gap: 4px; padding: 8px 0;
}
.typing-dots span {
    width: 8px; height: 8px;
    background: var(--secondary);
    border-radius: 50%;
    animation: bounce 1.2s infinite;
}
.typing-dots span:nth-child(2) {animation-delay: 0.2s;}
.typing-dots span:nth-child(3) {animation-delay: 0.4s;}
@keyframes bounce {
    0%,60%,100%{transform:translateY(0)}
    30%{transform:translateY(-8px)}
}

/* 桌面端适配 - 聊天气泡适当缩小 */
@media (min-width: 768px) {
    .chat-bubble {max-width: 65%;}
    .chat-messages {padding: 10px 24px 140px 24px;}
}
@media (min-width: 1024px) {
    .chat-bubble {max-width: 55%;}
    .main .block-container {max-width: 800px !important; margin: 0 auto !important;}
}
</style>
""", unsafe_allow_html=True)

# 初始化（@st.cache_resource 确保只执行一次，后续重跑秒回）
@st.cache_resource
def _do_init():
    init_app()

_do_init()

# ===================== 状态管理 =====================
if "room_id" not in st.session_state:
    st.session_state["room_id"] = generate_room_code()
if "user_name" not in st.session_state:
    st.session_state["user_name"] = ""
if "joined" not in st.session_state:
    st.session_state["joined"] = False
if "persona" not in st.session_state:
    st.session_state["persona"] = None
if "use_rag" not in st.session_state:
    st.session_state["use_rag"] = True
if "last_refresh" not in st.session_state:
    st.session_state["last_refresh"] = time.time()
if "show_menu" not in st.session_state:
    st.session_state["show_menu"] = False
if "show_join_modal" not in st.session_state:
    st.session_state["show_join_modal"] = False
if "join_code_input" not in st.session_state:
    st.session_state["join_code_input"] = ""
if "show_settings" not in st.session_state:
    st.session_state["show_settings"] = False

ROOM_ID = st.session_state["room_id"]
USER_NAME = st.session_state["user_name"]

# ===================== 表情 =====================
EMOJI_MAP = {
    "hug": "emojis/hug.gif", "laugh": "emojis/laugh.gif",
    "cry": "emojis/cry.gif", "love": "emojis/love.gif",
    "ok": "emojis/ok.gif", "think": "emojis/think.gif",
}

def render_content(text):
    parts = re.split(r'(\[emoji:(\w+)\])', text)
    result = []
    for part in parts:
        if part.startswith("[emoji:"):
            name = part[7:-1]
            if name in EMOJI_MAP:
                result.append(f'<img src="{EMOJI_MAP[name]}" width="48" style="vertical-align:middle;">')
            else:
                result.append(part)
        else:
            result.append(part)
    return "".join(result)

def show_pat_animation():
    return '<div class="pat-hand">🤚</div><div style="font-size:13px;color:var(--secondary);margin-top:4px;">小言轻轻拍了拍你~</div>'


# ===================== 未加入 → 登录/注册页面 =====================
if not st.session_state["joined"]:
    # 全屏居中布局
    st.markdown("""
    <div class="join-container">
        <div class="join-card">
            <div style="font-size:48px;margin-bottom:8px;">🔥</div>
            <h2 style="margin-bottom:4px;">小言 · AI 群聊</h2>
            <p class="subtitle">多人实时 AI 智能群聊助手</p>
    """, unsafe_allow_html=True)

    # 昵称输入
    name_input = st.text_input(
        "你的昵称",
        value=st.session_state.get("_name_val", ""),
        placeholder="给自己取个名字吧~",
        label_visibility="collapsed",
        key="mobile_name_input"
    )
    # 保存输入值防止丢失
    st.session_state["_name_val"] = name_input

    # 创建房间按钮
    col_a, col_b = st.columns([1, 1])
    with col_a:
        if st.button("✨ 创建房间", use_container_width=True, key="create_btn"):
            if not name_input.strip():
                st.toast("请先输入昵称", icon="⚠️")
            else:
                new_room = generate_room_code()
                st.session_state["user_name"] = name_input.strip()
                st.session_state["room_id"] = new_room
                st.session_state["joined"] = True
                st.session_state["_name_val"] = ""
                st.rerun()
    with col_b:
        if st.button("🚪 加入房间", use_container_width=True, key="join_btn"):
            if not name_input.strip():
                st.toast("请先输入昵称", icon="⚠️")
            else:
                st.session_state["show_join_modal"] = True

    # 加入房间弹窗
    if st.session_state["show_join_modal"]:
        join_code = st.text_input(
            "粘贴房间号",
            value=st.session_state["join_code_input"],
            placeholder="朋友分享的8位房间号",
            key="modal_join_input"
        )
        st.session_state["join_code_input"] = join_code

        c1, c2 = st.columns(2)
        with c1:
            if st.button("取消", use_container_width=True, key="cancel_join"):
                st.session_state["show_join_modal"] = False
                st.session_state["join_code_input"] = ""
                st.rerun()
        with c2:
            if st.button("确认加入", use_container_width=True, key="confirm_join_modal", type="primary"):
                if join_code.strip():
                    st.session_state["user_name"] = name_input.strip()
                    st.session_state["room_id"] = join_code.strip()
                    st.session_state["joined"] = True
                    st.session_state["show_join_modal"] = False
                    st.session_state["join_code_input"] = ""
                    st.session_state["_name_val"] = ""
                    st.rerun()
                else:
                    st.toast("请输入房间号", icon="⚠️")

    # 功能展示
    st.markdown("""
    <div class="feature-grid">
        <div class="feature-item">
            <span class="emoji">🌤️</span> 天气查询
        </div>
        <div class="feature-item">
            <span class="emoji">🌐</span> 多语翻译
        </div>
        <div class="feature-item">
            <span class="emoji">📚</span> 百科知识
        </div>
        <div class="feature-item">
            <span class="emoji">📈</span> 股票汇率
        </div>
        <div class="feature-item">
            <span class="emoji">💻</span> 代码执行
        </div>
        <div class="feature-item">
            <span class="emoji">🧠</span> RAG 增强
        </div>
    </div>
    </div></div>
    """, unsafe_allow_html=True)

    st.stop()


# ===================== 已加入 → 聊天主界面 =====================

# 顶部栏
col_title, col_actions = st.columns([3, 1])
with col_title:
    st.markdown(f"""
    <div class="app-header" style="border-bottom:none;padding:6px 0;">
        <span class="title">🔥 小言群聊</span>
        <span class="room-badge" onclick="navigator.clipboard.writeText('{ROOM_ID}')" title="点击复制房间号">
            📋 {ROOM_ID}
        </span>
    </div>
    """, unsafe_allow_html=True)
with col_actions:
    c_m, c_r = st.columns(2)
    with c_m:
        if st.button("⚙️", key="menu_toggle", use_container_width=True, help="菜单"):
            st.session_state["show_settings"] = not st.session_state["show_settings"]
    with c_r:
        if st.button("🔄", key="refresh_top", use_container_width=True, help="刷新"):
            st.rerun()

# 设置面板（折叠）
if st.session_state["show_settings"]:
    with st.expander("⚙️ 设置与工具", expanded=True):
        st.caption(f"👤 {USER_NAME}  |  🏠 房间: `{ROOM_ID}`")

        # 复制房间号
        st.code(ROOM_ID, language=None)
        st.caption("💡 把房间号发给朋友，在首页粘贴即可加入")

        # RAG 开关
        st.session_state["use_rag"] = st.toggle(
            "🧠 RAG 知识增强",
            value=st.session_state["use_rag"],
            help="开启后AI回答前检索本地知识库"
        )

        # 身份设定
        current_persona = st.session_state["persona"] or DEFAULT_PERSONA
        st.caption(f"当前身份：{current_persona[:50]}...")
        new_persona = st.text_input("新身份", placeholder="比如：幽默的脱口秀演员", key="persona_mobile")
        if st.button("应用身份", use_container_width=True):
            st.session_state["persona"] = new_persona.strip() if new_persona.strip() else None
            st.toast("身份已更新！", icon="✅")
            st.rerun()

        # 操作按钮
        c3, c4 = st.columns(2)
        with c3:
            if st.button("🗑️ 清空聊天", use_container_width=True):
                conn = sqlite3.connect("chat_history.db")
                conn.execute("DELETE FROM messages WHERE room_id = ?", (ROOM_ID,))
                conn.commit()
                conn.close()
                st.rerun()
        with c4:
            if st.button("🚪 离开房间", use_container_width=True):
                st.session_state["joined"] = False
                st.rerun()

        # 知识库状态
        try:
            rag = get_rag_engine()
            if rag is not None:
                stats = rag.get_stats()
                if stats:
                    st.caption(" | ".join(f"{k}: {v}条" for k, v in stats.items()))
        except Exception:
            pass

# 消息列表
st.markdown('<div class="chat-messages">', unsafe_allow_html=True)

history = load_recent_history(80, room_id=ROOM_ID)
for msg in history:
    role = msg["role"]
    content = msg["content"]
    msg_user_name = msg.get("user_name", "")
    initiative = msg.get("initiative", 0)

    if role == "user":
        sender_html = f'<div class="chat-sender user-sender">💬 {msg_user_name}</div>' if msg_user_name else ""
        st.markdown(f"""
        {sender_html}
        <div class="chat-bubble user">{content}</div>
        """, unsafe_allow_html=True)
    else:
        sender_html = '<div class="chat-sender">🤖 小言</div>'
        st.markdown(f"""
        {sender_html}
        <div class="chat-bubble ai">{render_content(content)}</div>
        """, unsafe_allow_html=True)
        if initiative:
            st.caption("（主动消息）")

st.markdown('</div>', unsafe_allow_html=True)

# 自动刷新逻辑
elapsed = time.time() - st.session_state["last_refresh"]
if elapsed >= 3:
    st.session_state["last_refresh"] = time.time()
    time.sleep(0.3)
    st.rerun()


# ===================== 底部固定输入栏 =====================
st.markdown("""
<div class="chat-input-bar" id="chat-input-bar">
</div>
""", unsafe_allow_html=True)

# 使用 st.chat_input（它在移动端表现较好）
chat_ph = "说点什么... 支持天气/翻译/百科/股票/汇率"
if prompt := st.chat_input(chat_ph):
    # 处理命令
    if prompt.startswith("/identity"):
        identity_desc = prompt[len("/identity"):].strip()
        st.session_state["persona"] = identity_desc if identity_desc else None
        save_message("user", prompt, room_id=ROOM_ID, user_name=USER_NAME)
        reply = f"@{USER_NAME} 身份已切换为：{identity_desc}" if identity_desc else f"@{USER_NAME} 已恢复默认身份~"
        save_message("assistant", reply, room_id=ROOM_ID)
        st.rerun()

    elif prompt.startswith("/rag_add"):
        knowledge = prompt[len("/rag_add"):].strip()
        if knowledge:
            try:
                rag = get_rag_engine()
                if rag is not None:
                    doc_id = rag.add_document(knowledge, {"source_type": "user"})
                    save_message("user", prompt, room_id=ROOM_ID, user_name=USER_NAME)
                    save_message("assistant", f"@{USER_NAME} 已添加知识到知识库 (ID: {doc_id})", room_id=ROOM_ID)
                else:
                    save_message("assistant", f"@{USER_NAME} 知识库尚未就绪，请稍后再试", room_id=ROOM_ID)
            except Exception as e:
                save_message("assistant", f"@{USER_NAME} 添加知识失败: {e}", room_id=ROOM_ID)
            st.rerun()
        else:
            st.toast("请提供知识内容", icon="⚠️")

    elif prompt.startswith("/tools"):
        tool_list = "\n".join(f"• **{n}**: {d}" for n, d in skill_manager.get_tool_list().items())
        save_message("user", prompt, room_id=ROOM_ID, user_name=USER_NAME)
        save_message("assistant", f"当前可用工具：\n{tool_list}", room_id=ROOM_ID)
        st.rerun()

    else:
        # 正常对话
        save_message("user", prompt, room_id=ROOM_ID, user_name=USER_NAME)

        # 显示用户消息
        st.markdown(f"""
        <div class="chat-sender user-sender">💬 {USER_NAME}</div>
        <div class="chat-bubble user">{prompt}</div>
        """, unsafe_allow_html=True)

        # AI 回复
        with st.spinner("小言思考中..."):
            persona = st.session_state.get("persona") or DEFAULT_PERSONA
            if st.session_state["use_rag"]:
                result = run_agent_with_rag(
                    prompt, room_id=ROOM_ID, user_name=USER_NAME, persona=persona
                )
            else:
                result = run_agent_with_tools(
                    prompt, room_id=ROOM_ID, user_name=USER_NAME, persona=persona
                )

        reply = result.get("reply", "小言走神了...")
        action = result.get("action", "none")

        st.markdown(f"""
        <div class="chat-sender">🤖 小言</div>
        <div class="chat-bubble ai">{render_content(reply)}</div>
        """, unsafe_allow_html=True)

        if action == "pat":
            st.markdown(show_pat_animation(), unsafe_allow_html=True)

        st.rerun()


# ===================== 底部提示条 =====================
# 显示自动刷新倒计时（不占用太多空间）
remaining = max(0, 3 - (time.time() - st.session_state["last_refresh"]))
if remaining > 0:
    st.caption(f"⏱️ {remaining:.0f}秒后自动刷新...")

# 通过 JS 实现剪贴板复制（手机端 pyperclip 不可用）
components.html(f"""
<script>
// 监听房间号点击复制
document.addEventListener('click', function(e) {{
    if (e.target.classList.contains('room-badge')) {{
        navigator.clipboard.writeText('{ROOM_ID}').then(function() {{
            // 短暂提示
            e.target.style.background = '#4caf50';
            e.target.textContent = '✅ 已复制!';
            setTimeout(function() {{
                e.target.style.background = '';
                e.target.textContent = '📋 {ROOM_ID}';
            }}, 1500);
        }}).catch(function() {{
            // 降级：选中文本
            var range = document.createRange();
            range.selectNode(e.target);
            window.getSelection().removeAllRanges();
            window.getSelection().addRange(range);
        }});
    }}
}});
</script>
""", height=0)
