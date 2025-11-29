from flask import Flask, request, jsonify
import logging

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    logger.info("Received chat completion request")
    data = request.json
    logger.info(f"Request data: {data}")
    
    return jsonify({
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "created": 1677652288,
        "model": "gpt-4.1",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "This is a mock response from the dummy OpenAI server."
            },
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": 9,
            "completion_tokens": 12,
            "total_tokens": 21
        }
    })

@app.route('/v1/models', methods=['GET'])
def list_models():
    logger.info("Received list models request")
    return jsonify({
        "object": "list",
        "data": [{
            "id": "gpt-4.1",
            "object": "model",
            "created": 1677610602,
            "owned_by": "openai"
        }]
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9999)
