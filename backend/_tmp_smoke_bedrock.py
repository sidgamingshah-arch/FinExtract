from pydantic import BaseModel
from app.adapters.bedrock_gateway_llm import BedrockGatewayLlmProvider


class Reply(BaseModel):
    answer: str


provider = BedrockGatewayLlmProvider()
parsed, meta = provider.complete_structured(
    system="Reply with a one-sentence identification of yourself.",
    messages=[{"role": "user", "content": "identify yourself"}],
    response_schema=Reply,
    max_tokens=100,
)
print("ANSWER:", parsed.answer)
print("META:", meta)
