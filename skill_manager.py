import json
from typing import Dict, Callable, Any

class SkillManager:
    def __init__(self):
        self.tools = {}

    def register(self, name: str, description: str, parameters: dict, func: Callable, source="local"):
        self.tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "func": func,
            "source": source
        }

    def get_openai_tools_schema(self):
        schema = []
        for name, tool in self.tools.items():
            schema.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool["description"],
                    "parameters": tool["parameters"]
                }
            })
        return schema

    def get_tool_list(self) -> dict:
        """返回工具名和描述的简洁映射"""
        return {name: tool["description"] for name, tool in self.tools.items()}

    def call(self, name: str, arguments: dict) -> str:
        tool = self.tools.get(name)
        if not tool:
            return json.dumps({"error": f"工具 {name} 不存在"})
        try:
            result = tool["func"](**arguments)
            if isinstance(result, dict):
                return json.dumps(result, ensure_ascii=False)
            return str(result)
        except Exception as e:
            return json.dumps({"error": str(e)})

# 全局技能管理器实例
skill_manager = SkillManager()