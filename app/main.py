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


async def handle_timeout(convo_id: str, convo: dict):
    customer_id = convo.get("customer_id", "unknown")
    print("====== [超时触发] ======")
    print(f"会话ID: {convo_id}, 客户ID: {customer_id}")
    print("[机器人→客户]：你好呀，我是智能小助手，目前老师暂时不在线，你的消息已经记录啦～")
    print("[系统→管理员]：某个会话已超时10分钟未回复，请关注。")
    print("=======================")


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(timeout_checker())
