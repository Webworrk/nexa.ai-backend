import requests
from flask import Flask, request, jsonify
from pymongo import MongoClient
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)

# MongoDB Configuration
# MongoDB Configuration
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client["Nexa"]
users_collection = db["Users"]

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Welcome to Nexa Backend! Your AI-powered networking assistant is live."}), 200

# Register a new user
@app.route("/register", methods=["POST"])
def register_user():
    data = request.json
    name = data.get("name", "Not Mentioned")
    email = data.get("email", "Not Mentioned")
    phone = data.get("phone", "Not Mentioned")

    # Check if user exists
    existing_user = users_collection.find_one({"Phone": phone})
    if existing_user:
        return jsonify({"error": "User already exists!", "nexa_id": existing_user["Nexa ID"]}), 409

    # Create user document
    user = {
        "Name": name,
        "Email": email,
        "Phone": phone,
        "Profession": "Not Mentioned",
        "Bio": "Not Mentioned",
        "Signup Status": "Completed",
        "Nexa ID": f"NEXA{users_collection.count_documents({}) + 1:05d}",
        "Calls": []
    }
    users_collection.insert_one(user)
    return jsonify({"message": "User registered successfully!", "nexa_id": user["Nexa ID"]}), 201

# Retrieve user profile
@app.route("/user/<phone>", methods=["GET"])
def get_user(phone):
    user = users_collection.find_one({"Phone": phone}, {"_id": 0})  # Exclude MongoDB's `_id` field
    if not user:
        return jsonify({"error": "User not found!"}), 404
    return jsonify({"message": "User details retrieved successfully!", "user": user}), 200

# Update user profile
@app.route("/user/<phone>/update", methods=["PUT"])
def update_user(phone):
    data = request.json
    user = users_collection.find_one({"Phone": phone})
    if not user:
        return jsonify({"error": "User not found!"}), 404

    # Update fields dynamically
    update_fields = {key: data.get(key, user.get(key, "Not Mentioned")) for key in ["Profession", "Bio"]}
    if "Skills" in data:
        update_fields["Skills"] = list(set(user.get("Skills", []) + data["Skills"]))

    users_collection.update_one({"Phone": phone}, {"$set": update_fields})
    return jsonify({"message": "User updated successfully!"}), 200

# Log call details
@app.route("/user/<phone>/log-call", methods=["POST"])
def log_call(phone):
    data = request.json
    user = users_collection.find_one({"Phone": phone})
    if not user:
        return jsonify({"error": "User not found!"}), 404

    call_log = {
        "Call Number": len(user.get("Calls", [])) + 1,
        "Networking Goal": data.get("Networking Goal", "Not Mentioned"),
        "Meeting Type": data.get("Meeting Type", "Not Mentioned"),
        "Proposed Meeting Date": data.get("Proposed Meeting Date", "Not Mentioned"),
        "Proposed Meeting Time": data.get("Proposed Meeting Time", "Not Mentioned"),
        "Meeting Requested to": data.get("Meeting Requested to", "Not Mentioned"),
        "Meeting Status": data.get("Meeting Status", "Not Mentioned"),
        "Finalized Meeting Date": data.get("Finalized Meeting Date", "Not Mentioned"),
        "Finalized Meeting Time": data.get("Finalized Meeting Time", "Not Mentioned"),
        "Meeting Link": data.get("Meeting Link", "Not Mentioned"),
        "Participants Notified": data.get("Participants Notified", False),
        "Status": data.get("Status", "Ongoing"),
        "Call Summary": data.get("Call Summary", "Not Mentioned")
    }

    users_collection.update_one({"Phone": phone}, {"$push": {"Calls": call_log}})
    return jsonify({"message": "Call logged successfully!", "call_number": call_log["Call Number"]}), 201


# ✅ Vapi.ai Configuration
VAPI_API_KEY = os.getenv("VAPI_API_KEY", "not_set")
VAPI_ASSISTANT_ID = os.getenv("VAPI_ASSISTANT_ID", "not_set")

# ✅ Check if API keys are loaded correctly
if VAPI_API_KEY == "not_set" or VAPI_ASSISTANT_ID == "not_set":
    raise ValueError("❌ Missing API Key or Assistant ID in environment variables!")

# ✅ Handle Incoming Calls from Vapi.ai
@app.route("/handle-call", methods=["POST"])
def handle_call():
    data = request.json
    user_phone = data.get("phone", "Not Mentioned")
    transcript = data.get("transcript", "")

    # Find user by phone number
    user = users_collection.find_one({"Phone": user_phone})
    if not user:
        return jsonify({"error": "User not found"}), 404

    # Store transcript as a logged call
    call_log = {
        "Call Number": len(user.get("Calls", [])) + 1,
        "Transcript": transcript,
        "Status": "Processed"
    }

    users_collection.update_one({"Phone": user_phone}, {"$push": {"Calls": call_log}})

    return jsonify({"message": "Call processed successfully!", "call_number": call_log["Call Number"]}), 200

@app.route("/start-call", methods=["POST"])
def start_call():
    data = request.json
    user_phone = data.get("phone")

    if not user_phone:
        return jsonify({"error": "Phone number is required!"}), 400

    payload = {
        "name": "Networking Call with Nexa",
        "assistantId": VAPI_ASSISTANT_ID,  # ✅ Assistant ID
        "phoneNumber": user_phone  # ✅ Added user phone number
    }

    headers = {
        "Authorization": f"Bearer {VAPI_API_KEY}",
        "Content-Type": "application/json"
    }

    response = requests.post("https://api.vapi.ai/call", json=payload, headers=headers)

    if response.status_code == 200:
        return jsonify({"message": "Call initiated successfully!", "response": response.json()}), 200
    else:
        return jsonify({"error": "Failed to initiate call", "details": response.text}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
