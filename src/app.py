import os
import uuid
from fastapi import FastAPI
import lark_oapi as lark
from lark_oapi.api.cardkit.v1 import ContentCardElementRequest, ContentCardElementRequestBody, \
    ContentCardElementResponse, CreateCardRequest, CreateCardRequestBody, CreateCardResponse
from lark_oapi.api.im.v1 import *
import json

from mcp import StdioServerParameters, stdio_client, ClientSession

from openai import AzureOpenAI, OpenAI
from pydantic import BaseModel
from openai.types.chat import ChatCompletionToolParam
from dotenv import load_dotenv
import sqlite3

load_dotenv()

lark.APP_ID = os.getenv("FEISHU_APP_ID")
lark.APP_SECRET = os.getenv("FEISHU_APP_SECRET")
verification_token = os.getenv("FEISHU_VERIFICATION_TOKEN")
client = lark.Client.builder().app_id(lark.APP_ID).app_secret(lark.APP_SECRET).build()

chat_model = os.getenv("CHAT_MODEL") or "gpt-4o-mini"

db_client = sqlite3.connect("db.sqlite3")
db_cursor = db_client.cursor()
db_cursor.execute('''CREATE TABLE IF NOT EXISTS messages
                   (id INTEGER PRIMARY KEY,
                   message_id TEXT,
                   message_type TEXT,
                   content TEXT,
                   chat_type TEXT,
                   chat_id TEXT)''')

app = FastAPI()

server_params = StdioServerParameters(
    command="npx",
    args=["@playwright/mcp@latest", "--headless"],
)

# 配置Azure OpenAI客户端
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE") or "https://api.openai.com/v1"

AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION") or "2024-02-01"

if OPENAI_API_KEY:
    llm = OpenAI(
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_API_BASE,
    )
elif AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY:
    llm = AzureOpenAI(
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
        azure_endpoint=os.getenv("AZURE_OPENAI_API_ENDPOINT"),
    )
else:
    raise ValueError("Please set OPENAI_API_KEY or AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY")

card_template = {
    "schema": "2.0",
    "config": {
        "streaming_mode": True,
        "summary": {
            "content": "[思考中]"
        },
        "streaming_config": {
            "print_frequency_ms": {
                "default": 30,
                "android": 25,
                "ios": 40,
                "pc": 50
            },

            "print_step": {
                "default": 2,
                "android": 3,
                "ios": 4,
                "pc": 5
            },

            "print_strategy": "fast",
        }
    },
    "body": {
        "elements": [
            {
                "tag": "markdown",
                "content": "思考中",
                "element_id": "markdown_1"
            }
        ]
    }
}


class Message(BaseModel):
    message_id: str
    message_type: str
    content: str
    chat_type: str
    chat_id: str


class Event(BaseModel):
    message: Message


class P2ImMessageReceiveV1Req(BaseModel):
    event: Event = None
    challenge: str = None
    token: str = None
    type: str = None


def send_msg(data, message, is_p2p=True):
    # 创建卡片 https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/cardkit-v1/card/create
    card_create_request: CreateCardRequest = CreateCardRequest.builder() \
        .request_body(CreateCardRequestBody.builder()
                      .type("card_json")
                      .data(json.dumps(card_template)
                            ).build()).build()

    # 发起请求
    card_create_response: CreateCardResponse = client.cardkit.v1.card.create(card_create_request)

    if is_p2p:
        send_card_request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(data.event.message.chat_id)
                .msg_type("interactive")
                .content("{\"type\":\"card\",\"data\":{\"card_id\":\"" + card_create_response.data.card_id + "\"}}")
                .build()
            ).build()
        )
        send_card_response = client.im.v1.chat.create(send_card_request)

    else:
        send_card_request: ReplyMessageRequest = (
            ReplyMessageRequest.builder()
            .message_id(data.event.message.message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("interactive")
                .content("{\"type\":\"card\",\"data\":{\"card_id\":\"" + card_create_response.data.card_id + "\"}}")
                .build()
            )
            .build()
        )
        # https://open.larkoffice.com/document/server-docs/im-v1/message/reply
        send_card_response: ReplyMessageResponse = client.im.v1.message.reply(send_card_request)

    request: ContentCardElementRequest = ContentCardElementRequest.builder() \
        .card_id(card_create_response.data.card_id) \
        .element_id("markdown_1") \
        .request_body(ContentCardElementRequestBody.builder()
                      .uuid(str(uuid.uuid4()))
                      .content(message)
                      .sequence(1)
                      .build()) \
        .build()
    # 使用发送OpenAPI发送消息
    # Use send OpenAPI to send messages
    response: ContentCardElementResponse = client.cardkit.v1.card_element.content(request)
    if not response.success():
        raise Exception(
            f"client.im.v1.chat.create failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}"
        )


@app.post("/im/v1/p2_im_message_receive_v1")
async def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1Req) -> (None or dict):
    event_type = data.type
    if "url_verification" == event_type:
        return {"challenge": data.challenge}
    message_id = data.event.message.message_id

    history = db_client.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
    if history:
        print("message_id: " + message_id, "已处理")
        return {"code": -1, "msg": "message_id: " + message_id + "已处理"}
    db_client.execute(
        "INSERT INTO messages (message_id, message_type, content, chat_type, chat_id) VALUES (?, ?, ?, ?, ?)",
        (message_id, data.event.message.message_type, data.event.message.content, data.event.message.chat_type,
         data.event.message.chat_id))
    db_client.commit()
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if data.event.message.message_type == "text":
                res_content = json.loads(data.event.message.content)["text"]
            else:
                return {"code": -1,
                        "msg": "解析消息失败，请发送文本消息\nparse message failed, please send text message"}
            tools_list = await session.list_tools()
            tools_list = [ChatCompletionToolParam(**{
                "type": "function",
                "function": {
                    "name": i.name,
                    "description": i.description,
                    "parameters": i.inputSchema,
                }
            }) for i in tools_list.tools]
            messages = [
                {
                    "role": "user",
                    "content": res_content
                }
            ]
            is_break = False
            is_p2p = data.event.message.chat_type == "p2p"
            max_count = 10
            while max_count:
                resp = llm.chat.completions.create(
                    model=chat_model,
                    messages=messages,
                    stream=False,
                    tools=tools_list,
                    tool_choice="auto"
                )
                for chunk in resp.choices:
                    message = chunk.message
                    finish_reason = chunk.finish_reason
                    if finish_reason == "tool_calls":
                        for tool in message.tool_calls:
                            function = tool.function
                            arguments = json.loads(function.arguments)
                            print(f"function: {function.name}, arguments: {arguments}")
                            try:
                                send_msg(data, f"我需要执行工具：{function.name}，参数为：{function.arguments}", is_p2p)
                                # 使用全局事件循环同步调用异步函数
                                tool_res = await session.call_tool(function.name, arguments)

                                messages.append(
                                    {
                                        "role": "assistant",
                                        "content": None,
                                        "tool_calls": [
                                            {
                                                "id": tool.id,
                                                "function": {
                                                    "name": function.name,
                                                    "arguments": function.arguments
                                                },
                                                "type": "function"
                                            }
                                        ]
                                    })
                                messages.append({
                                    "role": "tool",
                                    "content": tool_res.content[0].text,
                                    "tool_call_id": tool.id
                                })
                                send_msg(data, f"工具执行结果：\n{tool_res.content[0].text[:1000]}", is_p2p)
                            except Exception as e:
                                print(f"Error executing tool: {e}")
                    if finish_reason == "stop":
                        send_msg(data, chunk.message.content, is_p2p)
                        messages.append({
                            "role": "assistant",
                            "content": chunk.message.content
                        })
                        is_break = True
                        break
                if is_break:
                    break
                print("max_count: ", max_count, "is_break: ", is_break)
                max_count -= 1
        return {
            "code": 0,
            "msg": "success",
            "data": messages
        }


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8090)
