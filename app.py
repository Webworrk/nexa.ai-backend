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

# ✅ OpenAI API Key
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    raise ValueError("❌ OPENAI_API_KEY environment variable is missing!")

# ✅ Vapi.ai Configuration
VAPI_API_KEY = os.getenv("VAPI_API_KEY")

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Welcome to Nexa Backend! AI-powered networking assistant is live."}), 200

# ✅ Webhook for Processing Vapi.ai Calls
@app.route("/vapi-webhook", methods=["POST"])
def vapi_webhook():
    try:
        data = request.json
        user_phone = data.get("customer", {}).get("number")
        transcript = data.get("message", {}).get("artifact", {}).get("transcript", "")

        if not user_phone or not transcript:
            return jsonify({"error": "Missing phone number or transcript"}), 400

        # ✅ Step 1: Store Transcript in CallLogs
        call_log_entry = {
            "Phone": user_phone,
            "Call Summary": "Processing...",
            "Transcript": transcript,
            "Timestamp": datetime.utcnow().isoformat()
        }
        call_logs_collection.insert_one(call_log_entry)

        # ✅ Step 2: Process Transcript with OpenAI
        structured_data = process_transcript(transcript)

        # ✅ Step 3: Update Users Collection
        user = users_collection.find_one({"Phone": user_phone})
        if not user:
            user = {
                "Name": structured_data.get("Name", "Not Mentioned"),
                "Email": "Not Mentioned",
                "Phone": user_phone,
                "Profession": structured_data.get("Profession", "Not Mentioned"),
                "Bio": structured_data.get("Bio", "Not Mentioned"),
                "Signup Status": "Completed",
                "Nexa ID": f"NEXA{users_collection.count_documents({}) + 1:05d}",
                "Calls": []
            }
            users_collection.insert_one(user)

        # ✅ Add Call Details to User Profile
        user_call = {
            "Call Number": len(user.get("Calls", [])) + 1,
            "Networking Goal": structured_data.get("Networking Goal", "Not Mentioned"),
            "Meeting Type": structured_data.get("Meeting Type", "Not Mentioned"),
            "Proposed Meeting Date": structured_data.get("Proposed Meeting Date", None),
            "Proposed Meeting Time": structured_data.get("Proposed Meeting Time", None),
            "Meeting Requested to": structured_data.get("Meeting Requested to", None) if structured_data.get("Meeting Type") != "Speed Dating" else None,
            "Meeting Status": "Pending Confirmation",
            "Finalized Meeting Date": None,
            "Finalized Meeting Time": None,
            "Meeting Link": None,
            "Participants Notified": False,
            "Status": "Ongoing",
            "Call Summary": structured_data.get("Call Summary", "No summary available."),
            "Transcript": transcript
        }

        users_collection.update_one({"Phone": user_phone}, {"$push": {"Calls": user_call}})

        return jsonify({"message": "Call processed and stored successfully!"}), 200

    except Exception as e:
        return jsonify({"error": "Webhook processing failed", "details": str(e)}), 500


# ✅ Function to Extract Data from Transcript using OpenAI
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
