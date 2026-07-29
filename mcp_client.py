from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import asyncio
from skill_manager import skill_manager

async def load_mcp_tools(server_script_path: str, server_args: list = None):
    server_params = StdioServerParameters(
        command=server_script_path,
        args=server_args or []
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            for tool in tools.tools:
                # 创建闭包，正确捕获 tool_name
                def create_tool_func(tool_name):
                    async def call_tool(**kwargs):
                        result = await session.call_tool(tool_name, kwargs)
                        return result.content[0].text
                    return call_tool

                skill_manager.register(
                    name=tool.name,
                    description=tool.description or "",
                    parameters=tool.inputSchema,
                    func=create_tool_func(tool.name),
                    source="mcp"
                )