import os
import re
import json
import html as html_mod
import sqlite3
import uuid
import io
import sys
import traceback
import threading
import requests
from datetime import datetime
from openai import OpenAI
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

# 加载 .env
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# 设置 Hugging Face 镜像（国内网络优化，必须在 import rag_engine 之前）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from skill_manager import skill_manager
from tools import TOOLS

# RAG / MCP —— 延迟导入（不在 import 阶段创建实例）
# 它们通过 _get_rag() / _get_mcp() 在首次使用时才初始化

def _get_rag():
    """懒加载 RAG 引擎（首次调用时加载 90MB embedding 模型）"""
    try:
        from rag_engine import get_rag_engine
        return get_rag_engine()
    except Exception as e:
        print(f"[RAG] 初始化失败: {e}")
        return None

def _get_mcp():
    """懒加载 MCP 桥接"""
    try:
        from mcp_bridge import get_mcp_bridge
        return get_mcp_bridge()
    except Exception as e:
        print(f"[MCP] 初始化失败: {e}")
        return None

# 初始化状态
_init_done = False
_init_lock = threading.Lock()

def get_rag_engine():
    """获取 RAG 引擎（兼容旧代码，供 web_fireboy.py 调用）"""
    return _get_rag()

# ===================== OpenAI 客户端 =====================
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
)
MODEL = os.getenv("MODEL_NAME", "deepseek-chat")

# ===================== 默认人格 =====================
DEFAULT_PERSONA = "你是小言，一个住在手机里的可爱智能小火人。性格温暖、活泼，喜欢用颜文字和表情符号。"

# 全局人格（调度器等模块级调用时使用）
_current_persona = None

def set_persona(new_persona: str | None):
    global _current_persona
    _current_persona = new_persona

def get_persona() -> str:
    if _current_persona:
        return _current_persona
    return DEFAULT_PERSONA


# ===================== 系统提示词构建 =====================
def build_system_prompt(persona: str, rag_context: str = "") -> str:
    """动态构建完整系统提示词，可注入 RAG 检索上下文"""
    available_tools = skill_manager.get_tool_list()
    tool_descriptions = "\n".join(f"- {name}: {desc}" for name, desc in available_tools.items())

    rag_block = ""
    if rag_context.strip():
        rag_block = f"\n{rag_context}\n"

    return f"""{persona}
{rag_block}
⚠️ 核心规则：你必须严格使用工具获取实时数据。以下情况【禁止编造答案】：
- 天气 → 必须调用 get_weather
- 时间 → 必须调用 get_time
- 新闻/百科/资讯 → 必须调用 web_search 或 wikipedia_lookup
- 数学计算 → 必须调用 calculate
- 翻译 → 必须调用 translate_text
- 汇率 → 必须调用 exchange_rate
- 股票 → 必须调用 stock_lookup
- 词典释义 → 必须调用 dictionary_lookup
- 网页内容 → 必须调用 url_fetch
- 知识库 → 必须调用 rag_search

当前是群聊场景。每条用户消息以「[用户名]: 消息内容」格式发送，你需要区分不同说话者，可以在回复中 @用户名 来精准回应。

你可以使用以下工具：
{tool_descriptions}

最终回复必须是纯 JSON 格式（不要用 markdown 代码块包裹），包含：
- reply: 你的回复文本
- emotion: 用户情绪 (neutral/happy/sad/angry)
- action: 额外动作 ("pat" 或 "none")
如果你觉得有人情绪低落，请设置 action 为 "pat" 并在 reply 中安慰。"""


# ===================== 数据库 =====================
# DATA_DIR 用于云部署持久化存储（Fly.io /data 卷），本地默认当前目录
DB_NAME = os.path.join(os.environ.get("DATA_DIR", os.path.dirname(__file__)), "chat_history.db")

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS messages
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  role TEXT NOT NULL,
                  content TEXT NOT NULL,
                  room_id TEXT DEFAULT 'default',
                  user_name TEXT DEFAULT '',
                  initiative INTEGER DEFAULT 0,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    c.execute("PRAGMA table_info(messages)")
    cols = [col[1] for col in c.fetchall()]
    for col_name in ['initiative', 'session_id', 'room_id', 'user_name']:
        if col_name not in cols:
            if col_name == 'session_id':
                c.execute("ALTER TABLE messages ADD COLUMN session_id TEXT DEFAULT 'default'")
            elif col_name == 'room_id':
                c.execute("ALTER TABLE messages ADD COLUMN room_id TEXT DEFAULT 'default'")
            elif col_name == 'user_name':
                c.execute("ALTER TABLE messages ADD COLUMN user_name TEXT DEFAULT ''")
            elif col_name == 'initiative':
                c.execute("ALTER TABLE messages ADD COLUMN initiative INTEGER DEFAULT 0")
    c.execute("UPDATE messages SET room_id = session_id WHERE room_id = 'default' AND session_id != 'default'")
    conn.commit()
    conn.close()

def save_message(role, content, initiative=0, room_id="default", user_name=""):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "INSERT INTO messages (role, content, initiative, room_id, user_name) VALUES (?, ?, ?, ?, ?)",
        (role, content, initiative, room_id, user_name)
    )
    conn.commit()
    conn.close()

def load_recent_history(limit=30, room_id="default"):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "SELECT role, content, initiative, user_name FROM messages WHERE room_id = ? ORDER BY id DESC LIMIT ?",
        (room_id, limit)
    )
    rows = c.fetchall()[::-1]
    conn.close()
    return [{"role": r, "content": c, "initiative": i, "user_name": u} for r, c, i, u in rows]

def generate_room_code() -> str:
    """生成简短房间码"""
    return str(uuid.uuid4())[:8]


# ===================== 实用工具函数（8+ 新工具） =====================

def _safe_request(url: str, timeout: int = 10, **kwargs) -> requests.Response | None:
    """统一的 HTTP 请求封装"""
    try:
        headers = kwargs.pop("headers", {})
        headers.setdefault("User-Agent", "XiaoYan/2.1")
        return requests.get(url, timeout=timeout, headers=headers, **kwargs)
    except Exception as e:
        return None


# --- 已有工具 ---

def search_web(query: str, max_results: int = 5) -> str:
    from duckduckgo_search import DDGS
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            if not results:
                return "未找到相关结果。"
            formatted = []
            for r in results:
                formatted.append(f"标题：{r['title']}\n链接：{r['href']}\n摘要：{r['body']}\n")
            return "\n".join(formatted)
    except Exception as e:
        return f"搜索失败：{str(e)}"

def get_current_time() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S 星期%w")

def get_weather(city: str) -> str:
    try:
        url = f"https://wttr.in/{city}?format=j1"
        resp = _safe_request(url)
        if resp is None:
            return "获取天气失败：网络错误"
        data = resp.json()
        current = data["current_condition"][0]
        weather_desc = current["weatherDesc"][0]["value"]
        temp_c = current["temp_C"]
        humidity = current["humidity"]
        wind_speed = current["windspeedKmph"]
        feels_like = current["FeelsLikeC"]
        area = data.get("nearest_area", [{}])[0].get("areaName", [{}])[0].get("value", city)
        country = data.get("nearest_area", [{}])[0].get("country", [{}])[0].get("value", "")
        location = f"{area}, {country}" if country else area
        return (
            f"📍 {location}\n"
            f"天气：{weather_desc}，温度 {temp_c}°C（体感 {feels_like}°C）\n"
            f"湿度：{humidity}%，风速：{wind_speed} km/h"
        )
    except Exception as e:
        return f"获取「{city}」天气失败：{str(e)}"

def simple_calculate(expression: str) -> str:
    try:
        allowed = set("0123456789.+-*/()% ")
        if not all(c in allowed for c in expression):
            return "错误：表达式包含不允许的字符"
        result = eval(expression, {"__builtins__": {}}, {})
        return f"{expression} = {result}"
    except Exception as e:
        return f"计算出错：{str(e)}"


# --- 新工具：翻译 ---
def translate_text(text: str, target_lang: str = "zh", source_lang: str = "auto") -> str:
    """使用 MyMemory 免费翻译 API"""
    try:
        lang_pair = f"{source_lang}|{target_lang}"
        url = "https://api.mymemory.translated.net/get"
        resp = _safe_request(url, params={"q": text, "langpair": lang_pair})
        if resp is None:
            return "翻译失败：网络错误"
        data = resp.json()
        translated = data.get("responseData", {}).get("translatedText", "")
        match = data.get("responseData", {}).get("match", 0)
        if translated:
            quality = "高精度" if match > 0.8 else "机器翻译"
            return f"[{source_lang} → {target_lang}] ({quality})\n{translated}"
        return "翻译失败：未获取到结果"
    except Exception as e:
        return f"翻译出错：{str(e)}"


# --- 新工具：Wikipedia ---
def wikipedia_lookup(query: str, lang: str = "zh") -> str:
    """查询 Wikipedia 摘要"""
    try:
        url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(query)}"
        resp = _safe_request(url)
        if resp is None:
            return "查询失败：网络错误"
        data = resp.json()
        if "title" not in data or "extract" not in data:
            return f"未找到「{query}」的相关条目"
        extract = data["extract"][:800]
        url_desktop = data.get("content_urls", {}).get("desktop", {}).get("page", "")
        return f"📚 {data['title']}\n{extract}\n🔗 {url_desktop}" if url_desktop else f"📚 {data['title']}\n{extract}"
    except Exception as e:
        return f"Wikipedia 查询失败：{str(e)}"


# --- 新工具：汇率 ---
def exchange_rate(from_currency: str = "USD", to_currency: str = "CNY") -> str:
    """查询实时汇率"""
    try:
        url = f"https://open.er-api.com/v6/latest/{from_currency.upper()}"
        resp = _safe_request(url)
        if resp is None:
            return "查询汇率失败：网络错误"
        data = resp.json()
        rates = data.get("rates", {})
        target = to_currency.upper()
        if target not in rates:
            return f"不支持货币 {target}"
        rate = rates[target]
        updated = data.get("time_last_update_utc", "未知")
        return f"💱 1 {from_currency.upper()} = {rate} {target}\n更新时间：{updated}"
    except Exception as e:
        return f"汇率查询失败：{str(e)}"


# --- 新工具：笑话 ---
def random_joke(category: str = "Any") -> str:
    """获取随机笑话"""
    try:
        url = f"https://v2.jokeapi.dev/joke/{category}?type=single&lang=zh"
        resp = _safe_request(url)
        if resp is None:
            return "获取笑话失败：网络错误"
        data = resp.json()
        if data.get("error"):
            return f"笑话 API 错误：{data.get('message', '未知')}"
        joke = data.get("joke", "今天灵感枯竭，讲不出笑话了 😅")
        cat = data.get("category", "")
        return f"😂 [{cat}] {joke}"
    except Exception as e:
        return f"笑话获取失败：{str(e)}"


# --- 新工具：词典 ---
def dictionary_lookup(word: str, lang: str = "en") -> str:
    """查询英文单词释义（Dictionary API）"""
    try:
        url = f"https://api.dictionaryapi.dev/api/v2/entries/{lang}/{requests.utils.quote(word)}"
        resp = _safe_request(url)
        if resp is None:
            return "查询词典失败：网络错误"
        if resp.status_code == 404:
            return f"未找到单词「{word}」"
        data = resp.json()
        if not data:
            return f"未找到单词「{word}」"

        entry = data[0]
        word_text = entry.get("word", word)
        phonetic = entry.get("phonetic", "")
        lines = [f"📖 {word_text}" + (f" /{phonetic}/" if phonetic else "")]

        for meaning in entry.get("meanings", [])[:3]:
            pos = meaning.get("partOfSpeech", "")
            for i, defn in enumerate(meaning.get("definitions", [])[:2], 1):
                d = defn.get("definition", "")
                example = defn.get("example", "")
                line = f"  [{pos}] {d}"
                if example:
                    line += f"\n    例句：{example}"
                lines.append(line)
        return "\n".join(lines)
    except Exception as e:
        return f"词典查询失败：{str(e)}"


# --- 新工具：股票 ---
def stock_lookup(symbol: str) -> str:
    """查询股票实时价格（Yahoo Finance）"""
    try:
        symbol = symbol.upper().strip()
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
        resp = _safe_request(url)
        if resp is None:
            return "查询股票失败：网络错误"
        data = resp.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return f"未找到股票代码：{symbol}"

        meta = result[0].get("meta", {})
        price = meta.get("regularMarketPrice", "N/A")
        prev_close = meta.get("previousClose", "N/A")
        change = price - prev_close if isinstance(price, (int, float)) and isinstance(prev_close, (int, float)) else 0
        change_pct = (change / prev_close * 100) if prev_close and prev_close != 0 else 0
        name = meta.get("longName", meta.get("shortName", symbol))
        currency = meta.get("currency", "")

        arrow = "📈" if change > 0 else "📉" if change < 0 else "➡️"
        return (
            f"📊 {name} ({symbol})\n"
            f"价格：{price} {currency}\n"
            f"涨跌：{change:+.2f} ({change_pct:+.2f}%) {arrow}"
        )
    except Exception as e:
        return f"股票查询失败：{str(e)}"


# --- 新工具：网页抓取 ---
def url_fetch(target_url: str) -> str:
    """抓取并提取网页文本内容"""
    try:
        if not target_url.startswith("http"):
            target_url = "https://" + target_url
        resp = _safe_request(target_url, timeout=15)
        if resp is None:
            return "抓取失败：网络错误"
        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type:
            # 非 HTML 内容，只返回基本信息
            preview = resp.text[:1000]
            return f"内容类型：{content_type}\n长度：{len(resp.text)} 字符\n\n{preview}..."

        # 简单提取文本：去除 script/style 标签后提取 body 内容
        text = resp.text
        # 去除 script 和 style
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        # 去除 HTML 标签
        text = re.sub(r'<[^>]+>', ' ', text)
        # 解码 HTML 实体
        text = html_mod.unescape(text)
        # 压缩空白
        text = re.sub(r'\s+', ' ', text).strip()
        # 截取前 2000 字符
        if len(text) > 2000:
            text = text[:2000] + f"... (总长度 {len(text)} 字符，仅显示前 2000)"
        return f"📄 {target_url}\n\n{text}"
    except Exception as e:
        return f"抓取出错：{str(e)}"


# --- 新工具：代码执行 ---
def run_code(code: str, language: str = "python") -> str:
    """在沙箱中安全执行 Python 代码"""
    if language.lower() != "python":
        return "目前仅支持 Python 代码执行"

    # 禁止危险操作
    forbidden = ["import os", "import sys", "import subprocess", "__import__",
                 "exec(", "eval(", "open(", "compile(", "globals(", "locals(",
                 "getattr(", "setattr(", "delattr(", "__builtins__", "importlib"]
    code_lower = code.lower()
    for kw in forbidden:
        if kw in code_lower:
            return f"错误：代码包含禁用的操作 ({kw})"

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        exec(code, {"__builtins__": {
            "print": print, "range": range, "len": len, "str": str, "int": int,
            "float": float, "list": list, "dict": dict, "set": set, "tuple": tuple,
            "bool": bool, "abs": abs, "max": max, "min": min, "sum": sum,
            "sorted": sorted, "round": round, "enumerate": enumerate, "zip": zip,
            "map": map, "filter": filter, "type": type, "isinstance": isinstance,
            "True": True, "False": False, "None": None,
        }}, {})
        output = sys.stdout.getvalue()
        return f"✅ 执行成功\n输出：\n{output}" if output else "✅ 执行成功（无输出）"
    except Exception as e:
        return f"❌ 执行错误：{type(e).__name__}: {str(e)}"
    finally:
        sys.stdout = old_stdout


# --- 新工具：RAG 知识库检索 ---
def rag_search(query: str, top_k: int = 3) -> str:
    """从本地知识库中检索相关文档"""
    rag = _get_rag()
    if rag is None:
        return "知识库尚未就绪，请稍后再试。"
    try:
        context = rag.search_context(query, top_k=top_k)
        if not context.strip():
            return "知识库中暂无相关内容。你可以用 /rag_add 命令添加知识。"
        return context
    except Exception as e:
        return f"知识库检索失败：{str(e)}"


# ===================== 注册所有 Skill =====================

# --- 基础工具 ---
skill_manager.register(
    "web_search", "在互联网上搜索信息。当用户询问新闻、百科、实时资讯等需要联网查询的内容时使用。",
    {"type": "object", "properties": {
        "query": {"type": "string", "description": "搜索关键词"},
        "max_results": {"type": "integer", "description": "结果数", "default": 5}
    }, "required": ["query"]}, search_web)

skill_manager.register(
    "get_time", "获取当前日期和时间。当用户问「现在几点」「今天几号」时使用。",
    {"type": "object", "properties": {}}, get_current_time)

skill_manager.register(
    "get_weather", "查询指定城市的实时天气（温度、湿度、风速、体感温度）。必调用，禁止编造。",
    {"type": "object", "properties": {
        "city": {"type": "string", "description": "城市名称，如 北京、上海、tokyo"}
    }, "required": ["city"]}, get_weather)

skill_manager.register(
    "calculate", "安全计算数学表达式。当用户需要算数、计算时使用。",
    {"type": "object", "properties": {
        "expression": {"type": "string", "description": "数学表达式，如 (1+2)*3"}
    }, "required": ["expression"]}, simple_calculate)

# --- 新工具 ---
skill_manager.register(
    "translate_text", "翻译文本。源语言自动检测，目标语言默认中文(zh)。支持 zh/en/ja/ko/fr/de/es 等。",
    {"type": "object", "properties": {
        "text": {"type": "string", "description": "要翻译的文本"},
        "target_lang": {"type": "string", "description": "目标语言代码，默认 zh", "default": "zh"},
        "source_lang": {"type": "string", "description": "源语言代码，默认 auto", "default": "auto"}
    }, "required": ["text"]}, translate_text)

skill_manager.register(
    "wikipedia_lookup", "查询 Wikipedia/维基百科词条摘要。适合百科知识、人物、事件查询。",
    {"type": "object", "properties": {
        "query": {"type": "string", "description": "搜索关键词"},
        "lang": {"type": "string", "description": "语言代码 zh/en", "default": "zh"}
    }, "required": ["query"]}, wikipedia_lookup)

skill_manager.register(
    "exchange_rate", "查询实时汇率。默认查询美元兑人民币。",
    {"type": "object", "properties": {
        "from_currency": {"type": "string", "description": "源货币代码，默认 USD", "default": "USD"},
        "to_currency": {"type": "string", "description": "目标货币代码，默认 CNY", "default": "CNY"}
    }}, exchange_rate)

skill_manager.register(
    "random_joke", "获取一个随机笑话/段子，可作为群聊娱乐。",
    {"type": "object", "properties": {
        "category": {"type": "string", "description": "笑话类别 Any/Dark/Pun/Programming", "default": "Any"}
    }}, random_joke)

skill_manager.register(
    "dictionary_lookup", "查询英文单词的释义、音标和例句。",
    {"type": "object", "properties": {
        "word": {"type": "string", "description": "要查询的英文单词"},
        "lang": {"type": "string", "description": "语言代码，默认 en", "default": "en"}
    }, "required": ["word"]}, dictionary_lookup)

skill_manager.register(
    "stock_lookup", "查询股票实时价格。输入股票代码（如 AAPL、0700.HK、600519.SS）。",
    {"type": "object", "properties": {
        "symbol": {"type": "string", "description": "股票代码"}
    }, "required": ["symbol"]}, stock_lookup)

skill_manager.register(
    "url_fetch", "抓取并提取网页文本内容。用于获取文章、新闻等页面信息。",
    {"type": "object", "properties": {
        "target_url": {"type": "string", "description": "目标网页 URL"}
    }, "required": ["target_url"]}, url_fetch)

skill_manager.register(
    "run_code", "在安全沙箱中执行 Python 代码。支持 print、数学运算、数据结构等。",
    {"type": "object", "properties": {
        "code": {"type": "string", "description": "Python 代码"},
        "language": {"type": "string", "description": "语言，默认 python", "default": "python"}
    }, "required": ["code"]}, run_code)

skill_manager.register(
    "rag_search", "从本地知识库检索相关文档。当需要查资料、专业知识时使用。",
    {"type": "object", "properties": {
        "query": {"type": "string", "description": "搜索查询"},
        "top_k": {"type": "integer", "description": "返回文档数", "default": 3}
    }, "required": ["query"]}, rag_search)

# --- 原有业务工具 ---
for name, info in TOOLS.items():
    skill_manager.register(name, info["description"], info["parameters"], info["func"])

# --- MCP 工具注册 ---
def _register_mcp_tools():
    """注册所有 MCP 工具到 skill_manager（后台运行）"""
    mcp = _get_mcp()
    if mcp is None:
        return
    try:
        mcp.start()
        for tool_name, description in mcp.all_tools.items():
            def make_caller(full_name):
                def caller(**kwargs):
                    return _get_mcp().call_tool(full_name, kwargs)
                return caller
            skill_manager.register(
                tool_name,
                f"[MCP] {description}",
                {"type": "object", "properties": {}},
                make_caller(tool_name),
                source="mcp"
            )
        if mcp.connected_servers:
            print(f"[MCP] 已注册工具，来源: {mcp.connected_servers}")
    except Exception as e:
        print(f"[MCP] 注册失败: {e}")


# ===================== 核心推理函数 =====================
def _parse_ai_json(content: str) -> dict:
    text = content.strip()
    # 尝试提取 markdown 代码块中或混杂的 JSON
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    # 用花括号定位 JSON 对象
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        text = text[start:end+1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"reply": content, "emotion": "neutral", "action": "none"}


def _clean_reply(text: str) -> str:
    """清洗 AI 回复：移除损坏的表情标记和图片标签"""
    # 去除 [emoji:xxx] 标记
    text = re.sub(r'\[emoji:\w+\]', '', text)
    # 去除 <img ...> 标签
    text = re.sub(r'<img\s+[^>]*/?>', '', text)
    # 压缩多余空白
    text = re.sub(r'\s{2,}', ' ', text).strip()
    return text


def run_agent_with_tools(user_input: str, room_id: str = "default",
                         user_name: str = "", persona: str = "") -> dict:
    """核心 Agent 推理：RAG 上下文 + MCP 工具调用"""
    system_prompt = build_system_prompt(persona or DEFAULT_PERSONA)

    # 构建消息列表
    history = load_recent_history(30, room_id=room_id)
    messages = [{"role": "system", "content": system_prompt}]
    for h in history:
        h_name = h.get("user_name", "")
        if h["role"] == "user" and h_name:
            messages.append({"role": "user", "content": f"[{h_name}]: {h['content']}"})
        else:
            messages.append({"role": h["role"], "content": h["content"]})

    display_input = f"[{user_name}]: {user_input}" if user_name else user_input
    messages.append({"role": "user", "content": display_input})

    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=skill_manager.get_openai_tools_schema(),
        tool_choice="auto"
    )
    assistant_msg = response.choices[0].message

    max_loops = 8
    for _ in range(max_loops):
        if not assistant_msg.tool_calls:
            content = assistant_msg.content or ""
            result = _parse_ai_json(content)
            reply = _clean_reply(result.get("reply", ""))
            result["reply"] = reply
            save_message("assistant", reply, room_id=room_id)
            return result

        messages.append({
            "role": "assistant",
            "content": assistant_msg.content,
            "tool_calls": [{
                "id": tc.id, "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments}
            } for tc in assistant_msg.tool_calls]
        })

        for tc in assistant_msg.tool_calls:
            func_name = tc.function.name
            args = json.loads(tc.function.arguments)
            tool_result = skill_manager.call(func_name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": tool_result
            })

        response = client.chat.completions.create(
            model=MODEL, messages=messages,
            tools=skill_manager.get_openai_tools_schema(), tool_choice="auto"
        )
        assistant_msg = response.choices[0].message

    content = assistant_msg.content or "小言有点累了，晚点再聊哦~"
    save_message("assistant", content, room_id=room_id)
    return {"reply": content, "emotion": "neutral", "action": "none"}


def run_agent_with_rag(user_input: str, room_id: str = "default",
                        user_name: str = "", persona: str = "") -> dict:
    """带 RAG 增强的 Agent 推理：先检索知识库，将上下文注入提示词"""
    # 先从知识库检索相关上下文
    rag_context = ""
    rag = _get_rag()
    if rag is not None:
        try:
            rag_ctx = rag.search_context(user_input, top_k=3)
            if rag_ctx.strip():
                rag_context = rag_ctx
        except Exception as e:
            print(f"[RAG] 检索跳过: {e}")

    # 用 RAG 上下文重建提示词
    system_prompt = build_system_prompt(persona or DEFAULT_PERSONA, rag_context=rag_context)

    history = load_recent_history(30, room_id=room_id)
    messages = [{"role": "system", "content": system_prompt}]
    for h in history:
        h_name = h.get("user_name", "")
        if h["role"] == "user" and h_name:
            messages.append({"role": "user", "content": f"[{h_name}]: {h['content']}"})
        else:
            messages.append({"role": h["role"], "content": h["content"]})

    display_input = f"[{user_name}]: {user_input}" if user_name else user_input
    messages.append({"role": "user", "content": display_input})

    response = client.chat.completions.create(
        model=MODEL, messages=messages,
        tools=skill_manager.get_openai_tools_schema(), tool_choice="auto"
    )
    assistant_msg = response.choices[0].message

    max_loops = 8
    for _ in range(max_loops):
        if not assistant_msg.tool_calls:
            content = assistant_msg.content or ""
            result = _parse_ai_json(content)
            reply = _clean_reply(result.get("reply", ""))
            result["reply"] = reply
            save_message("assistant", reply, room_id=room_id)
            return result

        messages.append({
            "role": "assistant", "content": assistant_msg.content,
            "tool_calls": [{
                "id": tc.id, "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments}
            } for tc in assistant_msg.tool_calls]
        })

        for tc in assistant_msg.tool_calls:
            func_name = tc.function.name
            args = json.loads(tc.function.arguments)
            tool_result = skill_manager.call(func_name, args)
            messages.append({
                "role": "tool", "tool_call_id": tc.id, "content": tool_result
            })

        response = client.chat.completions.create(
            model=MODEL, messages=messages,
            tools=skill_manager.get_openai_tools_schema(), tool_choice="auto"
        )
        assistant_msg = response.choices[0].message

    content = assistant_msg.content or "小言有点累了，晚点再聊哦~"
    save_message("assistant", content, room_id=room_id)
    return {"reply": content, "emotion": "neutral", "action": "none"}


# ===================== 主动话题 =====================
def generate_proactive_topic():
    print("[主动消息] 开始生成话题...")
    system_prompt = build_system_prompt(DEFAULT_PERSONA)
    history = load_recent_history(10)
    prompt = "你刚刚主动想和大家聊点新话题，请基于对话历史说一句轻松有趣的开场白。直接给出JSON回复。"
    messages = [{"role": "system", "content": system_prompt}]
    for h in history:
        h_name = h.get("user_name", "")
        if h["role"] == "user" and h_name:
            messages.append({"role": "user", "content": f"[{h_name}]: {h['content']}"})
        else:
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": prompt})
    try:
        response = client.chat.completions.create(model=MODEL, messages=messages, temperature=1.0)
        content = response.choices[0].message.content
        result = json.loads(content)
        reply = result.get("reply", content)
    except Exception as e:
        print(f"[主动消息] 失败: {e}")
        reply = "在吗？小言想你们了呀 (´,,•ω•,,)♡"
    save_message("assistant", reply, initiative=1)
    print(f"[主动消息] 已存入: {reply[:50]}...")


# ===================== 初始化 =====================
def _bg_init_rag():
    """后台线程：加载 RAG 模型并注入内置知识（耗时操作）"""
    try:
        rag = _get_rag()
        if rag is not None:
            rag.seed_builtin_knowledge()
            print(f"[RAG] 就绪 | 模型: {rag.model_name} | 降级: {rag.is_fallback}")
    except Exception as e:
        print(f"[RAG] 后台初始化失败: {e}")

def _bg_init_mcp():
    """后台线程：连接 MCP Server（耗时操作）"""
    try:
        _register_mcp_tools()
    except Exception as e:
        print(f"[MCP] 后台初始化失败: {e}")

def init_app():
    """同步 + 异步两阶段初始化
    Streamlit 调用此函数：先用 @st.cache_resource 缓存，避免每次重跑
    - 阶段1（同步，秒级）：DB、调度器
    - 阶段2（后台线程，慢）：RAG 模型加载、MCP 连接
    """
    global _init_done
    with _init_lock:
        if _init_done:
            return

        # 阶段1：快速同步初始化
        init_db()
        if not scheduler.running:
            try:
                scheduler.add_job(generate_proactive_topic, 'interval', minutes=30, id='proactive')
                scheduler.start()
                print("[调度器] 已启动（每30分钟）")
            except Exception as e:
                print(f"[调度器] 启动失败: {e}")

        print(f"[启动] 小言就绪 | 工具数: {len(skill_manager.tools)} | RAG/MCP 后台加载中...")

        # 阶段2：后台异步初始化（不阻塞）
        threading.Thread(target=_bg_init_rag, daemon=True, name="RAG-Init").start()
        threading.Thread(target=_bg_init_mcp, daemon=True, name="MCP-Init").start()

        _init_done = True


# ===================== 调度器 =====================
scheduler = BackgroundScheduler()
