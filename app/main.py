import httpx
import os

from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime, timedelta
import asyncio

app = FastAPI()

TIMEOUT_SECONDS = 30  # 10分钟


class CustomerMessage(BaseModel):
    convo_id: str
    customer_id: str
    content: str


class StaffReply(BaseModel):
    convo_id: str
    staff_id: str
    content: str


# 简单双内存存储：真正上线时再换成数据库
conversations = {}


@app.post("/customer_message")
async def customer_message(msg: CustomerMessage):
    now = datetime.utcnow()
    convo = conversations.get(msg.convo_id, {
        "last_customer_msg_time": None,
        "last_staff_reply_time": None,
        "timeout_handled": False,
        "customer_id": msg.customer_id,
    })
    convo["last_customer_msg_time"] = now
    convo["timeout_handled"] = False  # 新消息来了重新计时
    conversations[msg.convo_id] = convo
    convo["last_customer_msg_content"] = msg.content

    print(f"[客户消息] 会话 {msg.convo_id} 内容：{msg.content} 时间：{now}")
    return {"status": "ok"}


@app.post("/staff_reply")
async def staff_reply(msg: StaffReply):
    now = datetime.utcnow()
    convo = conversations.get(msg.convo_id, {
        "last_customer_msg_time": None,
        "last_staff_reply_time": None,
        "timeout_handled": False,
        "customer_id": "",
    })
    convo["last_staff_reply_time"] = now
    convo["timeout_handled"] = True  # 已回复，不再触发超时
    conversations[msg.convo_id] = convo

    print(f"[顾问回复] 会话 {msg.convo_id} 内容：{msg.content} 时间：{now}")
    return {"status": "ok"}


async def timeout_checker():
    """后台定时任务：每30秒检查一次是否有超时会话"""
    while True:
        now = datetime.utcnow()
        for convo_id, convo in list(conversations.items()):
            last_c = convo.get("last_customer_msg_time")
            last_s = convo.get("last_staff_reply_time")
            handled = convo.get("timeout_handled", False)

            if not last_c or handled:
                continue

            if last_s is None or last_s < last_c:
                if now - last_c > timedelta(seconds=TIMEOUT_SECONDS):
                    await handle_timeout(convo_id, convo)
                    convo["timeout_handled"] = True
                    conversations[convo_id] = convo

        await asyncio.sleep(30)

# ===== Dify 配置 =====
DIFY_BASE_URL = "https://api.dify.ai/v1"
DIFY_API_URL = f"{DIFY_BASE_URL}/chat-messages"

# 从环境变量中读取（来自 Codespaces Secrets）
DIFY_API_KEY = os.getenv("DIFY_API_KEY", "")

if not DIFY_API_KEY:
    raise RuntimeError("❌ 未检测到 DIFY_API_KEY，请在 Codespaces Secrets 中配置")

# 用于保存：你自己的 convo_id -> Dify 的 conversation_id
dify_conversation_map = {}


async def call_dify_llm(customer_id: str, convo_id: str, last_message: str) -> str:
    """
    调用 Dify Chat API，根据客户最后一句话生成智能回复
    """
    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "inputs": {
            "customer_id": customer_id
        },
        "query": last_message,
        "response_mode": "blocking",   # 同步等待模型回复
        "user": f"cust:{customer_id}", # 稳定的用户标识
    }

    # 如果之前已经有 Dify 的 conversation_id，就带回去续上下文
    if convo_id in dify_conversation_map:
        payload["conversation_id"] = dify_conversation_map[convo_id]

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            DIFY_API_URL,
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    # 保存 Dify 返回的 conversation_id，供下次继续对话
    dify_cid = data.get("conversation_id")
    if dify_cid:
        dify_conversation_map[convo_id] = dify_cid

    # 返回模型生成的文本（兼容不同返回格式）
    return (
        data.get("answer")
        or data.get("output_text")
        or "（大模型未返回内容）"
    )


async def handle_timeout(convo_id: str, convo: dict):
    customer_id = convo.get("customer_id", "unknown")
    last_msg = convo.get("last_customer_msg_content", "")

    # 调 Dify 让大模型生成一段真正的客服回复
    try:
        ai_reply = await call_dify_llm(customer_id, convo_id, last_msg)
    except Exception as e:
        ai_reply = f"（调用大模型失败，错误：{e}）"

    print("====== [超时触发] ======")
    print(f"会话ID: {convo_id}, 客户ID: {customer_id}")
    print(f"[机器人→客户]：{ai_reply}")
    print("[系统→管理员]：某个会话已超时10分钟未回复，请关注。")
    print("=======================")



@app.on_event("startup")
async def on_startup():
    asyncio.create_task(timeout_checker())
