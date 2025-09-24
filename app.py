import os
from flask import Flask, request, abort
from dotenv import load_dotenv

# v3 imports
from linebot.v3.webhook import WebhookParser
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage
)

load_dotenv()

app = Flask(__name__)

CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CHANNEL_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

configuration = Configuration(access_token=CHANNEL_TOKEN)
parser = WebhookParser(CHANNEL_SECRET)


@app.route("/webhook", methods=['POST'])
def webhook():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)

    try:
        events = parser.parse(body, signature)  # 解析 LINE 傳來的事件
    except Exception as e:
        print("parse error:", e)
        abort(400)

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        for event in events:
            if event.type == "message" and event.message.type == "text":
                user_msg = event.message.text
                reply_token = event.reply_token

                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[TextMessage(text=f"Hello 👋 你說：{user_msg}")]
                    )
                )
    return "OK"


if __name__ == "__main__":
    app.run(port=3000)
