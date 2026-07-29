"""
MCP (Model Context Protocol) 桥接层
- 支持连接多个 MCP Server（stdio 协议）
- 自动发现并注册 MCP 工具
- 提供同步调用接口（适配 Streamlit 同步环境）
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from typing import Dict, List, Optional, Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPBridge:
    """MCP 桥：在后台线程维护 MCP 连接，提供同步工具调用接口"""

    def __init__(self):
        self._servers: Dict[str, dict] = {}        # server_name -> session info
        self._tools: Dict[str, dict] = {}           # tool_name -> (server_name, tool_info, wrapper)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ready = False

    def start(self, config_path: str = None):
        """启动 MCP 桥接，连接配置中所有 MCP Server"""
        if not config_path:
            config_path = os.path.join(os.path.dirname(__file__), "mcp_servers.json")
        if not os.path.exists(config_path):
            print("[MCP] 未找到 mcp_servers.json，跳过")
            return

        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        servers = config.get("mcpServers", {})
        if not servers:
            print("[MCP] 配置为空，跳过")
            return

        for name, server_cfg in servers.items():
            try:
                self._connect_server_sync(name, server_cfg)
            except Exception as e:
                print(f"[MCP] 连接 {name} 失败: {e}")

    def _connect_server_sync(self, name: str, server_cfg: dict):
        """同步连接一个 MCP Server，发现工具并缓存"""
        command = server_cfg.get("command", "")
        args = server_cfg.get("args", [])
        env_vars = server_cfg.get("env", {})

        # 跳过禁用的 server
        if server_cfg.get("disabled", False):
            print(f"[MCP] {name} 已禁用，跳过")
            return

        # 检查 command 是否存在
        if command == "npx" or command == "uvx":
            # 这些需要安装，检查一下
            check = subprocess.run([command, "--version"], capture_output=True, text=True)
            if check.returncode != 0:
                print(f"[MCP] {command} 不可用，跳过 {name}")
                return

        # 构建环境变量
        env = os.environ.copy()
        env.update(env_vars)

        print(f"[MCP] 正在连接 {name} ({command} {' '.join(args)})...")
        try:
            tools = asyncio.run(self._discover_tools(command, args, env))
        except Exception as e:
            print(f"[MCP] {name} 连接失败: {e}")
            return

        # 存储连接参数
        self._servers[name] = {
            "command": command,
            "args": args,
            "env": env,
            "tools": {t.name: {"description": t.description, "schema": t.inputSchema}
                      for t in tools}
        }
        print(f"[MCP] {name} 连接成功，发现 {len(tools)} 个工具: {[t.name for t in tools]}")

    async def _discover_tools(self, command: str, args: list, env: dict):
        """异步发现 MCP Server 的工具列表"""
        params = StdioServerParameters(command=command, args=args, env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return result.tools  # Ensure we don't modify during iteration

    def get_tool_schemas(self) -> List[dict]:
        """获取所有 MCP 工具的 OpenAI Function Calling Schema"""
        schemas = []
        for srv_name, srv in self._servers.items():
            for tool_name, tool_info in srv["tools"].items():
                full_name = f"mcp_{srv_name}_{tool_name}"
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": full_name,
                        "description": tool_info.get("description", ""),
                        "parameters": tool_info.get("schema", {"type": "object", "properties": {}})
                    }
                })
        return schemas

    def call_tool(self, full_name: str, arguments: dict) -> str:
        """同步调用 MCP 工具（full_name 格式: mcp_<server>_<tool>）"""
        # 解析 full_name: mcp_fetch_fetch -> server=fetch, tool=fetch
        parts = full_name.split("_", 2)  # ["mcp", "server_name", "tool_name"]
        if len(parts) < 3:
            return json.dumps({"error": f"无效的 MCP 工具名: {full_name}"})
        srv_name = parts[1]
        tool_name = parts[2]

        srv = self._servers.get(srv_name)
        if not srv:
            return json.dumps({"error": f"MCP Server {srv_name} 未连接"})
        if tool_name not in srv["tools"]:
            return json.dumps({"error": f"工具 {tool_name} 不存在于 {srv_name}"})

        try:
            return asyncio.run(self._call_once(srv_name, tool_name, arguments))
        except Exception as e:
            return json.dumps({"error": f"MCP 调用失败: {str(e)}"})

    async def _call_once(self, srv_name: str, tool_name: str, arguments: dict) -> str:
        """单次异步调用 MCP 工具（每次新建连接）"""
        srv = self._servers[srv_name]
        params = StdioServerParameters(
            command=srv["command"],
            args=srv["args"],
            env=srv.get("env", {})
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                if result.content:
                    # 合并所有 content 文本
                    texts = []
                    for c in result.content:
                        if hasattr(c, "text"):
                            texts.append(c.text)
                        elif hasattr(c, "data"):
                            texts.append(str(c.data))
                        else:
                            texts.append(str(c))
                    return "\n".join(texts)
                return str(result)

    def is_connected(self, server_name: str = None) -> bool:
        """检查 MCP Server 是否已连接"""
        if server_name:
            return server_name in self._servers
        return len(self._servers) > 0

    @property
    def connected_servers(self) -> List[str]:
        return list(self._servers.keys())

    @property
    def all_tools(self) -> Dict[str, str]:
        """返回 {工具全名: 描述}"""
        result = {}
        for srv_name, srv in self._servers.items():
            for tool_name, tool_info in srv["tools"].items():
                full_name = f"mcp_{srv_name}_{tool_name}"
                result[full_name] = tool_info.get("description", "")
        return result


# 全局单例（延迟创建，避免 import 时连接外部服务）
_mcp_instance = None

def get_mcp_bridge() -> MCPBridge:
    """获取 MCP 桥接单例（首次调用时才创建）"""
    global _mcp_instance
    if _mcp_instance is None:
        _mcp_instance = MCPBridge()
    return _mcp_instance
