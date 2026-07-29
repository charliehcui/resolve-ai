import os

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

load_dotenv(override=True)

ALIYUN_API_KEY = os.getenv("ALIYUN_API_KEY")
ALIYUN_BASE_URL = os.getenv("ALIYUN_BASE_URL")

# model = init_chat_model(
#     model="glm-4.5",
#     model_provider="openai",
#     api_key=ALIYUN_API_KEY,
#     base_url=ALIYUN_BASE_URL,
#     temperature=0.2,
#     max_tokens=15,
# )


# full_response = None

# for chunk in model.stream("1+1=?"):
#     print(chunk.content, end="", flush=True)
#     full_response = chunk if full_response is None else full_response + chunk

# print("\n\n--- 完整 Response ---")
# print(full_response.model_dump())

model = init_chat_model(
    model="qwen-plus",
    model_provider="openai",
    api_key=ALIYUN_API_KEY,
    base_url=ALIYUN_BASE_URL,
    temperature=0.2,
    max_tokens=150,
)

response = model.invoke("用一句话解释什么是 LangChain。")

print(response)
response.pretty_print()