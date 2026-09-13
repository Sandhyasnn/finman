print("STEP 1: script started")

import os
print("STEP 2: os imported")

from mistralai.client import Mistral
print("STEP 3: mistralai imported")

api_key = os.environ.get("MISTRAL_API_KEY")
print("STEP 4: API key present:", bool(api_key))

if not api_key:
    print("ERROR: MISTRAL_API_KEY is not set")
    raise SystemExit(1)

print("STEP 5: creating Mistral client")

client = Mistral(api_key=api_key)

print("STEP 6: sending request")

response = client.chat.complete(
    model="mistral-small-latest",
    messages=[
        {
            "role": "user",
            "content": "Say hello in one word."
        }
    ],
    max_tokens=5,
)

print("STEP 7: response received")
print("Response:", response.choices[0].message.content)
print("DONE")
