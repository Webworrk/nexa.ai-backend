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

# Function to retrieve a user by phone number
def get_user_by_phone(phone):
    """Retrieve user details from MongoDB based on phone number."""
    return users_collection.find_one({"Phone": phone})

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
        "Bio_Components": {
            "Company": "Not Mentioned",
            "Experience": "Not Mentioned",
            "Industry": "Not Mentioned",
            "Background": "Not Mentioned",
            "Achievements": "Not Mentioned",
            "Current_Status": "Not Mentioned"
        },
        "Networking Goal": "Not Mentioned",
        "Meeting Type": "Not Mentioned",
        "Proposed Meeting Date": "Not Mentioned",
        "Proposed Meeting Time": "Not Mentioned",
        "Call Summary": "Not Mentioned"
    }
    
    if not transcript or transcript == "Not Available":
        return default_response
        
    try:
        system_prompt = """You are an AI assistant that extracts detailed information and returns it in JSON format.
        Extract the following fields and return them in a JSON object:

        {
            "Name": "Full name if mentioned",
            "Email": "Email if mentioned",
            "Profession": "Role and company name, e.g. 'Co-founder, MedX AI (Healthcare Startup)'",
            "Bio_Components": {
                "Company": "Company name",
                "Experience": "Years of experience",
                "Industry": "Industry sector",
                "Background": "What they do and their expertise",
                "Achievements": "Specific achievements and metrics",
                "Current_Status": "Current company/product status"
            },
            "Networking Goal": "What they want to achieve in detail",
            "Meeting Type": "Type of meeting requested",
            "Proposed Meeting Date": "Any mentioned date",
            "Proposed Meeting Time": "Any mentioned time",
            "Call Summary": "Comprehensive overview of key points discussed"
        }

        Be specific and detailed in the Bio_Components section.
        If a field is not mentioned in the transcript, use 'Not Mentioned' as the value.
        Remember to return the response in valid JSON format."""

        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Please analyze this transcript and return the information in JSON format:\n\n{transcript}"}
            ],
            response_format={"type": "json_object"},
            temperature=0.1
        )

        print(f"📝 OpenAI Response: {response.choices[0].message.content}")
        
        extracted_info = json.loads(response.choices[0].message.content)
        
        # Clean and validate the extracted information
        cleaned_info = {}
        for key in default_response.keys():
            if key == "Bio_Components":
                cleaned_info[key] = {}
                for bio_key in default_response[key].keys():
                    value = str(extracted_info.get(key, {}).get(bio_key, "Not Mentioned")).strip()
                    cleaned_info[key][bio_key] = value if value and value.lower() not in ["none", "null", "undefined", "not mentioned"] else "Not Mentioned"
            else:
                value = str(extracted_info.get(key, "Not Mentioned")).strip()
                cleaned_info[key] = value if value and value.lower() not in ["none", "null", "undefined", "not mentioned"] else "Not Mentioned"
        
        print(f"✨ Cleaned Information: {json.dumps(cleaned_info, indent=2)}")
        return cleaned_info

    except Exception as e:
        print(f"❌ Error in OpenAI processing: {str(e)}")
        print(f"🔍 Stack trace: {traceback.format_exc()}")
        return default_response


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

        processed_count = 0
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
                "Call Summary": "Processing...",
                "Transcript": transcript,
                "Transcript Hash": transcript_hash,
                "Timestamp": timestamp,
                "Processed": False
            }
            
            result = call_logs_collection.insert_one(call_entry)
            if result.inserted_id:
                processed_count += 1
                print(f"✅ Call log stored for {user_phone}")
                process_transcript(user_phone, transcript)

        return jsonify({
            "message": f"✅ Synced {processed_count} new call logs successfully!",
            "total_logs": len(call_logs),
            "processed": processed_count
        }), 200

    except Exception as e:
        print(f"❌ Sync Error: {str(e)}")
        print(f"Stack trace: {traceback.format_exc()}")
        return jsonify({"error": "Syncing call logs failed", "details": str(e)}), 500

@app.route("/vapi-webhook", methods=["POST"])
def vapi_webhook():
    try:
        data = request.get_json()
        if not data:
            print("❌ No JSON received!")
            return jsonify({"error": "No JSON received"}), 400

        print("📥 Incoming Webhook Data:", json.dumps(data, indent=4))

        user_phone = data.get("message", {}).get("customer", {}).get("number")
        event_type = data.get("message", {}).get("type")

        if not user_phone:
            print("❌ Phone number missing!")
            return jsonify({"error": "Phone number not provided"}), 400

        # Check if the user exists in MongoDB
        existing_user = users_collection.find_one({"Phone": user_phone})

        # 🎯 If call is in-progress, personalize the greeting for returning users
        if event_type == "status-update" and data["message"].get("status") == "in-progress":
            if existing_user:
                # Extract name and last networking goal
                name = existing_user.get("Name", "there")
                last_call = existing_user.get("Calls", [{}])[-1]  # Get latest call
                last_goal = last_call.get("Networking Goal", "expanding your network")

                greeting = f"Hey {name}, how are you? I see you're still looking for {last_goal}. Let’s make it happen!"
            else:
                greeting = "Hey there! I'm Nexa, your friendly Networking Manager. Let's get you connected with the right people!"

            return jsonify({"message": greeting}), 200  # ✅ FIX: Return immediately

        # 🎯 If the call ended, process the transcript
        if event_type == "end-of-call-report":
            transcript = data.get("message", {}).get("artifact", {}).get("transcript", "Not Mentioned")
            if not transcript or transcript == "Not Mentioned":
                print("❌ No transcript in webhook data!")
                return jsonify({"error": "No transcript provided"}), 400

            transcript_hash = hash_transcript(transcript)
            timestamp = datetime.utcnow().isoformat()

            # Prevent duplicate logs
            existing_log = call_logs_collection.find_one({
                "Phone": user_phone,
                "Transcript Hash": transcript_hash
            })

            if existing_log:
                print(f"⚠️ Duplicate call log detected for {user_phone}. Skipping insertion.")
                return jsonify({"message": "Duplicate call log detected. Skipping."}), 200

            # Store call log
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
            
            # Process transcript and extract useful information
            processed_data = process_transcript(user_phone, transcript)

            # 🎯 If user exists, update their call history
            if existing_user:
                new_call = {
                    "Call Number": len(existing_user["Calls"]) + 1,
                    "Networking Goal": processed_data.get("Networking Goal", "Not Mentioned"),
                    "Meeting Type": processed_data.get("Meeting Type", "Not Mentioned"),
                    "Proposed Meeting Date": processed_data.get("Proposed Meeting Date", "Not Mentioned"),
                    "Proposed Meeting Time": processed_data.get("Proposed Meeting Time", "Not Mentioned"),
                    "Meeting Status": "Pending Confirmation",
                    "Finalized Meeting Date": None,
                    "Finalized Meeting Time": None,
                    "Meeting Link": None,
                    "Participants Notified": False,
                    "Status": "Completed",
                    "Call Summary": processed_data.get("Call Summary", "Not Mentioned")
                }
                users_collection.update_one({"Phone": user_phone}, {"$push": {"Calls": new_call}})
            else:
                # 🎯 Create new user entry
                new_user = {
                    "Nexa ID": f"NEXA{users_collection.count_documents({}) + 1:05d}",
                    "Name": processed_data.get("Name", "Not Mentioned"),
                    "Email": processed_data.get("Email", "Not Mentioned"),
                    "Phone": user_phone,
                    "Profession": processed_data.get("Profession", "Not Mentioned"),
                    "Bio": f"{processed_data['Bio_Components'].get('Company', 'Not Mentioned')}, "
                           f"{processed_data['Bio_Components'].get('Experience', 'Not Mentioned')} years of experience, "
                           f"Industry: {processed_data['Bio_Components'].get('Industry', 'Not Mentioned')}, "
                           f"{processed_data['Bio_Components'].get('Background', 'Not Mentioned')}, "
                           f"Achievements: {processed_data['Bio_Components'].get('Achievements', 'Not Mentioned')}.",
                    "Signup Status": "Incomplete",
                    "Calls": [
                        {
                            "Call Number": 1,
                            "Networking Goal": processed_data.get("Networking Goal", "Not Mentioned"),
                            "Meeting Type": processed_data.get("Meeting Type", "Not Mentioned"),
                            "Proposed Meeting Date": processed_data.get("Proposed Meeting Date", "Not Mentioned"),
                            "Proposed Meeting Time": processed_data.get("Proposed Meeting Time", "Not Mentioned"),
                            "Meeting Status": "Pending Confirmation",
                            "Finalized Meeting Date": None,
                            "Finalized Meeting Time": None,
                            "Meeting Link": None,
                            "Participants Notified": False,
                            "Status": "Completed",
                            "Call Summary": processed_data.get("Call Summary", "Not Mentioned")
                        }
                    ]
                }
                users_collection.insert_one(new_user)

            return jsonify({"message": "✅ Call log stored and processed successfully!"}), 200

    except Exception as e:
        print(f"❌ Webhook Error: {str(e)}")
        print(f"Stack trace: {traceback.format_exc()}")
        return jsonify({"error": "Webhook processing failed", "details": str(e)}), 500


def process_transcript(user_phone, transcript):
    """Process transcript and update both Users and CallLogs collections."""
    try:
        print(f"Processing transcript for phone: {user_phone}")
        summary = extract_user_info_from_transcript(transcript)
        
        # Format Bio as a comprehensive sentence
        bio_parts = summary.get('Bio_Components', {})
        bio = f"Co-founder at {bio_parts.get('Company', 'their company')} "
        
        if bio_parts.get('Experience') != 'Not Mentioned':
            bio += f"with {bio_parts.get('Experience')} of experience "
        
        if bio_parts.get('Industry') != 'Not Mentioned':
            bio += f"in the {bio_parts.get('Industry')} industry. "
        else:
            bio += ". "
            
        if bio_parts.get('Background') != 'Not Mentioned':
            bio += f"{bio_parts.get('Background')}. "
            
        if bio_parts.get('Achievements') != 'Not Mentioned':
            bio += f"Key achievements include {bio_parts.get('Achievements')}. "
            
        if bio_parts.get('Current_Status') != 'Not Mentioned':
            bio += f"Currently {bio_parts.get('Current_Status')}."

        # Format conversation messages
        messages = []
        for msg in transcript.split('\n'):
            if msg.startswith('AI: '):
                messages.append({
                    "role": "bot",
                    "message": msg[4:].strip()
                })
            elif msg.startswith('User: '):
                messages.append({
                    "role": "user",
                    "message": msg[6:].strip()
                })

        # Find or create user
        user = users_collection.find_one({"Phone": user_phone})
        if not user:
            print(f"👤 Creating new user for phone: {user_phone}")
            next_id = users_collection.count_documents({}) + 1
            user = {
                "Nexa ID": f"NEXA{next_id:05d}",
                "Name": summary.get("Name", "Not Mentioned"),
                "Email": summary.get("Email", "Not Mentioned"),
                "Phone": user_phone,
                "Profession": summary.get("Profession", "Not Mentioned"),
                "Bio": bio,
                "Signup Status": "Incomplete",
                "Calls": []
            }
            result = users_collection.insert_one(user)
            if not result.inserted_id:
                raise Exception("Failed to create new user")
        elif summary.get("Name") != "Not Mentioned" or summary.get("Profession") != "Not Mentioned":
            update_fields = {}
            if summary.get("Name") != "Not Mentioned":
                update_fields["Name"] = summary.get("Name")
            if summary.get("Profession") != "Not Mentioned":
                update_fields["Profession"] = summary.get("Profession")
            update_fields["Bio"] = bio
            
            if update_fields:
                users_collection.update_one(
                    {"Phone": user_phone},
                    {"$set": update_fields}
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
            "Call Summary": summary.get("Call Summary", "No summary available."),
            "Conversation": messages
        }

        # Update Users collection
        users_collection.update_one(
            {"Phone": user_phone},
            {"$push": {"Calls": user_call_log}}
        )

        # Update CallLogs collection
        call_logs_collection.update_one(
            {"Phone": user_phone, "Transcript Hash": hash_transcript(transcript)},
            {"$set": {
                "Call Summary": summary.get("Call Summary", "No summary available."),
                "Messages": messages,
                "Processed": True,
                "Last Updated": datetime.utcnow().isoformat()
            }}
        )

        print(f"✅ Call processed & User Updated: {user_phone}")
        print(f"📝 Call Summary: {summary.get('Call Summary')}")

    except Exception as e:
        print(f"❌ Error Processing Transcript: {str(e)}")
        print(f"Stack trace: {traceback.format_exc()}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
