from flask import Flask, request, jsonify
import requests
from pymongo import MongoClient  # ✅ Correct import
from dotenv import load_dotenv
import os
from datetime import datetime
import openai
import json

# ✅ Initialize Flask app at the top
app = Flask(__name__)

# ✅ Load environment variables
load_dotenv()

# ✅ Connect to MongoDB
MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise ValueError("❌ MONGO_URI environment variable is missing!")

client = MongoClient(MONGO_URI)  # ✅ Fix pymongo import
db = client["Nexa"]
call_logs_collection = db["CallLogs"]
users_collection = db["Users"]

# ✅ OpenAI API Key
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    raise ValueError("❌ OPENAI_API_KEY environment variable is missing!")

# ✅ Vapi.ai Configuration
VAPI_API_KEY = os.getenv("VAPI_API_KEY", None)
VAPI_ASSISTANT_ID = os.getenv("VAPI_ASSISTANT_ID", None)

if not VAPI_API_KEY or not VAPI_ASSISTANT_ID:
    print("⚠️ WARNING: Missing Vapi.ai API Key or Assistant ID!")

# ✅ Define Routes
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
    try:
        data = request.json
        print("📥 Incoming Webhook Data:", data)

        # ✅ Store Call Logs in `CallLogs` Collection
        call_logs_collection.insert_one(data)

        # ✅ Extract User Information Using OpenAI
        transcript = data.get("message", {}).get("artifact", {}).get("transcript", "")

        if transcript:
            structured_data = extract_user_info_from_transcript(transcript)
            users_collection.update_one(
                {"Phone": structured_data["Phone"]},  # Match existing user by phone
                {"$set": structured_data},  # Update user info
                upsert=True  # Insert if doesn't exist
            )

        return jsonify({"message": "Data processed successfully"}), 200

    except Exception as e:
        print(f"❌ Error processing webhook: {e}")
        return jsonify({"error": "Webhook processing failed"}), 500


def extract_user_info_from_transcript(transcript):
    """
    Uses OpenAI API to extract structured user details from a call transcript.
    """
    prompt = f"""
    Extract structured information from this call transcript:

    {transcript}

    Return the data in JSON format, following this structure:
    {{
      "Name": "<User's Name or 'Not Mentioned'>",
      "Email": "<User's Email or 'Not Mentioned'>",
      "Phone": "<User's Phone Number or 'Not Mentioned'>",
      "Profession": "<User's Profession or 'Not Mentioned'>",
      "Bio": "<A brief summary of the user's experience>",
      "Signup Status": "Incomplete",
      "Nexa ID": null,
      "Latest Call": {{
        "Networking Goal": "<What the user wants to achieve>",
        "Meeting Type": "<Speed Dating | One-on-One | Not Mentioned>",
        "Proposed Meeting Date": "<Formatted Date or 'Not Yet Decided'>",
        "Proposed Meeting Time": "<Formatted Time or 'Not Yet Decided'>",
        "Meeting Requested to": "<Who they want to connect with>",
        "Meeting Status": "<Pending | Confirmed | Cancelled>",
        "Finalized Meeting Date": "<Date or 'Not Yet Agreed'>",
        "Finalized Meeting Time": "<Time or 'Not Yet Agreed'>",
        "Meeting Link": "Not Yet Created",
        "Status": "Ongoing",
        "Call Summary": "<Short Summary of the Call>"
      }}
    }}
    """

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": "Extract structured information from the following call transcript."},
                      {"role": "user", "content": prompt}],
            temperature=0.7
        )
        extracted_data = json.loads(response["choices"][0]["message"]["content"])
        return extracted_data
    except Exception as e:
        print(f"❌ OpenAI Extraction Error: {e}")
        return {
            "Name": "Not Mentioned",
            "Email": "Not Mentioned",
            "Phone": "Not Mentioned",
            "Profession": "Not Mentioned",
            "Bio": "Not Mentioned",
            "Signup Status": "Incomplete",
            "Nexa ID": None,
            "Latest Call": {
                "Networking Goal": "Not Mentioned",
                "Meeting Type": "Not Mentioned",
                "Proposed Meeting Date": "Not Yet Decided",
                "Proposed Meeting Time": "Not Yet Decided",
                "Meeting Requested to": "Not Mentioned",
                "Meeting Status": "Pending",
                "Finalized Meeting Date": "Not Yet Agreed",
                "Finalized Meeting Time": "Not Yet Agreed",
                "Meeting Link": "Not Yet Created",
                "Status": "Ongoing",
                "Call Summary": "No summary available."
            }
        }


if __name__ != "__main__":
    gunicorn_app = app  # ✅ Exposes Flask app to Gunicorn
