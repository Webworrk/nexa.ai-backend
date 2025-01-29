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

# ✅ Fetch Old Call Logs & Store in MongoDB
@app.route("/sync-vapi-calllogs", methods=["GET"])
def sync_vapi_calllogs():
    try:
        headers = {
            "Authorization": f"Bearer {VAPI_API_KEY}",
            "Content-Type": "application/json"
        }

        # ✅ Fetch Call Logs from Vapi.ai
        response = requests.get("https://api.vapi.ai/calls", headers=headers, timeout=30)

        if response.status_code != 200:
            return jsonify({"error": "Failed to fetch call logs", "details": response.text}), response.status_code

        call_logs = response.json().get("calls", [])

        if not call_logs:
            return jsonify({"message": "No new call logs found!"}), 200

        # ✅ Store Each Call Log in MongoDB
        for log in call_logs:
            user_phone = log.get("customer", {}).get("number", "Unknown")
            transcript = log.get("messages", [{}])[-1].get("artifact", {}).get("transcript", "Not Available")

            # ✅ Skip duplicate logs
            if call_logs_collection.find_one({"Phone": user_phone, "Transcript": transcript}):
                continue

            call_entry = {
                "Phone": user_phone,
                "Call Summary": "Pending AI Processing...",
                "Transcript": transcript,
                "Timestamp": datetime.utcnow().isoformat()
            }
            call_logs_collection.insert_one(call_entry)

            # ✅ Process & Update User Data from Transcript
            process_transcript(user_phone, transcript)

        return jsonify({"message": f"✅ Synced {len(call_logs)} call logs successfully!"}), 200

    except Exception as e:
        return jsonify({"error": "Syncing call logs failed", "details": str(e)}), 500


# ✅ Webhook to Handle New Calls from Vapi.ai
@app.route("/vapi-webhook", methods=["POST"])
def vapi_webhook():
    try:
        data = request.json
        print("📥 Incoming Webhook Data:", json.dumps(data, indent=4))

        # ✅ Extract User Phone Number
        user_phone = data.get("customer", {}).get("number")
        if not user_phone:
            return jsonify({"error": "Phone number not provided"}), 400

        # ✅ Extract Call Transcript
        transcript = data.get("message", {}).get("artifact", {}).get("transcript", "Not Mentioned")

        # ✅ Step 1: Store Raw Transcript in CallLogs
        call_log_entry = {
            "Phone": user_phone,
            "Call Summary": "Processing...",
            "Transcript": transcript,
            "Timestamp": datetime.utcnow().isoformat()
        }
        call_logs_collection.insert_one(call_log_entry)

        # ✅ Step 2: Process Transcript & Update User Data
        process_transcript(user_phone, transcript)

        return jsonify({"message": "Call log stored and processed successfully!"}), 200

    except Exception as e:
        print(f"❌ Webhook Error: {str(e)}")
        return jsonify({"error": "Webhook processing failed", "details": str(e)}), 500


# ✅ Process Transcript Using OpenAI & Update User Collection
def process_transcript(user_phone, transcript):
    try:
        summary = extract_user_info_from_transcript(transcript)

        # ✅ Find or Create User in MongoDB
        user = users_collection.find_one({"Phone": user_phone})
        if not user:
            print(f"👤 Creating new user for phone: {user_phone}")
            user = {
                "Nexa ID": f"NEXA{users_collection.count_documents({}) + 1:05d}",
                "Name": summary.get("Name", "Not Mentioned"),
                "Email": summary.get("Email", "Not Mentioned"),
                "Phone": user_phone,
                "Profession": summary.get("Profession", "Not Mentioned"),
                "Bio": summary.get("Bio", "Not Mentioned"),
                "Signup Status": "Incomplete",
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
            "Meeting Status": "Pending Confirmation",
            "Finalized Meeting Date": None,
            "Finalized Meeting Time": None,
            "Meeting Link": None,
            "Participants Notified": False,
            "Status": "Ongoing",
            "Call Summary": summary.get("Call Summary", "No summary available.")
        }

        # ✅ Store Call Log in Users Collection
        users_collection.update_one(
            {"Phone": user_phone},
            {"$push": {"Calls": user_call_log}}
        )

        # ✅ Update CallLogs with Summary
        call_logs_collection.update_one(
            {"Phone": user_phone, "Transcript": transcript},
            {"$set": {"Call Summary": summary.get("Call Summary", "No summary available.")}}
        )

        print(f"✅ Call processed & User Updated: {user_phone}")

    except Exception as e:
        print(f"❌ Error Processing Transcript: {e}")


# ✅ Extract Information from Transcript Using OpenAI
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
        extracted_data = json.loads(response_text)
        return extracted_data

    except Exception as e:
        print(f"❌ OpenAI Extraction Error: {e}")
        return {}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
