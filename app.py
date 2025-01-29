from flask import Flask, request, jsonify
import requests
from pymongo import MongoClient
from dotenv import load_dotenv
import os
from datetime import datetime
from openai import OpenAI
import json
import hashlib

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
    """
    Extract relevant user information from call transcript using OpenAI's API with JSON mode.
    Returns a dictionary containing structured information about the call.
    """
    default_info = {
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
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            response_format={ "type": "json_object" },
            messages=[
                {"role": "system", "content": "You are a helpful assistant that extracts structured information from call transcripts. Extract all available information and use 'Not Mentioned' for missing fields."},
                {"role": "user", "content": f"Extract information from this transcript in JSON format:\n\n{transcript}"}
            ],
            functions=[{
                "name": "extract_info",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "Name": {
                            "type": "string",
                            "description": "Name of the person"
                        },
                        "Email": {
                            "type": "string",
                            "description": "Email address"
                        },
                        "Profession": {
                            "type": "string",
                            "description": "Person's profession or role"
                        },
                        "Bio": {
                            "type": "string",
                            "description": "Brief summary about the person"
                        },
                        "Networking Goal": {
                            "type": "string",
                            "description": "What they want to achieve through networking"
                        },
                        "Meeting Type": {
                            "type": "string",
                            "description": "Type of meeting (virtual/in-person)"
                        },
                        "Proposed Meeting Date": {
                            "type": "string",
                            "description": "Proposed date for meeting"
                        },
                        "Proposed Meeting Time": {
                            "type": "string",
                            "description": "Proposed time for meeting"
                        },
                        "Call Summary": {
                            "type": "string",
                            "description": "Brief overview of the conversation"
                        }
                    },
                    "required": ["Name", "Email", "Profession", "Bio", "Networking Goal", 
                               "Meeting Type", "Proposed Meeting Date", "Proposed Meeting Time", 
                               "Call Summary"]
                }
            }],
            temperature=0.3
        )

        try:
            extracted_info = json.loads(response.choices[0].message.content)
            return {**default_info, **extracted_info}  # Merge with defaults
        except (json.JSONDecodeError, AttributeError, IndexError) as e:
            print(f"Error parsing OpenAI response: {str(e)}")
            return default_info

    except Exception as e:
        print(f"Error in OpenAI API call: {str(e)}")
        return default_info

        except Exception as parsing_error:
            print(f"Error parsing OpenAI response: {parsing_error}")
            return {
                "Name": "Not Mentioned",
                "Email": "Not Mentioned",
                "Profession": "Not Mentioned",
                "Bio": "Not Mentioned",
                "Networking Goal": "Not Mentioned",
                "Meeting Type": "Not Mentioned",
                "Proposed Meeting Date": "Not Mentioned",
                "Proposed Meeting Time": "Not Mentioned",
                "Call Summary": "Error processing transcript"
            }

    except Exception as e:
        print(f"Error in OpenAI API call: {str(e)}")
        raise Exception(f"Failed to process transcript: {str(e)}")

@app.route("/sync-vapi-calllogs", methods=["GET"])
def sync_vapi_calllogs():
    try:
        headers = {
            "Authorization": f"Bearer {VAPI_API_KEY}",
            "Content-Type": "application/json"
        }

        # Fetch Call Logs from Vapi.ai
        response = requests.get("https://api.vapi.ai/call", headers=headers, timeout=30)

        if response.status_code != 200:
            return jsonify({"error": "Failed to fetch call logs", "details": response.text}), response.status_code

        call_logs = response.json()

        if not call_logs:
            return jsonify({"message": "No new call logs found!"}), 200

        # Store Each Call Log in MongoDB
        for log in call_logs:
            user_phone = log.get("customer", {}).get("number", "Unknown")
            transcript = log.get("messages", [{}])[-1].get("artifact", {}).get("transcript", "Not Available")
            transcript_hash = hash_transcript(transcript)
            timestamp = datetime.utcnow().isoformat()

            # Check for duplicate logs
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
                "Timestamp": timestamp
            }
            call_logs_collection.insert_one(call_entry)

            # Process & Update User Data from Transcript
            process_transcript(user_phone, transcript)

        return jsonify({"message": f"✅ Synced {len(call_logs)} call logs successfully!"}), 200

    except Exception as e:
        return jsonify({"error": "Syncing call logs failed", "details": str(e)}), 500

@app.route("/vapi-webhook", methods=["POST"])
def vapi_webhook():
    try:
        # Receive JSON data
        data = request.get_json()
        if not data:
            print("❌ No JSON received!")
            return jsonify({"error": "No JSON received"}), 400
        
        print("📥 Incoming Webhook Data:", json.dumps(data, indent=4))
        
        # Extract User Phone Number
        user_phone = data.get("customer", {}).get("number")
        if not user_phone:
            print("❌ Phone number missing!")
            return jsonify({"error": "Phone number not provided"}), 400

        # Extract Call Transcript
        transcript = data.get("message", {}).get("artifact", {}).get("transcript", "Not Mentioned")
        transcript_hash = hash_transcript(transcript)
        timestamp = datetime.utcnow().isoformat()

        # Check for duplicate logs
        existing_log = call_logs_collection.find_one({
            "Phone": user_phone,
            "Transcript Hash": transcript_hash
        })

        if existing_log:
            print(f"⚠️ Duplicate call log detected for {user_phone}. Skipping insertion.")
            return jsonify({"message": "Duplicate call log detected. Skipping."}), 200

        # Store New Call Log
        call_log_entry = {
            "Phone": user_phone,
            "Call Summary": "Processing...",
            "Transcript": transcript,
            "Transcript Hash": transcript_hash,
            "Timestamp": timestamp
        }
        call_logs_collection.insert_one(call_log_entry)
        print("✅ Call log successfully stored.")

        # Process Transcript & Update User Data
        process_transcript(user_phone, transcript)

        return jsonify({"message": "✅ Call log stored and processed successfully!"}), 200

    except Exception as e:
        print(f"❌ Webhook Error: {str(e)}")
        return jsonify({"error": "Webhook processing failed", "details": str(e)}), 500

def process_transcript(user_phone, transcript):
    try:
        summary = extract_user_info_from_transcript(transcript)

        # Find or Create User in MongoDB
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

        # Prepare Call Log Entry
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

        # Store Call Log in Users Collection
        users_collection.update_one(
            {"Phone": user_phone},
            {"$push": {"Calls": user_call_log}}
        )

        # Update CallLogs with Summary
        call_logs_collection.update_one(
            {"Phone": user_phone, "Transcript Hash": hash_transcript(transcript)},
            {"$set": {"Call Summary": summary.get("Call Summary", "No summary available.")}}
        )

        print(f"✅ Call processed & User Updated: {user_phone}")

    except Exception as e:
        print(f"❌ Error Processing Transcript: {e}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
