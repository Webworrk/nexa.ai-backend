from flask import Flask, request, jsonify
import requests
from pymongo import MongoClient
from dotenv import load_dotenv
import os
from datetime import datetime
import openai
import json

app = Flask(__name__)

# ✅ Load environment variables
load_dotenv()

# ✅ Connect to MongoDB
MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise ValueError("❌ MONGO_URI environment variable is missing!")

client = MongoClient(MONGO_URI)
db = client["Nexa"]
call_logs_collection = db["CallLogs"]
users_collection = db["Users"]

try:
    print("✅ MongoDB Connected: ", client.server_info())  # Debug connection
except Exception as e:
    print("❌ MongoDB Connection Failed:", e)

# ✅ OpenAI API Key
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    raise ValueError("❌ OPENAI_API_KEY environment variable is missing!")

# ✅ Vapi.ai Configuration
VAPI_API_KEY = os.getenv("VAPI_API_KEY")
VAPI_ASSISTANT_ID = os.getenv("VAPI_ASSISTANT_ID")

if not VAPI_API_KEY or not VAPI_ASSISTANT_ID:
    print("⚠️ WARNING: Missing Vapi.ai API Key or Assistant ID!")

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Welcome to Nexa Backend! Your AI-powered networking assistant is live."}), 200

# ✅ Start an Outbound Call via Vapi.ai
@app.route("/start-call", methods=["POST"])
def start_call():
    try:
        data = request.json
        customer_phone = data.get("customer", {}).get("phoneNumber")

        if not customer_phone:
            return jsonify({"error": "Valid customer phone number is required"}), 400
            
        twilio_phone_number = "+18454796197"  # Your Twilio number

        payload = {
            "name": "Networking Call with Nexa",
            "assistantId": VAPI_ASSISTANT_ID,
            "type": "outboundPhoneCall",
            "phoneNumber": {
                "twilioPhoneNumber": twilio_phone_number
            },
            "customer": {
                "number": customer_phone,
                "numberE164CheckEnabled": True,
                "name": "Nexa Customer"
            }
        }
        
        headers = {
            "Authorization": f"Bearer {VAPI_API_KEY}",
            "Content-Type": "application/json"
        }

        response = requests.post(
            "https://api.vapi.ai/call",
            json=payload,
            headers=headers,
            timeout=30
        )

        if response.status_code == 200:
            user = users_collection.find_one({"Phone": customer_phone})
            if user:
                call_log = {
                    "Call Number": len(user.get("Calls", [])) + 1,
                    "Status": "Initiated",
                    "Call ID": response.json().get("id", "Not Available"),
                    "Timestamp": datetime.now().isoformat()
                }
                users_collection.update_one({"Phone": customer_phone}, {"$push": {"Calls": call_log}})
        
        return jsonify({"message": "Call initiated successfully!", "response": response.json()}), response.status_code
            
    except Exception as e:
        return jsonify({"error": "An unexpected error occurred", "details": str(e)}), 500

# ✅ Handle Incoming Webhook Data from Vapi.ai
@app.route("/vapi-webhook", methods=["POST"])
def vapi_webhook():
    try:
        data = request.json
        user_phone = data.get("customer", {}).get("number")
        transcript = data.get("message", {}).get("artifact", {}).get("transcript", "Not Mentioned")

        if not user_phone:
            return jsonify({"error": "Phone number not provided"}), 400

        summary = extract_user_info_from_transcript(transcript)

        # ✅ Find or Create User in MongoDB
        user = users_collection.find_one({"Phone": user_phone})
        if not user:
            user = {
                "Name": summary.get("Name", "Not Mentioned"),
                "Email": summary.get("Email", "Not Mentioned"),
                "Phone": user_phone,
                "Profession": summary.get("Profession", "Not Mentioned"),
                "Bio": summary.get("Bio", "Not Mentioned"),
                "Signup Status": "Incomplete",
                "Nexa ID": f"NEXA{users_collection.count_documents({}) + 1:05d}",
                "Calls": []
            }
            users_collection.insert_one(user)

        # ✅ Prepare Call Log Entry
        user_call_log = {
            "Call Number": len(user.get("Calls", [])) + 1,
            "Networking Goal": summary.get("Networking Goal", "Not Mentioned"),
            "Meeting Type": summary.get("Meeting Type", "Not Mentioned"),
            "Proposed Meeting Date": summary.get("Proposed Meeting Date", "Not Mentioned"),
            "Proposed Meeting Time": summary.get("Proposed Meeting Time", "Not Mentioned"),
            "Meeting Requested to": {
                "Name": summary.get("Requested To Name", "Not Mentioned"),
                "Email": summary.get("Requested To Email", "Not Mentioned"),
                "Phone": summary.get("Requested To Phone", "Not Mentioned"),
                "Profession": summary.get("Requested To Profession", "Not Mentioned"),
                "Bio": summary.get("Requested To Bio", "Not Mentioned")
            },
            "Meeting Status": "Pending Confirmation",
            "Finalized Meeting Date": None,
            "Finalized Meeting Time": None,
            "Meeting Link": None,
            "Participants Notified": False,
            "Status": "Ongoing",
            "Call Summary": summary.get("Call Summary", "No summary available.")
        }

        # ✅ Store Call Log in Users Collection
        users_collection.update_one({"Phone": user_phone}, {"$push": {"Calls": user_call_log}})

        # ✅ Store Call Log in CallLogs Collection
        call_logs_collection.insert_one({
            "Phone": user_phone,
            "Call Summary": summary.get("Call Summary", "No summary available."),
            "Transcript": transcript,
            "Timestamp": datetime.now().isoformat()
        })

        return jsonify({"message": "Call logged successfully!", "call_number": user_call_log["Call Number"]}), 200

    except Exception as e:
        return jsonify({"error": "Webhook processing failed", "details": str(e)}), 500

# ✅ Extract Structured Data from Call Transcript using OpenAI
def extract_user_info_from_transcript(transcript):
    prompt = f"""
    Extract structured information from this call transcript:

    {transcript}

    Return the data in JSON format.
    """

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": "Extract structured networking details from the transcript."},
                      {"role": "user", "content": prompt}],
            temperature=0.5
        )
        response_text = response["choices"][0]["message"]["content"]

        try:
            extracted_data = json.loads(response_text)
        except json.JSONDecodeError:
            return {}

        return extracted_data

    except Exception as e:
        return {}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
