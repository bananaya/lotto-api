import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/proxy_fetch')
def proxy_fetch():
    url = request.args.get("url")
    try:
        res = requests.get(url, verify=False, timeout=10)
        return res.text
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run()
