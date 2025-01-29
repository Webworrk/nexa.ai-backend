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

def get_user_context(phone_number):
    """Get existing user context from MongoDB."""
    try:
        user = users_collection.find_one({"Phone": phone_number})
        if user:
            # Get the most recent call details
            last_call = user.get("Calls", [])[-1] if user.get("Calls") else {}
            investment_details = None
            if last_call.get("Investment Details"):
                investment_details = {
                    "amount": last_call["Investment Details"].get("Amount", "Not Mentioned"),
                    "purpose": last_call["Investment Details"].get("Purpose", "Not Mentioned"),
                    "status": last_call["Investment Details"].get("Current Status", "Not Mentioned")
                }

            return {
                "is_returning": True,
                "nexa_id": user.get("Nexa ID"),
                "name": user.get("Name"),
                "profession": user.get("Profession"),
                "company": {
                    "name": user.get("Company", {}).get("Name"),
                    "industry": user.get("Company", {}).get("Industry"),
                    "stage": user.get("Company", {}).get("Stage"),
                    "achievements": user.get("Company", {}).get("Achievements")
                },
                "experience": user.get("Experience", "Not Mentioned"),
                "bio": user.get("Bio"),
                "last_goal": last_call.get("Networking Goal"),
                "last_interaction": last_call.get("Call Summary"),
                "last_meeting": {
                    "type": last_call.get("Meeting Type"),
                    "status": last_call.get("Meeting Status"),
                    "date": last_call.get("Finalized Meeting Date")
                },
                "investment_details": investment_details
            }
        return {
            "is_returning": False,
            "nexa_id": None,
            "name": None,
            "profession": None,
            "company": None,
            "experience": None,
            "bio": None,
            "last_goal": None,
            "last_interaction": None,
            "last_meeting": None,
            "investment_details": None
        }
    except Exception as e:
        print(f"❌ Error getting user context: {str(e)}")
        return None

def extract_user_info_from_transcript(transcript, user_context=None):
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
        "Investment_Details": {
            "Amount": "Not Mentioned",
            "Purpose": "Not Mentioned",
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
        # Prepare context information for GPT
        context_info = ""
        if user_context and user_context["is_returning"]:
            context_info = f"""
            Previous User Information:
            - Name: {user_context['name']}
            - Company: {user_context['company']['name']}
            - Industry: {user_context['company']['industry']}
            - Stage: {user_context['company']['stage']}
            - Experience: {user_context['experience']}
            - Last Goal: {user_context['last_goal']}
            - Last Meeting: {user_context['last_meeting']['type']} ({user_context['last_meeting']['status']})
            - Investment Status: {user_context['investment_details']['status'] if user_context['investment_details'] else 'Not Mentioned'}
            """

        system_prompt = f"""You are an AI assistant that extracts and updates user information from conversations. 
        {context_info}
        
        Extract and merge information with the context above, updating or adding new details as mentioned in the conversation.
        Return in JSON format:

        {{
            "Name": "Full name if mentioned",
            "Email": "Email if mentioned",
            "Profession": "Role and company name",
            "Bio_Components": {{
                "Company": "Company name",
                "Experience": "Years of experience",
                "Industry": "Industry sector",
                "Background": "Their full background and expertise",
                "Achievements": "Key achievements with metrics",
                "Current_Status": "Current company/product status"
            }},
            "Investment_Details": {{
                "Amount": "Amount seeking/received",
                "Purpose": "Purpose of investment",
                "Current_Status": "Current funding status"
            }},
            "Networking Goal": "Current networking objective",
            "Meeting Type": "Type of meeting requested",
            "Proposed Meeting Date": "Any mentioned date",
            "Proposed Meeting Time": "Any mentioned time",
            "Call Summary": "Brief overview including any updates from previous interactions"
        }}"""

        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Analyze this transcript considering previous context:\n\n{transcript}"}
            ],
            response_format={"type": "json_object"},
            temperature=0.1
        )

        print(f"📝 OpenAI Response: {response.choices[0].message.content}")
        
        extracted_info = json.loads(response.choices[0].message.content)
        
        # Clean and validate the extracted information
        cleaned_info = {}
        for key in default_response.keys():
            if key in ["Bio_Components", "Investment_Details"]:
                cleaned_info[key] = {}
                for sub_key in default_response[key].keys():
                    value = str(extracted_info.get(key, {}).get(sub_key, "Not Mentioned")).strip()
                    cleaned_info[key][sub_key] = value if value and value.lower() not in ["none", "null", "undefined", "not mentioned"] else "Not Mentioned"
            else:
                value = str(extracted_info.get(key, "Not Mentioned")).strip()
                cleaned_info[key] = value if value and value.lower() not in ["none", "null", "undefined", "not mentioned"] else "Not Mentioned"
        
        print(f"✨ Cleaned Information: {json.dumps(cleaned_info, indent=2)}")
        return cleaned_info

    except Exception as e:
        print(f"❌ Error in OpenAI processing: {str(e)}")
        print(f"🔍 Stack trace: {traceback.format_exc()}")
        return default_response

def process_transcript(user_phone, transcript, user_context=None):
    """Process transcript and update both Users and CallLogs collections."""
    try:
        print(f"Processing transcript for phone: {user_phone}")
        summary = extract_user_info_from_transcript(transcript, user_context)
        
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

        # Format messages for the conversation log
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

        # Prepare company information
        company_info = {
            "Name": bio_parts.get("Company", "Not Mentioned"),
            "Industry": bio_parts.get("Industry", "Not Mentioned"),
            "Stage": bio_parts.get("Current_Status", "Not Mentioned"),
            "Achievements": bio_parts.get("Achievements", "Not Mentioned")
        }

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
                "Company": company_info,
                "Experience": bio_parts.get("Experience", "Not Mentioned"),
                "Bio": bio,
                "Signup Status": "Incomplete",
                "Calls": []
            }
            result = users_collection.insert_one(user)
            if not result.inserted_id:
                raise Exception("Failed to create new user")
        else:
            # Update user info with any new information
            update_fields = {}
            if summary.get("Name") != "Not Mentioned":
                update_fields["Name"] = summary.get("Name")
            if summary.get("Profession") != "Not Mentioned":
                update_fields["Profession"] = summary.get("Profession")
            if summary.get("Email") != "Not Mentioned":
                update_fields["Email"] = summary.get("Email")
            
            # Update company info if new details available
            for key, value in company_info.items():
                if value != "Not Mentioned":
                    update_fields[f"Company.{key}"] = value
            
            if bio_parts.get("Experience") != "Not Mentioned":
                update_fields["Experience"] = bio_parts.get("Experience")
            
            update_fields["Bio"] = bio
            update_fields["Last Updated"] = datetime.utcnow().isoformat()
            
            if update_fields:
                users_collection.update_one(
                    {"Phone": user_phone},
                    {"$set": update_fields}
                )

        # Prepare call log entry with rich information
        user_call_log = {
            "Call Number": len(user.get("Calls", [])) + 1,
            "Networking Goal": summary.get("Networking Goal", "Not Mentioned"),
            "Investment Details": summary.get("Investment_Details", {}),
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
            "Conversation": messages,
            "Previous Context": user_context
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
                "User Context": user_context,
                "Updated Information": update_fields if 'update_fields' in locals() else None,
                "Processed": True,
                "Last Updated": datetime.utcnow().isoformat()
            }}
        )

        print(f"✅ Call processed & User Updated: {user_phone}")
        print(f"📝 Call Summary: {summary.get('Call Summary')}")

    except Exception as e:
        print(f"❌ Error Processing Transcript: {str(e)}")
        print(f"Stack trace: {traceback.format_exc()}")

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Welcome to Nexa Backend! Your AI-powered networking assistant is live."}), 200

def hash_transcript(transcript):
    """Generate a unique hash for the transcript to prevent duplicates."""
    return hashlib.sha256(transcript.encode()).hexdigest()

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
            
            # Get user context for returning users
            user_context = get_user_context(user_phone)
            if user_context and user_context["is_returning"]:
                print(f"📱 Returning User Found: {json.dumps(user_context, indent=2)}")
            
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
                "User Context": user_context,
                "Processed": False,
                "Call Type": "Outbound"
            }
            
            result = call_logs_collection.insert_one(call_entry)
            if result.inserted_id:
                processed_count += 1
                print(f"✅ Call log stored for {user_phone}")
                process_transcript(user_phone, transcript, user_context)

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
        if not user_phone:
            print("❌ Phone number missing!")
            return jsonify({"error": "Phone number not provided"}), 400

        # Get user context for returning users
        user_context = get_user_context(user_phone)
        if user_context:
            print(f"📱 User Context Found: {json.dumps(user_context, indent=2)}")

        transcript = data.get("message", {}).get("artifact", {}).get("transcript", "Not Mentioned")
        if not transcript or transcript == "Not Mentioned":
            print("❌ No transcript in webhook data!")
            return jsonify({"error": "No transcript provided"}), 400

        transcript_hash = hash_transcript(transcript)
        timestamp = datetime.utcnow().isoformat()

        # Enhance messages with user context
        messages_data = data.get("message", {}).get("artifact", {}).get("messages", [])
        if user_context and user_context["is_returning"]:
            context_message = {
                "role": "system",
                "content": f"""This is a returning user:
                - Name: {user_context['name']}
                - Company: {user_context['company']['name'] if user_context['company'] else 'Not Mentioned'}
                - Industry: {user_context['company']['industry'] if user_context['company'] else 'Not Mentioned'}
                - Last Goal: {user_context['last_goal']}
                - Last Interaction: {user_context['last_interaction']}
                - Investment Status: {user_context['investment_details']['status'] if user_context['investment_details'] else 'Not Mentioned'}
                """
            }
            messages_data.insert(0, context_message)

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
            "User Context": user_context,
            "Enhanced Messages": messages_data,
            "Call Type": "Inbound",
            "Processed": False
        }
        
        result = call_logs_collection.insert_one(call_log_entry)
        if result.inserted_id:
            print("✅ Call log successfully stored.")
            process_transcript(user_phone, transcript, user_context)
            return jsonify({"message": "✅ Call log stored and processed successfully!"}), 200
        else:
            print("❌ Failed to store call log!")
            return jsonify({"error": "Failed to store call log"}), 500

    except Exception as e:
        print(f"❌ Webhook Error: {str(e)}")
        print(f"Stack trace: {traceback.format_exc()}")
        return jsonify({"error": "Webhook processing failed", "details": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
