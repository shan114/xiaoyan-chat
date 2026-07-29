import json
from pydantic import BaseModel, Field
from typing import Dict, Any

MOCK_ORDERS = {
    "E12345": {
        "user_name": "张三",
        "product": "机械键盘",
        "amount": 359.00,
        "status": "已签收",
        "refundable_amount": 359.00,
        "user_phone_tail": "5678"
    },
    "E12346": {
        "user_name": "李四",
        "product": "蓝牙耳机",
        "amount": 199.00,
        "status": "运输中",
        "refundable_amount": 0,
        "user_phone_tail": "1234"
    }
}

REFUNDED_ORDERS = set()

class QueryOrderInput(BaseModel):
    order_id: str = Field(description="订单编号")
    user_phone: str = Field(description="用户手机后四位")

class RefundOrderInput(BaseModel):
    order_id: str = Field(description="订单编号")
    reason: str = Field(description="退款原因")

def query_order(order_id: str, user_phone: str) -> str:
    order = MOCK_ORDERS.get(order_id)
    if not order:
        return f"错误：订单 {order_id} 不存在。"
    if order["user_phone_tail"] != user_phone:
        return f"错误：手机号后四位不匹配。"
    return (
        f"订单{order_id}：{order['product']}，金额{order['amount']}元，"
        f"状态{order['status']}，可退款{order['refundable_amount']}元。"
    )

def refund_order(order_id: str, reason: str) -> str:
    order = MOCK_ORDERS.get(order_id)
    if not order:
        return f"错误：订单 {order_id} 不存在。"
    if order["status"] == "运输中":
        return f"错误：订单 {order_id} 运输中，不可退款。"
    if order_id in REFUNDED_ORDERS:
        return f"错误：订单 {order_id} 已退款。"
    REFUNDED_ORDERS.add(order_id)
    return f"退款成功：订单{order_id}已退款{order['refundable_amount']}元。"

TOOLS = {
    "query_order": {
        "func": query_order,
        "description": "根据订单号和手机后四位查询订单信息。当用户询问订单状态时使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "订单编号"},
                "user_phone": {"type": "string", "description": "用户手机后四位"}
            },
            "required": ["order_id", "user_phone"]
        }
    },
    "refund_order": {
        "func": refund_order,
        "description": "为用户办理退款。需要订单号和退款原因。",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "订单编号"},
                "reason": {"type": "string", "description": "退款原因"}
            },
            "required": ["order_id", "reason"]
        }
    }
}