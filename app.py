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
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Load environment variables
load_dotenv()

def standardize_phone_number(phone):
    """
    Standardize phone number format to E.164 format
    Example: +918098758196
    """
    # Remove any non-digit characters
    phone = ''.join(filter(str.isdigit, str(phone)))
    
    # Handle Indian numbers
    if len(phone) == 10:  # Local number without country code
        return f"+91{phone}"
    elif len(phone) == 11 and phone.startswith('0'):  # Local number with leading 0
        return f"+91{phone[1:]}"
    elif len(phone) == 12 and phone.startswith('91'):  # Number with country code without +
        return f"+{phone}"
    elif len(phone) == 13 and phone.startswith('+91'):  # Complete international format
        return phone
    else:
        raise ValueError(f"Invalid phone number format: {phone}")

# Connect to MongoDB
MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise ValueError("❌ MONGO_URI environment variable is missing!")

try:
    mongo_client = MongoClient(MONGO_URI)
    # Test the connection
    mongo_client.server_info()
    logger.info("✅ MongoDB Connected Successfully")
    
    db = mongo_client["Nexa"]
    call_logs_collection = db["CallLogs"]
    users_collection = db["Users"]
    
except Exception as e:
    logger.error(f"❌ MongoDB Connection Failed: {str(e)}")
    logger.error(f"Stack trace: {traceback.format_exc()}")
    raise

# Initialize OpenAI client
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("❌ OPENAI_API_KEY environment variable is missing!")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Vapi.ai Configuration
VAPI_API_KEY = os.getenv("VAPI_API_KEY")
VAPI_ASSISTANT_ID = os.getenv("VAPI_ASSISTANT_ID")

if not VAPI_API_KEY or not VAPI_ASSISTANT_ID:
    logger.warning("⚠️ WARNING: Missing Vapi.ai API Key or Assistant ID!")

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "message": "Welcome to Nexa Backend! Your AI-powered networking assistant is live.",
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    }), 200

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

        logger.info(f"📝 OpenAI Response: {response.choices[0].message.content}")
        
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
        
        logger.info(f"✨ Cleaned Information: {json.dumps(cleaned_info, indent=2)}")
        return cleaned_info

    except Exception as e:
        logger.error(f"❌ Error in OpenAI processing: {str(e)}")
        logger.error(f"🔍 Stack trace: {traceback.format_exc()}")
        return default_response

@app.route("/sync-vapi-calllogs", methods=["GET"])
def sync_vapi_calllogs():
    try:
        headers = {
            "Authorization": f"Bearer {VAPI_API_KEY}",
            "Content-Type": "application/json"
        }

        response = requests.get(
            "https://api.vapi.ai/call", 
            headers=headers, 
            timeout=30
        )

        if response.status_code != 200:
            logger.error(f"Failed to fetch call logs: {response.text}")
            return jsonify({
                "error": "Failed to fetch call logs", 
                "details": response.text
            }), response.status_code

        call_logs = response.json()

        if not call_logs:
            return jsonify({"message": "No new call logs found!"}), 200

        processed_count = 0
        for log in call_logs:
            user_phone = log.get("customer", {}).get("number", "Unknown")
            try:
                user_phone = standardize_phone_number(user_phone)
            except ValueError as e:
                logger.error(f"Invalid phone number format: {user_phone}")
                continue

            transcript = log.get("messages", [{}])[-1].get("artifact", {}).get("transcript", "Not Available")
            transcript_hash = hash_transcript(transcript)
            timestamp = datetime.utcnow().isoformat()

            existing_log = call_logs_collection.find_one({
                "Phone": user_phone,
                "Transcript Hash": transcript_hash
            })

            if existing_log:
                logger.warning(f"⚠️ Skipping duplicate log for {user_phone}")
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
                logger.info(f"✅ Call log stored for {user_phone}")
                process_transcript(user_phone, transcript)

        return jsonify({
            "message": f"✅ Synced {processed_count} new call logs successfully!",
            "total_logs": len(call_logs),
            "processed": processed_count
        }), 200

    except Exception as e:
        logger.error(f"❌ Sync Error: {str(e)}")
        logger.error(f"Stack trace: {traceback.format_exc()}")
        return jsonify({
            "error": "Syncing call logs failed", 
            "details": str(e)
        }), 500

@app.route("/vapi-webhook", methods=["POST"])
def vapi_webhook():
    try:
        data = request.get_json()
        if not data:
            logger.error("❌ No JSON received!")
            return jsonify({"error": "No JSON received"}), 400
        
        logger.info("📥 Incoming Webhook Data: %s", json.dumps(data, indent=4))
        
        user_phone = data.get("message", {}).get("customer", {}).get("number")
        if not user_phone:
            logger.error("❌ Phone number missing!")
            return jsonify({"error": "Phone number not provided"}), 400

        try:
            user_phone = standardize_phone_number(user_phone)
        except ValueError as e:
            logger.error(f"Invalid phone number format: {user_phone}")
            return jsonify({"error": str(e)}), 400

        transcript = data.get("message", {}).get("artifact", {}).get("transcript", "Not Mentioned")
        if not transcript or transcript == "Not Mentioned":
            logger.error("❌ No transcript in webhook data!")
            return jsonify({"error": "No transcript provided"}), 400

        transcript_hash = hash_transcript(transcript)
        timestamp = datetime.utcnow().isoformat()

        existing_log = call_logs_collection.find_one({
            "Phone": user_phone,
            "Transcript Hash": transcript_hash
        })

        if existing_log:
            logger.warning(f"⚠️ Duplicate call log detected for {user_phone}. Skipping insertion.")
            return jsonify({"message": "Duplicate call log detected. Skipping."}), 200

        call_log_entry = {
            "Phone": user_phone,
            "Call Summary": "Processing...",
            "Transcript": transcript,
            "Transcript Hash": transcript_hash,
            "Timestamp": timestamp,
            "Processed": False
        }
        
        result = call_logs_collection.insert_one(call_log_entry)
        if result.inserted_id:
            logger.info("✅ Call log successfully stored.")
            process_transcript(user_phone, transcript)
            return jsonify({"message": "✅ Call log stored and processed successfully!"}), 200
        else:
            logger.error("❌ Failed to store call log!")
            return jsonify({"error": "Failed to store call log"}), 500

    except Exception as e:
        logger.error(f"❌ Webhook Error: {str(e)}")
        logger.error(f"Stack trace: {traceback.format_exc()}")
        return jsonify({
            "error": "Webhook processing failed", 
            "details": str(e)
        }), 500

def process_transcript(user_phone, transcript):
    """Process transcript and update both Users and CallLogs collections."""
    try:
        logger.info(f"Processing transcript for phone: {user_phone}")
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
            logger.info(f"👤 Creating new user for phone: {user_phone}")
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

        logger.info(f"✅ Call processed & User Updated: {user_phone}")
        logger.info(f"📝 Call Summary: {summary.get('Call Summary')}")

    except Exception as e:
        logger.error(f"❌ Error Processing Transcript: {str(e)}")
        logger.error(f"Stack trace: {traceback.format_exc()}")

@app.route("/user-context/<phone_number>", methods=["GET"])
def get_user_context(phone_number):
    """Endpoint to fetch user context for Vapi.ai."""
    try:
        # Standardize the phone number format
        try:
            standardized_phone = standardize_phone_number(phone_number)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
            
        # Find user in database using standardized format
        user = users_collection.find_one({"Phone": standardized_phone})
        
        if not user:
            logger.info(f"📱 New user with phone: {standardized_phone}")
            return jsonify({
                "exists": False,
                "message": "New user detected"
            }), 200
            
        # Get last 3 calls for recent context
        recent_calls = user.get("Calls", [])[-3:]
        
        # Get networking goals from recent calls
        networking_goals = [
            call.get("Networking Goal") 
            for call in recent_calls 
            if call.get("Networking Goal") != "Not Mentioned"
        ]
        
        # Format the context response
        context = {
            "exists": True,
            "user_info": {
                "name": user.get("Name"),
                "profession": user.get("Profession"),
                "bio": user.get("Bio"),
                "email": user.get("Email"),
                "nexa_id": user.get("Nexa ID"),
                "signup_status": user.get("Signup Status"),
                "total_calls": len(user.get("Calls", [])),
                "networking_goals": networking_goals
            },
            "recent_interactions": [{
                "call_number": call.get("Call Number"),
                "networking_goal": call.get("Networking Goal"),
                "meeting_type": call.get("Meeting Type"),
                "meeting_status": call.get("Meeting Status"),
                "proposed_date": call.get("Proposed Meeting Date"),
                "proposed_time": call.get("Proposed Meeting Time"),
                "call_summary": call.get("Call Summary")
            } for call in recent_calls]
        }
        
        logger.info(f"✅ Context retrieved for user: {standardized_phone}")
        logger.debug(f"📝 Context: {json.dumps(context, indent=2)}")
        
        return jsonify(context), 200
        
    except Exception as e:
        logger.error(f"❌ Error fetching user context: {str(e)}")
        logger.error(f"Stack trace: {traceback.format_exc()}")
        return jsonify({
            "error": "Failed to fetch user context",
            "details": str(e)
        }), 500

if __name__ == "__main__":
    # Use PORT environment variable if available (for Render deployment)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
