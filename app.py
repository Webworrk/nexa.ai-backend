from flask import Flask, request, jsonify
import requests
from pymongo import MongoClient
from dotenv import load_dotenv
import os
from datetime import datetime
from openai import OpenAI
import json
import hashlib
import traceback

app = Flask(__name__)

# Load environment variables
load_dotenv()

# Connect to MongoDB
MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise ValueError("❌ MONGO_URI environment variable is missing!")

mongo_client = MongoClient(MONGO_URI)
db = mongo_client["Nexa"]
call_logs_collection = db["CallLogs"]
users_collection = db["Users"]

try:
    print("✅ MongoDB Connected: ", mongo_client.server_info())
except Exception as e:
    print("❌ MongoDB Connection Failed:", e)

# Initialize OpenAI client
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
if not os.getenv("OPENAI_API_KEY"):
    raise ValueError("❌ OPENAI_API_KEY environment variable is missing!")

# Vapi.ai Configuration
VAPI_API_KEY = os.getenv("VAPI_API_KEY")
VAPI_ASSISTANT_ID = os.getenv("VAPI_ASSISTANT_ID")

if not VAPI_API_KEY or not VAPI_ASSISTANT_ID:
    print("⚠️ WARNING: Missing Vapi.ai API Key or Assistant ID!")

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Welcome to Nexa Backend! Your AI-powered networking assistant is live."}), 200

def hash_transcript(transcript):
    """Generate a unique hash for the transcript to prevent duplicates."""
    return hashlib.sha256(transcript.encode()).hexdigest()

def extract_user_info_from_transcript(transcript):
    """Extract user information from transcript using OpenAI."""
    default_response = {
        "Name": "Not Mentioned",
        "Email": "Not Mentioned",
        "Profession": "Not Mentioned",
        "Bio": "Not Mentioned",
        "Networking Goal": "Not Mentioned",
        "Meeting Type": "Not Mentioned",
        "Proposed Meeting Date": "Not Mentioned",
        "Proposed Meeting Time": "Not Mentioned",
        "Call Summary": "Not Mentioned"
    }
    
    try:
        # Better system prompt with examples
        system_prompt = """You are an AI assistant that extracts structured information from call transcripts.
        Extract information in the following format, with specific focus on details actually mentioned in the transcript:

        Example Output:
        {
            "Name": "John Smith",
            "Email": "john@example.com",
            "Profession": "CEO of TechCorp",
            "Bio": "Founder of AI startup with 10 years experience",
            "Networking Goal": "Seeking Series A funding of $5M",
            "Meeting Type": "Virtual",
            "Proposed Meeting Date": "March 15th",
            "Proposed Meeting Time": "2 PM EST",
            "Call Summary": "Brief overview of key points discussed"
        }

        If information is not mentioned in the transcript, use "Not Mentioned". Be specific and extract exact dates, times, and goals mentioned."""

        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": f"Extract information from this transcript, focusing on specific details mentioned: {transcript}"
                }
            ],
            response_format={"type": "json_object"},
            temperature=0.1  # Lower temperature for more consistent outputs
        )
        
        print("OpenAI Response:", response.choices[0].message.content)  # Debug log
        
        try:
            extracted_info = json.loads(response.choices[0].message.content)
            
            # Validate and clean the extracted information
            cleaned_info = {}
            for key in default_response.keys():
                value = extracted_info.get(key, "Not Mentioned").strip()
                cleaned_info[key] = value if value and value != "None" else "Not Mentioned"
            
            print("Cleaned Info:", cleaned_info)  # Debug log
            return cleaned_info
            
        except json.JSONDecodeError as e:
            print(f"JSON Decode Error: {str(e)}")
            print("Raw Response:", response.choices[0].message.content)
            return default_response
            
    except Exception as e:
        print(f"OpenAI API Error: {str(e)}")
        return default_response

def process_transcript(user_phone, transcript):
    """Process transcript and update both Users and CallLogs collections."""
    try:
        print(f"Processing transcript for phone: {user_phone}")
        summary = extract_user_info_from_transcript(transcript)
        print(f"Extracted information: {json.dumps(summary, indent=2)}")

        # Find or create user
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
        elif summary.get("Name") != "Not Mentioned" and user.get("Name") == "Not Mentioned":
            # Update user info if we got new information
            users_collection.update_one(
                {"Phone": user_phone},
                {"$set": {
                    "Name": summary.get("Name"),
                    "Profession": summary.get("Profession"),
                    "Bio": summary.get("Bio")
                }}
            )

        # Prepare call log entry with rich information
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

        # Update Users collection with call log
        users_collection.update_one(
            {"Phone": user_phone},
            {"$push": {"Calls": user_call_log}}
        )

        # Update CallLogs with summary
        call_logs_collection.update_one(
            {"Phone": user_phone, "Transcript Hash": hash_transcript(transcript)},
            {"$set": {
                "Call Summary": summary.get("Call Summary", "No summary available."),
                "Processed": True,
                "Last Updated": datetime.utcnow().isoformat()
            }}
        )

        print(f"✅ Call processed & User Updated: {user_phone}")
        print(f"📝 Call Summary: {summary.get('Call Summary')}")

    except Exception as e:
        print(f"❌ Error Processing Transcript: {str(e)}")
        print(f"Stack trace: {traceback.format_exc()}")

@app.route("/sync-vapi-calllogs", methods=["GET"])
def sync_vapi_calllogs():
    try:
        headers = {
            "Authorization": f"Bearer {VAPI_API_KEY}",
            "Content-Type": "application/json"
        }

        response = requests.get("https://api.vapi.ai/call", headers=headers, timeout=30)

        if response.status_code != 200:
            return jsonify({"error": "Failed to fetch call logs", "details": response.text}), response.status_code

        call_logs = response.json()

        if not call_logs:
            return jsonify({"message": "No new call logs found!"}), 200

        for log in call_logs:
            user_phone = log.get("customer", {}).get("number", "Unknown")
            transcript = log.get("messages", [{}])[-1].get("artifact", {}).get("transcript", "Not Available")
            transcript_hash = hash_transcript(transcript)
            timestamp = datetime.utcnow().isoformat()

            existing_log = call_logs_collection.find_one({
                "Phone": user_phone,
                "Transcript Hash": transcript_hash
            })

            if existing_log:
                print(f"⚠️ Skipping duplicate log for {user_phone}")
                continue

            call_entry = {
                "Phone": user_phone,
                "Call Summary": "Pending AI Processing...",
                "Transcript": transcript,
                "Transcript Hash": transcript_hash,
                "Timestamp": timestamp,
                "Processed": False
            }
            call_logs_collection.insert_one(call_entry)

            process_transcript(user_phone, transcript)

        return jsonify({"message": f"✅ Synced {len(call_logs)} call logs successfully!"}), 200

    except Exception as e:
        return jsonify({"error": "Syncing call logs failed", "details": str(e)}), 500

@app.route("/vapi-webhook", methods=["POST"])
def vapi_webhook():
    try:
        data = request.get_json()
        if not data:
            print("❌ No JSON received!")
            return jsonify({"error": "No JSON received"}), 400
        
        print("📥 Incoming Webhook Data:", json.dumps(data, indent=4))
        
        user_phone = data.get("customer", {}).get("number")
        if not user_phone:
            print("❌ Phone number missing!")
            return jsonify({"error": "Phone number not provided"}), 400

        transcript = data.get("message", {}).get("artifact", {}).get("transcript", "Not Mentioned")
        transcript_hash = hash_transcript(transcript)
        timestamp = datetime.utcnow().isoformat()

        existing_log = call_logs_collection.find_one({
            "Phone": user_phone,
            "Transcript Hash": transcript_hash
        })

        if existing_log:
            print(f"⚠️ Duplicate call log detected for {user_phone}. Skipping insertion.")
            return jsonify({"message": "Duplicate call log detected. Skipping."}), 200

        call_log_entry = {
            "Phone": user_phone,
            "Call Summary": "Processing...",
            "Transcript": transcript,
            "Transcript Hash": transcript_hash,
            "Timestamp": timestamp,
            "Processed": False
        }
        call_logs_collection.insert_one(call_log_entry)
        print("✅ Call log successfully stored.")

        process_transcript(user_phone, transcript)

        return jsonify({"message": "✅ Call log stored and processed successfully!"}), 200

    except Exception as e:
        print(f"❌ Webhook Error: {str(e)}")
        return jsonify({"error": "Webhook processing failed", "details": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
