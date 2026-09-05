import requests
import json

url = "https://llmgateway.crisil.local/api/bedrock/model/bedrock.us.anthropic.claude-opus-4-7/invoke"

payload = json.dumps({
  "anthropic_version": "bedrock-2023-05-31",
  "max_tokens": 100,
  "messages": [
    {
      "role": "user",
      "content": "identify yourself"
    }
  ]
})
headers = {
  'Content-Type': 'application/json',
  'Authorization': 'Bearer c4cc588b9e8f4877be6f42f11a83ded6'
}

response = requests.request("POST", url, headers=headers, data=payload, verify=False)

print(response.text)
 