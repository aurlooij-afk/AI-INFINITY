AI Infinity TARGET-2050.2701
What changed
2701 is an additive upgrade over the deployed 2700 fabric.
Preserves the 2700 real-world execution fabric and its safety gates.
Preserves the existing 2600 mission path and 2700 command routes.
Replaces the technical-first command-center page with a human-centered web interface.
Adds a private connection prompt that appears only when a capability needs a connector.
Adds a free-first model router with multiple provider paths:
Hugging Face Inference Providers
Google Gemini API free tier where available
Ollama/local runtime
generic OpenAI-compatible fallback
deterministic built-in fallback when none are configured
Never returns provider secrets through status, UI, or model responses.
Keeps consequential actions behind approval, operator authorization, scoped authority, and the existing 2700 safety boundaries.
Deploy
Use these files together:
main.py
requirements.txt
Dockerfile
render.yaml
The Docker command is:
uvicorn main:app --host 0.0.0.0 --port 10000
Model connection
No paid provider is mandatory.
The application automatically detects these private environment variables:
Hugging Face
HF_TOKEN
Optional model:
AI_INFINITY_HF_MODEL
Google Gemini
GEMINI_API_KEY
Optional model:
AI_INFINITY_GEMINI_MODEL
Ollama
AI_INFINITY_OLLAMA_URL
Optional model:
AI_INFINITY_OLLAMA_MODEL
Generic OpenAI-compatible provider
AI_INFINITY_MODEL_BASE_URL
AI_INFINITY_MODEL_API_KEY
AI_INFINITY_MODEL_NAME
Do not put secrets into the public command box or source code.
Main interface
/infinity/2700/ui
The same upgraded interface is also available at:
/infinity/2701/ui
Verification
Open:
/infinity/2701/health
/infinity/2701/self-test
/infinity/2700/self-test
/infinity/2701/model/providers
Both self-tests must report passed: true.
Truthful limits
A hosted free web service cannot manufacture access to private accounts, computers, phones, browsers, or paid model capacity. Those capabilities become available only after the corresponding real connector is privately configured and authorized. The interface exposes that state without exposing credentials.
