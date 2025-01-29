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

@app.route("/vapi-webhook", methods=["POST"])
def vapi_webhook():
    try:
        data = request.json
        print("📥 Incoming Webhook Data:", json.dumps(data, indent=4))

        # ✅ Extract user phone number
        user_phone = data.get("customer", {}).get("number")
        if not user_phone:
            return jsonify({"error": "Phone number not provided"}), 400

        # ✅ Extract Transcript
        transcript = data.get("message", {}).get("artifact", {}).get("transcript", "Not Mentioned")
        timestamp = datetime.utcnow().isoformat()

        # ✅ Step 1: Store Raw Call Transcript in CallLogs
        call_logs_collection.insert_one({
            "Phone": user_phone,
            "Call Summary": "Processing...",  # Placeholder until OpenAI extracts info
            "Transcript": transcript,
            "Timestamp": timestamp
        })

        print(f"✅ Transcript stored in CallLogs for {user_phone}")

        # ✅ Step 2: Extract Structured Data Using OpenAI
        structured_data = extract_user_info_from_transcript(transcript)
        if not structured_data:
            return jsonify({"error": "Failed to extract data from transcript"}), 500

        # ✅ Step 3: Update Users Collection
        user = users_collection.find_one({"Phone": user_phone})
        if not user:
            user = {
                "Name": structured_data.get("Name", "Not Mentioned"),
                "Email": structured_data.get("Email", "Not Mentioned"),
                "Phone": user_phone,
                "Profession": structured_data.get("Profession", "Not Mentioned"),
                "Bio": structured_data.get("Bio", "Not Mentioned"),
                "Signup Status": "Incomplete",
                "Nexa ID": f"NEXA{users_collection.count_documents({}) + 1:05d}",
                "Calls": []
            }
            users_collection.insert_one(user)

        # ✅ Prepare Call Entry for Users Collection
        call_entry = {
            "Call Number": len(user.get("Calls", [])) + 1,
            "Networking Goal": structured_data.get("Networking Goal", "Not Mentioned"),
            "Meeting Type": structured_data.get("Meeting Type", "Not Mentioned"),
            "Proposed Meeting Date": structured_data.get("Proposed Meeting Date", "Not Mentioned"),
            "Proposed Meeting Time": structured_data.get("Proposed Meeting Time", "Not Mentioned"),
            "Meeting Requested to": {
                "Name": structured_data.get("Requested To Name", "Not Mentioned"),
                "Email": structured_data.get("Requested To Email", "Not Mentioned"),
                "Phone": structured_data.get("Requested To Phone", "Not Mentioned"),
                "Profession": structured_data.get("Requested To Profession", "Not Mentioned"),
                "Bio": structured_data.get("Requested To Bio", "Not Mentioned")
            },
            "Meeting Status": "Pending Confirmation",
            "Finalized Meeting Date": None,
            "Finalized Meeting Time": None,
            "Meeting Link": None,
            "Participants Notified": False,
            "Status": "Ongoing",
            "Call Summary": structured_data.get("Call Summary", "No summary available."),
            "Transcript": transcript
        }

        users_collection.update_one({"Phone": user_phone}, {"$push": {"Calls": call_entry}})
        call_logs_collection.update_one({"Phone": user_phone, "Transcript": transcript}, {"$set": {"Call Summary": structured_data.get("Call Summary", "No summary available.")}})

        print(f"✅ Call Log updated for {user_phone}")
        return jsonify({"message": "Call logged successfully!", "call_number": call_entry["Call Number"]}), 200

    except Exception as e:
        print(f"❌ ERROR Processing Webhook: {str(e)}")
        return jsonify({"error": "Webhook processing failed", "details": str(e)}), 500

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
            print(f"❌ JSON Decode Error: {response_text}")
            return {}

        return extracted_data

    except Exception as e:
        print(f"❌ OpenAI Extraction Error: {e}")
        return {}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
