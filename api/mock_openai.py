from flask import Flask, jsonify, request

app = Flask(__name__)


@app.route('/v1/models', methods=['GET'])
def models():
    return jsonify({
        "object": "list",
        "data": [
            {"id": "gpt-4.1", "object": "model", "created": 1686935002, "owned_by": "openai"},
            {"id": "gpt-4", "object": "model", "created": 1686935002, "owned_by": "openai"}
        ]
    })


@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    data = request.json
    stream = data.get('stream', False)
    if stream:
        # Handle streaming response
        def generate():
            import time
            chunk = {
                "id": "chatcmpl-123",
                "object": "chat.completion.chunk",
                "created": 1677652288,
                "model": "gpt-4.1",
                "choices": [{"delta": {"role": "assistant", "content": ""}, "index": 0, "finish_reason": None}]
            }
            yield f"data: {jsonify(chunk).get_data(as_text=True)}\n\n"
            
            content = "Mock response from uiuiapi"
            for char in content.split(' '):
                chunk = {
                    "id": "chatcmpl-123",
                    "object": "chat.completion.chunk",
                    "created": 1677652288,
                    "model": "gpt-4.1",
                    "choices": [{"delta": {"content": char + " "}, "index": 0, "finish_reason": None}]
                }
                yield f"data: {jsonify(chunk).get_data(as_text=True)}\n\n"
                time.sleep(0.1)
            
            chunk = {
                "id": "chatcmpl-123",
                "object": "chat.completion.chunk",
                "created": 1677652288,
                "model": "gpt-4.1",
                "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]
            }
            yield f"data: {jsonify(chunk).get_data(as_text=True)}\n\n"
            yield "data: [DONE]\n\n"

        return app.response_class(generate(), mimetype='text/event-stream')
    else:
        return jsonify({
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "created": 1677652288,
            "model": "gpt-4.1",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Mock response from uiuiapi"
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 9,
                "completion_tokens": 12,
                "total_tokens": 21
            }
        })


if __name__ == '__main__':
    app.run(port=9999)
