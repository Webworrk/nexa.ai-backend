import requests
from flask import Flask, request, jsonify
from pymongo import MongoClient
from dotenv import load_dotenv
import os
from datetime import datetime

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)

MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client["Nexa"]
users_collection = db["Users"]
call_logs_collection = db["CallLogs"]  # New collection for calls

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
    try:
        # 1. Log incoming request data
        data = request.json
        print("Incoming request data:", data)

        # 2. Validate customer phone number
        customer_phone = None
        if "customer" in data and isinstance(data["customer"], dict):
            customer_phone = data["customer"].get("phoneNumber")
        
        if not customer_phone:
            return jsonify({"error": "Valid customer phone number is required"}), 400
            
        # 3. Get and validate VAPI credentials
        vapi_api_key = os.getenv("VAPI_API_KEY")
        vapi_assistant_id = os.getenv("VAPI_ASSISTANT_ID")
        twilio_phone_number = "+18454796197"  # Your Twilio phone number
        
        print("VAPI Assistant ID:", vapi_assistant_id)
        
        # 4. Prepare request - Updated payload structure according to Vapi docs
        payload = {
            "name": "Networking Call with Nexa",
            "assistantId": vapi_assistant_id,
            "type": "outboundPhoneCall",
            "phoneNumber": {  # Add the Twilio phone number configuration
                "twilioPhoneNumber": twilio_phone_number
            },
            "customer": {
                "number": customer_phone,
                "numberE164CheckEnabled": True,
                "name": "Nexa Customer"
            }
        }
        
        print("Sending payload to VAPI:", payload)
        
        headers = {
            "Authorization": f"Bearer {vapi_api_key}",
            "Content-Type": "application/json"
        }

        # 5. Make request
        response = requests.post(
            "https://api.vapi.ai/call",
            json=payload,
            headers=headers,
            timeout=30
        )
        
        print("VAPI Response Status:", response.status_code)
        print("VAPI Response:", response.text)

        # 6. Log call to MongoDB
        if response.status_code == 200:
            # Extract user's phone number
            user = users_collection.find_one({"Phone": customer_phone})
            if user:
                call_log = {
                    "Call Number": len(user.get("Calls", [])) + 1,
                    "Status": "Initiated",
                    "Call ID": response.json().get("id", "Not Available"),
                    "Timestamp": datetime.now().isoformat()
                }
                users_collection.update_one(
                    {"Phone": customer_phone}, 
                    {"$push": {"Calls": call_log}}
                )
        
        if response.status_code == 401:
            return jsonify({"error": "Authentication failed with VAPI"}), 500
        elif response.status_code == 400:
            return jsonify({"error": "Invalid request to VAPI", "details": response.text}), 500
        elif response.status_code != 200:
            return jsonify({"error": f"VAPI returned status {response.status_code}", "details": response.text}), 500
        
        return jsonify({
            "message": "Call initiated successfully!",
            "response": response.json()
        }), 200
            
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return jsonify({"error": "An unexpected error occurred", "details": str(e)}), 500

@app.route("/vapi-webhook", methods=["POST"])
def vapi_webhook():
    data = request.json
    print("📥 Incoming Webhook Data:", data)  # Debugging log

    call_id = data.get("call", {}).get("id", "Unknown Call ID")
    call_status = data.get("message", {}).get("status", "Unknown Status")

    # Extract transcript messages properly
    transcript_messages = data.get("message", {}).get("artifact", {}).get("messages", [])
    
    if not transcript_messages:
        transcript_text = "No transcript available."
    else:
        # Extract all messages from the list
        transcript_text = "\n".join([msg.get("message", "") for msg in transcript_messages])

    # ✅ Store Call Log in MongoDB
    call_log = {
        "Call ID": call_id,
        "Transcript": transcript_text,  # 🔥 Now stores only the extracted text
        "Status": call_status,
        "Timestamp": datetime.now().isoformat()
    }

    call_logs_collection.insert_one(call_log)

    print("✅ Stored Call Log in MongoDB:", call_log)  # Debugging print
    return jsonify({"message": "Call log saved successfully!", "call_id": call_id}), 200



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
