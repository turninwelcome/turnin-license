from flask import Flask, request, jsonify, render_template_string, render_template, redirect
from flask_cors import CORS
import json
import os
import secrets
import string
import requests
from datetime import datetime
from functools import wraps

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'))
CORS(app) # Allow landing page to talk to the server

# --- ROUTES ---

@app.route("/")
def index():
    return render_template("index.html")

# --- CONFIG ---
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))
MAIL_PROXY_URL = os.environ.get("MAIL_PROXY_URL")
APP_DOWNLOAD_URL = os.environ.get("APP_DOWNLOAD_URL", "https://turnin.app")
LATEST_APP_VERSION = os.environ.get("LATEST_APP_VERSION", "0.1.0").strip()

# --- DATA STORAGE ---
# Use absolute path for the data file so it doesn't get lost in Render subdirectories
DATA_FILE = os.path.join(BASE_DIR, "data.json")

def load_data():
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                content = f.read().strip()
                if not content:
                    return {"keys": {}, "orders": {}}
                return json.loads(content)
    except Exception as e:
        print(f"Error loading data: {e}")
    return {"keys": {}, "orders": {}}

def save_data(data):
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Error saving data: {e}")

# --- HELPERS ---
def generate_key():
    return "TURNIN-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))

def generate_order_id():
    return "ORD-" + "".join(secrets.choice(string.digits) for _ in range(4))

def check_auth(username, password):
    return username == "admin" and password == ADMIN_PASSWORD

def authenticate():
    return ("<h1>401 Unauthorized</h1>", 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

def send_email(to_email, key, features):
    if not MAIL_PROXY_URL:
        print("CRITICAL: MAIL_PROXY_URL missing.")
        return False
    
    feature_list = [f for f, val in features.items() if val]
    feature_text = ", ".join(feature_list)
    
    # We build the HTML for the email
    html_content = f"""
    <div style="font-family: sans-serif; padding: 20px; border: 1px solid #e8d5a3; border-radius: 10px;">
        <h1 style="color: #8b6914;">Welcome to Turnin!</h1>
        <p>Your license key has been activated.</p>
        <div style="background: #f5f2ec; padding: 15px; border-radius: 8px; font-family: monospace; font-size: 20px; font-weight: bold; text-align: center; color: #1a1714; margin: 20px 0;">
            {key}
        </div>
        <div style="text-align: center; margin: 20px 0 24px;">
            <a href="{APP_DOWNLOAD_URL}" style="display: inline-block; background: #8b6914; color: #ffffff; text-decoration: none; padding: 12px 22px; border-radius: 8px; font-weight: 700;">
                Download the App
            </a>
        </div>
        <p><strong>Enabled Features:</strong> {feature_text}</p>
        <hr style="border: 0; border-top: 1px solid #eee; margin: 20px 0;">
        <p style="font-size: 12px; color: #6b6560;">This is a professional desktop application. Please keep your key secure and do not share it.</p>
    </div>
    """
    
    payload = {
        "to": to_email,
        "subject": "Your Turnin License Key",
        "body": html_content
    }
    
    try:
        # We send a standard POST request to the Google Script
        # This bypasses port restrictions because it uses standard HTTPS (443)
        resp = requests.post(MAIL_PROXY_URL, json=payload, timeout=15)
        if resp.text == "OK":
            print(f"SUCCESS: Email sent to {to_email} via Google Proxy")
            return True
        else:
            print(f"PROXY ERROR: Script returned: {resp.text}")
            return False
    except Exception as e:
        print(f"CONNECTION ERROR during proxy send: {e}")
        return False

# --- ROUTES ---

@app.route("/api/order", methods=["POST"])
def create_order():
    data = request.get_json()
    email = data.get("email")
    features = data.get("features", {}) # e.g. {"base": True, "humanizer": True}
    total_price = data.get("total_price", 0)
    
    # Debug print for Render Free Tier logs
    print(f"--- NEW ORDER ---")
    print(f"Email: {email}")
    print(f"Features: {json.dumps(features)}")
    print(f"Total: ${total_price}")
    print(f"-----------------")
    
    if not email:
        return jsonify({"ok": False, "error": "Email required"}), 400
    
    order_id = generate_order_id()
    store = load_data()
    store["orders"][order_id] = {
        "email": email,
        "features": features,
        "total_price": total_price,
        "status": "pending",
        "timestamp": datetime.now().isoformat()
    }
    save_data(store)
    
    return jsonify({"ok": True, "order_id": order_id})

@app.route("/api/validate", methods=["POST"])
def validate_key():
    data = request.get_json()
    key = data.get("key", "").strip().upper()
    google_id = data.get("google_id", "").strip()
    google_email = data.get("google_email", "").strip()
    
    store = load_data()
    if key not in store["keys"]:
        return jsonify({"valid": False, "reason": "Invalid key"})
    
    key_info = store["keys"][key]
    
    # Lock to Google ID if not already locked
    if not key_info.get("google_id"):
        key_info["google_id"] = google_id
        key_info["google_email"] = google_email
        save_data(store)
    elif key_info["google_id"] != google_id:
        return jsonify({"valid": False, "reason": "Key locked to another Google account"})
    
    return jsonify({
        "valid": True, 
        "features": key_info.get("features", {"base": True})
    })

@app.route("/api/latest-release")
def latest_release():
    return jsonify({
        "ok": True,
        "latest_version": LATEST_APP_VERSION,
        "download_url": APP_DOWNLOAD_URL,
    })

# --- ADMIN ROUTES ---

@app.route("/admin")
@requires_auth
def admin_dashboard():
    msg = request.args.get("msg")
    store = load_data()
    pending_orders = {k: v for k, v in store["orders"].items() if v["status"] == "pending"}
    active_keys = store["keys"]
    
    html = """
    <html>
    <head><title>Turnin Admin</title>
    <style>
        body { font-family: sans-serif; margin: 40px; background: #f4f7f6; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; background: white; }
        th, td { padding: 12px; border: 1px solid #ddd; text-align: left; }
        th { background: #eee; }
        .btn { padding: 8px 16px; cursor: pointer; border: none; border-radius: 4px; color: white; text-decoration: none; font-size: 13px; }
        .btn-approve { background: #28a745; }
        .btn-revoke { background: #dc3545; }
        .alert { padding: 15px; margin-bottom: 20px; border: 1px solid transparent; border-radius: 4px; }
        .alert-success { color: #155724; background-color: #d4edda; border-color: #c3e6cb; }
    </style>
    </head>
    <body>
        <h1>Turnin Admin Dashboard</h1>
        
        {% if msg %}
        <div class="alert alert-success">Action Completed: {{ msg }}</div>
        {% endif %}
        
        <h2>Pending Orders</h2>
        <table>
            <tr>
                <th>Order ID</th>
                <th>Email</th>
                <th>Total</th>
                <th>Features</th>
                <th>Action</th>
            </tr>
            {% for id, order in pending.items() %}
            <tr>
                <td>{{ id }}</td>
                <td>{{ order.email }}</td>
                <td>${{ order.total_price }}</td>
                <td>
                    {% for f, val in order.features.items() %}
                        {% if val %} {{ f }} {% endif %}
                    {% endfor %}
                </td>
                <td>
                    <form action="/admin/approve/{{ id }}" method="POST" style="display:inline;">
                        <button class="btn btn-approve">Approve & Send Key</button>
                    </form>
                </td>
            </tr>
            {% endfor %}
        </table>

        <h2>Active Keys</h2>
        <table>
            <tr>
                <th>Key</th>
                <th>Email</th>
                <th>Features</th>
                <th>Google ID</th>
                <th>Action</th>
            </tr>
            {% for key, info in keys.items() %}
            <tr>
                <td>{{ key }}</td>
                <td>{{ info.email }}</td>
                <td>
                    {% for f, val in info.features.items() %}
                        {% if val %} {{ f }} {% endif %}
                    {% endfor %}
                </td>
                <td>{{ info.google_id or 'Not linked' }}</td>
                <td>
                    <form action="/admin/revoke/{{ key }}" method="POST" style="display:inline;" onsubmit="return confirm('Really revoke this key?')">
                        <button class="btn btn-revoke">Revoke</button>
                    </form>
                </td>
            </tr>
            {% endfor %}
        </table>
    </body>
    </html>
    """
    return render_template_string(html, pending=pending_orders, keys=active_keys, msg=msg)

@app.route("/admin/approve/<order_id>", methods=["POST"])
@requires_auth
def approve_order(order_id):
    store = load_data()
    if order_id not in store["orders"]:
        return "Order not found", 404
    
    order = store["orders"][order_id]
    if order["status"] == "approved":
        return "Already approved", 400
    
    # 1. Generate Key
    key = generate_key()
    
    # 2. Save Key
    store["keys"][key] = {
        "email": order["email"],
        "features": order["features"],
        "google_id": None,
        "created_at": datetime.now().isoformat()
    }
    
    # 3. Mark order as approved
    order["status"] = "approved"
    order["assigned_key"] = key
    save_data(store)
    
    # 4. Send Email
    success = send_email(order["email"], key, order["features"])
    
    if success:
        return redirect("/admin?msg=Approved+and+Email+Sent")
    else:
        return redirect("/admin?msg=Approved+BUT+Email+Failed+Check+Logs")

@app.route("/admin/revoke/<key>", methods=["POST"])
@requires_auth
def revoke_key(key):
    store = load_data()
    if key in store["keys"]:
        del store["keys"][key]
        save_data(store)
    return redirect("/admin?msg=Revoked")

if __name__ == "__main__":
    # Startup Check
    print("--- SERVER STARTUP ---")
    print(f"MAIL_PROXY_URL: {'Configured' if MAIL_PROXY_URL else 'MISSING'}")
    print(f"DATA_FILE: {DATA_FILE}")
    print("----------------------")
    
    port = int(os.environ.get("PORT", 5051))
    app.run(host="0.0.0.0", port=port)
