from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_caching import Cache
import redis
import requests
from pymongo import MongoClient
from dotenv import load_dotenv
import os
from datetime import datetime
import json
import hashlib
import traceback
import logging
from openai import OpenAI
from flask_cors import CORS
from flask import make_response
from werkzeug.exceptions import HTTPException

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app) 

# Configure Redis for rate limiting
redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')
redis_client = redis.from_url(redis_url)

# Initialize rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# Initialize cache
cache = Cache(app, config={
    'CACHE_TYPE': 'simple',
    'CACHE_DEFAULT_TIMEOUT': 300
})

# Load environment variables
load_dotenv()

# Constants and Configuration
TIMEOUT_SECONDS = 25
REQUIRED_ENV_VARS = {
    "MONGO_URI": "MongoDB connection string",
    "OPENAI_API_KEY": "OpenAI API key",
    "VAPI_API_KEY": "Vapi.ai API key",
    "VAPI_ASSISTANT_ID": "Vapi.ai Assistant ID",
    "VAPI_SECRET_TOKEN": "Vapi.ai Secret Token"
}

# Validate environment variables
for var, description in REQUIRED_ENV_VARS.items():
    if not os.getenv(var):
        raise ValueError(f"❌ Missing required environment variable: {var} ({description})")

# Initialize global variables
VAPI_API_KEY = os.getenv("VAPI_API_KEY")
VAPI_ASSISTANT_ID = os.getenv("VAPI_ASSISTANT_ID")
VAPI_SECRET_TOKEN = os.getenv("VAPI_SECRET_TOKEN")

def validate_vapi_request(request):
    """Validate incoming Vapi.ai requests"""
    token = request.headers.get('x-vapi-secret')
    if not token or token != VAPI_SECRET_TOKEN:
        raise ValueError("Invalid or missing Vapi secret token")

# Connect to MongoDB
try:
    mongo_client = MongoClient(os.getenv("MONGO_URI"))
    mongo_client.server_info()  # Test connection
    logger.info("✅ MongoDB Connected Successfully")
    
    db = mongo_client["Nexa"]
    call_logs_collection = db["CallLogs"]
    users_collection = db["Users"]
    
except Exception as e:
    logger.error(f"❌ MongoDB Connection Failed: {str(e)}")
    logger.error(f"Stack trace: {traceback.format_exc()}")
    raise

# Initialize OpenAI client
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def setup_mongodb_indexes():
    """Setup MongoDB indexes for better query performance"""
    try:
        users_collection.create_index([("Phone", 1)], unique=True)
        call_logs_collection.create_index([
            ("Phone", 1),
            ("Transcript Hash", 1)
        ], unique=True)
        logger.info("✅ MongoDB indexes created successfully")
    except Exception as e:
        logger.error(f"❌ Error creating MongoDB indexes: {str(e)}")

setup_mongodb_indexes()

def standardize_phone_number(phone):
    """
    Standardize phone number to E.164 format
    Valid formats:
    - 10 digits: 9876543210
    - With country code: 919876543210
    - With +: +919876543210
    """
    try:
        # Remove any non-digit characters
        phone = ''.join(filter(str.isdigit, str(phone)))
        
        # Handle different formats
        if len(phone) == 10:  # Local number
            return f"+91{phone}"
        elif len(phone) == 11 and phone.startswith('0'):
            return f"+91{phone[1:]}"
        elif len(phone) == 12 and phone.startswith('91'):
            return f"+{phone}"
        elif len(phone) == 13 and phone.startswith('91'):
            return f"+{phone}"
        else:
            raise ValueError(
                "Invalid phone number format. Must be 10 digits or include +91 country code."
            )
    except Exception as e:
        logger.error(f"Phone number standardization failed: {str(e)}")
        raise

def hash_transcript(transcript):
    """Generate a unique hash for the transcript"""
    return hashlib.sha256(transcript.encode()).hexdigest()

@app.before_request
def log_request_info():
    """Log incoming request details"""
    logger.info(f"Request: {request.method} {request.url}")
    logger.debug(f"Headers: {dict(request.headers)}")
    if request.get_json():
        logger.debug(f"Body: {request.get_json()}")

# After your Flask app initialization:
@app.after_request
def after_request(response):
    response.headers.add('Content-Type', 'application/json')
    return response

@app.errorhandler(HTTPException)
def handle_exception(e):
    return jsonify({
        "error": e.description,
        "status": e.code,
        "timestamp": datetime.utcnow().isoformat()
    }), e.code

@app.errorhandler(500)
def handle_500_error(e):
    return jsonify({
        "error": "Internal server error",
        "status": "error",
        "timestamp": datetime.utcnow().isoformat()
    }), 500

# Add the before_request handler here
@app.before_request
def before_request():
    if request.method == "HEAD":
        return make_response('', 200)

@app.route("/", methods=["GET", "HEAD"])
def home():
    """Home endpoint"""
    data = {
        "message": "Welcome to Nexa Backend! Your AI-powered networking assistant is live.",
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    }
    
    if request.method == "HEAD":
        response = make_response()
        response.headers["Content-Type"] = "application/json"
        return response
        
    return jsonify(data)

@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    try:
        # Test MongoDB connection
        db_status = mongo_client.server_info()
        return jsonify({
            "status": "healthy",
            "database": {
                "status": "connected",
                "version": db_status.get("version"),
                "connection": str(mongo_client.address if mongo_client else "Not connected")
            },
            "environment": {
                "mongo_uri_configured": bool(os.getenv("MONGO_URI")),
                "server_time": datetime.utcnow().isoformat()
            }
        }), 200
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": {
                "message": str(e),
                "type": type(e).__name__
            },
            "environment": {
                "mongo_uri_configured": bool(os.getenv("MONGO_URI")),
                "server_time": datetime.utcnow().isoformat()
            }
        }), 500

def extract_user_info_from_transcript(transcript):
    """Extract user information from transcript using OpenAI"""
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
        logger.error(f"Stack trace: {traceback.format_exc()}")
        return default_response

@app.route("/sync-vapi-calllogs", methods=["GET"])
@limiter.limit("10 per minute")
def sync_vapi_calllogs():
    """Sync call logs from Vapi.ai"""
    try:
        validate_vapi_request(request)
        
        headers = {
            "Authorization": f"Bearer {VAPI_API_KEY}",
            "Content-Type": "application/json"
        }

        response = requests.get(
            "https://api.vapi.ai/call", 
            headers=headers, 
            timeout=TIMEOUT_SECONDS
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
            try:
                user_phone = log.get("customer", {}).get("number", "Unknown")
                user_phone = standardize_phone_number(user_phone)
                
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

            except Exception as e:
                logger.error(f"Error processing log: {str(e)}")
                continue

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
@limiter.limit("30 per minute")
def vapi_webhook():
    """Handle incoming webhooks from Vapi.ai"""
    try:
        validate_vapi_request(request)
        
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
            return jsonify({
                "message": "✅ Call log stored and processed successfully!",
                "status": "success",
                "timestamp": timestamp
            }), 200
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
                "Calls": [],
                "Created At": datetime.utcnow().isoformat(),
                "Last Updated": datetime.utcnow().isoformat()
            }
            result = users_collection.insert_one(user)
            if not result.inserted_id:
                raise Exception("Failed to create new user")
        elif summary.get("Name") != "Not Mentioned" or summary.get("Profession") != "Not Mentioned":
            update_fields = {
                "Last Updated": datetime.utcnow().isoformat()
            }
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
            "Timestamp": datetime.utcnow().isoformat(),
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

        # Update Users collection with new call
        users_collection.update_one(
            {"Phone": user_phone},
            {
                "$push": {"Calls": user_call_log},
                "$set": {"Last Updated": datetime.utcnow().isoformat()}
            }
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
        raise

@app.route("/user-context/<phone_number>", methods=["GET"])
@limiter.limit("60 per minute")
@cache.memoize(timeout=300)  # Cache for 5 minutes
def get_user_context(phone_number):
    """Endpoint to fetch user context for Vapi.ai."""
    try:
        # Validate and standardize the phone number
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
                "message": "New user detected",
                "timestamp": datetime.utcnow().isoformat()
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
                "networking_goals": networking_goals,
                "created_at": user.get("Created At"),
                "last_updated": user.get("Last Updated")
            },
            "recent_interactions": [{
                "call_number": call.get("Call Number"),
                "timestamp": call.get("Timestamp"),
                "networking_goal": call.get("Networking Goal"),
                "meeting_type": call.get("Meeting Type"),
                "meeting_status": call.get("Meeting Status"),
                "proposed_date": call.get("Proposed Meeting Date"),
                "proposed_time": call.get("Proposed Meeting Time"),
                "call_summary": call.get("Call Summary")
            } for call in recent_calls],
            "timestamp": datetime.utcnow().isoformat()
        }
        
        logger.info(f"✅ Context retrieved for user: {standardized_phone}")
        logger.debug(f"📝 Context: {json.dumps(context, indent=2)}")
        
        return jsonify(context), 200
        
    except Exception as e:
        logger.error(f"❌ Error fetching user context: {str(e)}")
        logger.error(f"Stack trace: {traceback.format_exc()}")
        return jsonify({
            "error": "Failed to fetch user context",
            "details": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }), 500

if __name__ == "__main__":
    # Use PORT environment variable if available (for Render deployment)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
