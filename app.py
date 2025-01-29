from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta
import json
import hashlib
import traceback
import asyncio
import logging
import re
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from openai import OpenAI
from retry import retry

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Initialize rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# Load environment variables
load_dotenv()

# Configuration class
@dataclass
class Config:
    MONGO_URI: str = os.getenv("MONGO_URI")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY")
    VAPI_API_KEY: str = os.getenv("VAPI_API_KEY")
    VAPI_ASSISTANT_ID: str = os.getenv("VAPI_ASSISTANT_ID")
    MAX_RETRIES: int = 3
    RETRY_DELAY: int = 2
    CONVERSATION_TIMEOUT: int = 300  # 5 minutes in seconds
    DEFAULT_LANGUAGE: str = "en"

config = Config()

# Validate configuration
if not all([config.MONGO_URI, config.OPENAI_API_KEY, config.VAPI_API_KEY, config.VAPI_ASSISTANT_ID]):
    raise ValueError("Missing required environment variables!")

# Database connection with retry logic and async support
class DatabaseConnection:
    def __init__(self):
        self.client = None
        self.db = None
        self.connect()

    @retry(PyMongoError, tries=3, delay=2)
    def connect(self):
        try:
            self.client = AsyncIOMotorClient(
                config.MONGO_URI,
                serverSelectionTimeoutMS=5000,
                retryWrites=True,
                maxPoolSize=50
            )
            self.db = self.client["Nexa"]
            logger.info("✅ MongoDB Connected Successfully")
        except PyMongoError as e:
            logger.error(f"❌ MongoDB Connection Failed: {str(e)}")
            raise

db_conn = DatabaseConnection()

# Initialize OpenAI client
openai_client = OpenAI(api_key=config.OPENAI_API_KEY)

class ConversationManager:
    def __init__(self):
        self.context_cache = {}
        self.active_conversations = {}

    def is_conversation_active(self, phone_number: str) -> bool:
        if phone_number in self.active_conversations:
            last_activity = self.active_conversations[phone_number]
            return (datetime.utcnow() - last_activity).seconds < config.CONVERSATION_TIMEOUT
        return False

    def update_conversation_activity(self, phone_number: str):
        self.active_conversations[phone_number] = datetime.utcnow()

    def end_conversation(self, phone_number: str):
        if phone_number in self.active_conversations:
            del self.active_conversations[phone_number]

conversation_manager = ConversationManager()

class UserContextManager:
    def __init__(self, db_connection: DatabaseConnection):
        self.db = db_connection.db
        self.users_collection = self.db["Users"]
        self.context_cache = {}

    @retry(PyMongoError, tries=3, delay=2)
    async def get_user_context(self, phone_number: str) -> Dict[str, Any]:
        """Get and cache user context with improved error handling."""
        try:
            # Check cache first
            if phone_number in self.context_cache:
                cached_context = self.context_cache[phone_number]
                if (datetime.utcnow() - cached_context['timestamp']).seconds < 300:
                    return cached_context['data']

            # Query database
            user = await self.users_collection.find_one(
                {"Phone": phone_number},
                {'Calls': {'$slice': -1}}
            )

            if not user:
                return self._create_default_context()

            # Process user data
            context = self._process_user_data(user)
            
            # Update cache
            self.context_cache[phone_number] = {
                'data': context,
                'timestamp': datetime.utcnow()
            }

            return context

        except PyMongoError as e:
            logger.error(f"Database error in get_user_context: {str(e)}")
            return self._create_default_context()
        except Exception as e:
            logger.error(f"Unexpected error in get_user_context: {str(e)}")
            return self._create_default_context()

    def _process_user_data(self, user: Dict) -> Dict[str, Any]:
        """Process user data into context format."""
        last_call = user.get('Calls', [])[-1] if user.get('Calls') else {}
        
        investment_details = None
        if last_call.get('Investment Details'):
            investment_details = {
                'amount': last_call['Investment Details'].get('Amount', 'Not Mentioned'),
                'purpose': last_call['Investment Details'].get('Purpose', 'Not Mentioned'),
                'status': last_call['Investment Details'].get('Current Status', 'Not Mentioned')
            }

        return {
            'is_returning': True,
            'nexa_id': user.get('Nexa ID'),
            'name': user.get('Name'),
            'profession': user.get('Profession'),
            'company': {
                'name': user.get('Company', {}).get('Name'),
                'industry': user.get('Company', {}).get('Industry'),
                'stage': user.get('Company', {}).get('Stage'),
                'achievements': user.get('Company', {}).get('Achievements')
            },
            'experience': user.get('Experience', 'Not Mentioned'),
            'bio': user.get('Bio'),
            'last_goal': last_call.get('Networking Goal'),
            'last_interaction': last_call.get('Call Summary'),
            'last_meeting': {
                'type': last_call.get('Meeting Type'),
                'status': last_call.get('Meeting Status'),
                'date': last_call.get('Finalized Meeting Date')
            },
            'investment_details': investment_details
        }

    def _create_default_context(self) -> Dict[str, Any]:
        """Create default context for new users."""
        return {
            'is_returning': False,
            'nexa_id': None,
            'name': None,
            'profession': None,
            'company': None,
            'experience': None,
            'bio': None,
            'last_goal': None,
            'last_interaction': None,
            'last_meeting': None,
            'investment_details': None
        }

    @retry(PyMongoError, tries=3, delay=2)
    async def update_user_context(self, phone_number: str, new_data: Dict[str, Any]):
        """Update user context with new information."""
        try:
            update_fields = {}
            
            # Process new data
            if new_data.get('name') and new_data['name'] != 'Not Mentioned':
                update_fields['Name'] = new_data['name']
            if new_data.get('profession') and new_data['profession'] != 'Not Mentioned':
                update_fields['Profession'] = new_data['profession']
            if new_data.get('company'):
                for key, value in new_data['company'].items():
                    if value and value != 'Not Mentioned':
                        update_fields[f'Company.{key}'] = value

            if update_fields:
                update_fields['Last Updated'] = datetime.utcnow()
                
                result = await self.users_collection.update_one(
                    {'Phone': phone_number},
                    {'$set': update_fields}
                )

                if result.modified_count > 0:
                    # Clear cache to force refresh
                    if phone_number in self.context_cache:
                        del self.context_cache[phone_number]
                    logger.info(f"Updated user context for {phone_number}")
                    return True

            return False

        except PyMongoError as e:
            logger.error(f"Database error in update_user_context: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in update_user_context: {str(e)}")
            raise

class TranscriptProcessor:
    def __init__(self, openai_client: OpenAI):
        self.openai_client = openai_client
        self.processed_transcripts = set()

    def hash_transcript(self, transcript: str) -> str:
        """Generate unique hash for transcript."""
        return hashlib.sha256(transcript.encode()).hexdigest()

    async def process_transcript(self, transcript: str, user_context: Dict[str, Any]) -> Dict[str, Any]:
        """Process transcript with improved error handling and retry logic."""
        if not transcript or transcript == "Not Available":
            return self._create_default_response()

        transcript_hash = self.hash_transcript(transcript)
        if transcript_hash in self.processed_transcripts:
            logger.info(f"Transcript already processed: {transcript_hash}")
            return self._create_default_response()

        try:
            # Prepare context information
            context_info = self._prepare_context_info(user_context)
            
            # Call OpenAI for analysis
            response = await self._analyze_transcript(transcript, context_info)
            
            # Process and validate response
            processed_info = self._process_openai_response(response)
            
            # Mark transcript as processed
            self.processed_transcripts.add(transcript_hash)
            
            return processed_info

        except Exception as e:
            logger.error(f"Error processing transcript: {str(e)}")
            logger.error(f"Stack trace: {traceback.format_exc()}")
            return self._create_default_response()

    def _prepare_context_info(self, user_context: Dict[str, Any]) -> str:
        """Prepare context information for OpenAI."""
        if user_context and user_context["is_returning"]:
            return f"""
            Previous User Information:
            - Name: {user_context['name']}
            - Company: {user_context['company']['name'] if user_context['company'] else 'Not Mentioned'}
            - Industry: {user_context['company']['industry'] if user_context['company'] else 'Not Mentioned'}
            - Experience: {user_context['experience']}
            - Last Goal: {user_context['last_goal']}
            - Last Meeting: {user_context['last_meeting']['type']} ({user_context['last_meeting']['status']})
            - Investment Status: {user_context['investment_details']['status'] if user_context['investment_details'] else 'Not Mentioned'}
            """
        return ""

    @retry(Exception, tries=3, delay=2)
    async def _analyze_transcript(self, transcript: str, context_info: str) -> Dict[str, Any]:
        """Analyze transcript using OpenAI with retry logic."""
        system_prompt = f"""You are an AI assistant that extracts and updates user information from conversations. 
        {context_info}
        Extract and merge information with the context above, updating or adding new details as mentioned in the conversation.
        Return in JSON format with specified fields."""

        response = await self.openai_client.chat.completions.acreate(
            model="gpt-3.5-turbo-1106",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Analyze this transcript:\n\n{transcript}"}
            ],
            response_format={"type": "json_object"},
            temperature=0.1
        )

        return json.loads(response.choices[0].message.content)

    def _process_openai_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Process and validate OpenAI response."""
        try:
            processed_response = self._create_default_response()
            
            if not response:
                return processed_response

            # Process basic fields
            fields_to_process = {
                "Name": "Name",
                "Email": "Email",
                "Profession": "Profession",
                "Networking Goal": "Networking Goal",
                "Meeting Type": "Meeting Type",
                "Proposed Meeting Date": "Proposed Meeting Date",
                "Proposed Meeting Time": "Proposed Meeting Time",
                "Call Summary": "Call Summary"
            }

            for api_field, response_field in fields_to_process.items():
                if response.get(response_field) and response[response_field] != "Not Mentioned":
                    cleaned_value = self._sanitize_field(response[response_field])
                    if cleaned_value:
                        processed_response[api_field] = cleaned_value

            # Process Bio Components
            if response.get("Bio_Components"):
                bio_fields = [
                    "Company", "Experience", "Industry", 
                    "Background", "Achievements", "Current_Status"
                ]
                for field in bio_fields:
                    value = response["Bio_Components"].get(field)
                    if value and value != "Not Mentioned":
                        cleaned_value = self._sanitize_field(value)
                        if cleaned_value:
                            processed_response["Bio_Components"][field] = cleaned_value

            # Process Investment Details
            if response.get("Investment_Details"):
                investment_fields = ["Amount", "Purpose", "Current_Status"]
                for field in investment_fields:
                    value = response["Investment_Details"].get(field)
                    if value and value != "Not Mentioned":
                        cleaned_value = self._sanitize_field(value)
                        if cleaned_value:
                            processed_response["Investment_Details"][field] = cleaned_value

            return processed_response

        except Exception as e:
            logger.error(f"Error processing OpenAI response: {str(e)}")
            return self._create_default_response()

    def _sanitize_field(self, value: str) -> str:
        """Sanitize field values to prevent injection and ensure data quality."""
        try:
            # Remove any HTML tags
            clean_value = re.sub(r'<[^>]*>', '', str(value))
            
            # Remove any special characters except basic punctuation
            clean_value = re.sub(r'[^\w\s\-.,!?@]', '', clean_value)
            
            # Remove extra whitespace
            clean_value = ' '.join(clean_value.split())
            
            # Limit length to prevent excessive data
            MAX_LENGTH = 1000
            if len(clean_value) > MAX_LENGTH:
                clean_value = clean_value[:MAX_LENGTH]
                
            return clean_value if clean_value else "Not Mentioned"
            
        except Exception as e:
            logger.error(f"Error sanitizing field: {str(e)}")
            return "Not Mentioned"

    def _create_default_response(self) -> Dict[str, Any]:
        """Create default response structure."""
        return {
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

class WebhookHandler:
    def __init__(self):
        self.transcript_processor = TranscriptProcessor(openai_client)
        self.user_context_manager = UserContextManager(db_conn)
        self.conversation_manager = conversation_manager

    async def handle_webhook(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle incoming webhook with improved error handling."""
        try:
            # Validate webhook data
            if not self._validate_webhook_data(data):
                return self._create_error_response("Invalid webhook data")

            # Extract phone number and verify
            phone_number = self._extract_phone_number(data)
            if not phone_number:
                return self._create_error_response("Invalid phone number")

            # Get user context
            user_context = await self.user_context_manager.get_user_context(phone_number)

            # Process webhook based on type
            webhook_type = data.get('message', {}).get('type')
            if webhook_type == 'status-update':
                return await self._handle_status_update(data, phone_number, user_context)
            elif webhook_type == 'end-of-call-report':
                return await self._handle_end_of_call(data, phone_number, user_context)
            else:
                return self._create_error_response(f"Unsupported webhook type: {webhook_type}")

        except Exception as e:
            logger.error(f"Error handling webhook: {str(e)}")
            logger.error(f"Stack trace: {traceback.format_exc()}")
            return self._create_error_response(str(e))

    async def _handle_status_update(self, data: Dict[str, Any], phone_number: str, 
                                  user_context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle status update webhook type."""
        try:
            message_data = data.get('message', {})
            status = message_data.get('status')
            
            if status == 'in-progress':
                self.conversation_manager.update_conversation_activity(phone_number)
                
                # Process transcript if available
                transcript = self._extract_transcript(message_data)
                if transcript:
                    processed_info = await self.transcript_processor.process_transcript(
                        transcript, user_context
                    )
                    await self._update_call_log(phone_number, processed_info, transcript)
                
                return self._create_success_response("Status update processed")
                
            elif status == 'ended':
                self.conversation_manager.end_conversation(phone_number)
                return self._create_success_response("Call ended")
                
            return self._create_success_response(f"Status {status} acknowledged")

        except Exception as e:
            logger.error(f"Error handling status update: {str(e)}")
            return self._create_error_response(str(e))

    async def _handle_end_of_call(self, data: Dict[str, Any], phone_number: str, 
                                 user_context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle end-of-call report webhook type."""
        try:
            # Extract call summary and analysis
            message_data = data.get('message', {})
            analysis = message_data.get('analysis', {})
            summary = analysis.get('summary', '')
            
            # Get full transcript
            transcript = self._extract_transcript(message_data)
            if not transcript:
                return self._create_error_response("No transcript available")

            # Process final transcript
            processed_info = await self.transcript_processor.process_transcript(
                transcript, user_context
            )
            
            # Add call summary
            processed_info['Call Summary'] = summary
            
            # Update call logs and user context
            await self._update_call_log(phone_number, processed_info, transcript, is_final=True)
            await self._update_user_metrics(phone_number, data)
            
            self.conversation_manager.end_conversation(phone_number)
            
            return self._create_success_response(
                "End of call report processed",
                {"summary": summary}
            )

        except Exception as e:
            logger.error(f"Error handling end of call: {str(e)}")
            return self._create_error_response(str(e))

    async def _update_call_log(self, phone_number: str, processed_info: Dict[str, Any], 
                             transcript: str, is_final: bool = False) -> None:
        """Update call logs with processed information."""
        try:
            timestamp = datetime.utcnow().isoformat()
            transcript_hash = self.transcript_processor.hash_transcript(transcript)

            # Format messages for the conversation log
            messages = self._format_messages(transcript)

            call_log_entry = {
                "Phone": phone_number,
                "Call Summary": processed_info.get('Call Summary', 'Processing...'),
                "Transcript": transcript,
                "Transcript Hash": transcript_hash,
                "Timestamp": timestamp,
                "Messages": messages,
                "Processed": is_final,
                "Updated Information": processed_info,
                "Call Type": "Inbound",
                "Status": "Completed" if is_final else "In Progress"
            }

            result = await db_conn.db["CallLogs"].update_one(
                {"Phone": phone_number, "Transcript Hash": transcript_hash},
                {"$set": call_log_entry},
                upsert=True
            )

            if result.modified_count > 0 or result.upserted_id:
                logger.info(f"Call log updated for {phone_number}")
                if is_final:
                    await self._update_user_calls(phone_number, call_log_entry)
            else:
                logger.warning(f"No call log update performed for {phone_number}")

        except Exception as e:
            logger.error(f"Error updating call log: {str(e)}")
            raise

    async def _update_user_calls(self, phone_number: str, call_log: Dict[str, Any]) -> None:
        """Update user's calls array with latest call information."""
        try:
            user = await db_conn.db["Users"].find_one({"Phone": phone_number})
            
            if not user:
                logger.warning(f"User not found for phone: {phone_number}")
                return

            user_call_log = {
                "Call Number": len(user.get("Calls", [])) + 1,
                "Timestamp": call_log["Timestamp"],
                "Call Type": call_log["Call Type"],
                "Call Summary": call_log["Call Summary"],
                "Networking Goal": call_log["Updated Information"].get("Networking Goal", "Not Mentioned"),
                "Investment Details": call_log["Updated Information"].get("Investment_Details", {}),
                "Meeting Type": call_log["Updated Information"].get("Meeting Type", "Not Mentioned"),
                "Proposed Meeting Date": call_log["Updated Information"].get("Proposed Meeting Date", "Not Mentioned"),
                "Proposed Meeting Time": call_log["Updated Information"].get("Proposed Meeting Time", "Not Mentioned"),
                "Meeting Status": "Pending Confirmation",
                "Finalized Meeting Date": None,
                "Finalized Meeting Time": None,
                "Meeting Link": None,
                "Participants Notified": False,
                "Status": "Completed",
                "Messages": call_log.get("Messages", [])
            }

            result = await db_conn.db["Users"].update_one(
                {"Phone": phone_number},
                {
                    "$push": {"Calls": user_call_log},
                    "$set": {
                        "Last Updated": datetime.utcnow().isoformat(),
                        "Last Call Date": datetime.utcnow().isoformat()
                    }
                }
            )

            if result.modified_count > 0:
                logger.info(f"User calls updated for {phone_number}")
            else:
                logger.warning(f"No user calls update performed for {phone_number}")

        except Exception as e:
            logger.error(f"Error updating user calls: {str(e)}")
            raise

    async def _update_user_metrics(self, phone_number: str, call_data: Dict[str, Any]) -> None:
        """Update user metrics based on call data."""
        try:
            duration = call_data.get('message', {}).get('durationSeconds', 0)
            cost = call_data.get('message', {}).get('cost', 0)
            success_evaluation = call_data.get('message', {}).get('analysis', {}).get('successEvaluation', False)

            metrics_update = {
                "$inc": {
                    "TotalCalls": 1,
                    "TotalDuration": duration,
                    "TotalCost": cost,
                    "SuccessfulCalls": 1 if success_evaluation else 0
                },
                "$set": {
                    "LastCallDate": datetime.utcnow().isoformat(),
                    "LastCallSuccess": success_evaluation,
                    "AverageCallDuration": {
                        "$divide": [
                            {"$add": ["$TotalDuration", duration]},
                            {"$add": ["$TotalCalls", 1]}
                        ]
                    }
                }
            }

            result = await db_conn.db["Users"].update_one(
                {"Phone": phone_number},
                metrics_update
            )

            if result.modified_count > 0:
                logger.info(f"User metrics updated for {phone_number}")
            else:
                logger.warning(f"No user metrics update performed for {phone_number}")

        except Exception as e:
            logger.error(f"Error updating user metrics: {str(e)}")
            raise

    def _validate_webhook_data(self, data: Dict[str, Any]) -> bool:
        """Validate webhook data structure."""
        required_fields = ['message']
        return all(field in data for field in required_fields)

    def _extract_phone_number(self, data: Dict[str, Any]) -> Optional[str]:
        """Extract and validate phone number from webhook data."""
        try:
            phone_number = data.get('message', {}).get('customer', {}).get('number')
            if phone_number:
                # Basic phone number validation
                clean_number = re.sub(r'[^\d+]', '', phone_number)
                return clean_number if clean_number else None
            return None
        except Exception as e:
            logger.error(f"Error extracting phone number: {str(e)}")
            return None

    def _extract_transcript(self, message_data: Dict[str, Any]) -> Optional[str]:
        """Extract transcript from message data."""
        try:
            if 'artifact' in message_data:
                return message_data.get('artifact', {}).get('transcript')
            return None
        except Exception as e:
            logger.error(f"Error extracting transcript: {str(e)}")
            return None

    def _format_messages(self, transcript: str) -> List[Dict[str, Any]]:
        """Format transcript into structured messages."""
        try:
            messages = []
            for line in transcript.split('\n'):
                if line.startswith('AI: '):
                    messages.append({
                        "role": "bot",
                        "message": line[4:].strip(),
                        "timestamp": datetime.utcnow().isoformat()
                    })
                elif line.startswith('User: '):
                    messages.append({
                        "role": "user",
                        "message": line[6:].strip(),
                        "timestamp": datetime.utcnow().isoformat()
                    })
            return messages
        except Exception as e:
            logger.error(f"Error formatting messages: {str(e)}")
            return []

    def _create_error_response(self, error_message: str) -> Dict[str, Any]:
        """Create standardized error response."""
        return {
            "status": "error",
            "message": error_message,
            "timestamp": datetime.utcnow().isoformat(),
            "code": "ERROR",
            "details": {
                "error_type": "webhook_processing_error",
                "error_message": error_message
            }
        }

    def _create_success_response(self, message: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create standardized success response."""
        response = {
            "status": "success",
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
            "code": "SUCCESS"
        }
        if data:
            response["data"] = data
        return response

# Initialize webhook handler
webhook_handler = WebhookHandler()

# Route handlers
@app.route("/vapi-webhook", methods=["POST"])
@limiter.limit("100/minute")
async def vapi_webhook():
    """Handle incoming Vapi.ai webhooks."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON received"}), 400
            
        logger.info("📥 Incoming Webhook Data:", json.dumps(data, indent=4))
        
        response = await webhook_handler.handle_webhook(data)
        return jsonify(response), 200 if response["status"] == "success" else 400

    except Exception as e:
        logger.error(f"❌ Webhook Error: {str(e)}")
        logger.error(f"Stack trace: {traceback.format_exc()}")
        return jsonify({
            "status": "error",
            "message": "Webhook processing failed",
            "details": str(e)
        }), 500

@app.route("/sync-vapi-calllogs", methods=["GET"])
@limiter.limit("10/minute")
async def sync_vapi_calllogs():
    """Sync call logs from Vapi.ai."""
    try:
        headers = {
            "Authorization": f"Bearer {config.VAPI_API_KEY}",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.vapi.ai/call", 
                headers=headers, 
                timeout=30
            ) as response:
                if response.status != 200:
                    return jsonify({
                        "status": "error",
                        "error": "Failed to fetch call logs", 
                        "details": await response.text()
                    }), response.status

                call_logs = await response.json()

        if not call_logs:
            return jsonify({
                "status": "success",
                "message": "No new call logs found!"
            }), 200

        processed_count = 0
        total_logs = len(call_logs)
        errors = []

        for log in call_logs:
            try:
                # Process each call log
                response = await webhook_handler.handle_webhook({
                    "message": log
                })
                if response["status"] == "success":
                    processed_count += 1
                else:
                    errors.append({
                        "phone": log.get("customer", {}).get("number"),
                        "error": response.get("message")
                    })
            except Exception as e:
                logger.error(f"Error processing call log: {str(e)}")
                errors.append({
                    "phone": log.get("customer", {}).get("number"),
                    "error": str(e)
                })
                continue

        response_data = {
            "status": "success",
            "message": f"✅ Synced {processed_count} new call logs successfully!",
            "data": {
                "total_logs": total_logs,
                "processed": processed_count,
                "failed": total_logs - processed_count,
                "errors": errors if errors else None
            }
        }

        return jsonify(response_data), 200

    except Exception as e:
        logger.error(f"❌ Sync Error: {str(e)}")
        logger.error(f"Stack trace: {traceback.format_exc()}")
        return jsonify({
            "status": "error",
            "message": "Syncing call logs failed",
            "details": str(e)
        }), 500

@app.route("/", methods=["GET"])
def home():
    """Home route for health check."""
    return jsonify({
        "status": "success",
        "message": "Welcome to Nexa Backend! Your AI-powered networking assistant is live.",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat()
    }), 200

@app.route("/health", methods=["GET"])
async def health_check():
    """Health check endpoint with database connectivity test."""
    try:
        # Test database connection
        await db_conn.db.command("ping")
        
        # Check OpenAI API
        test_response = await openai_client.chat.completions.acreate(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "test"}],
            max_tokens=5
        )
        
        # Check Vapi.ai connection
        headers = {
            "Authorization": f"Bearer {config.VAPI_API_KEY}",
            "Content-Type": "application/json"
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.vapi.ai/health", 
                headers=headers,
                timeout=5
            ) as response:
                vapi_status = response.status == 200

        return jsonify({
            "status": "healthy",
            "database": "connected",
            "openai": "connected",
            "vapi": "connected" if vapi_status else "error",
            "timestamp": datetime.utcnow().isoformat()
        }), 200

    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return jsonify({
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }), 500

@app.errorhandler(429)
def ratelimit_handler(e):
    """Handle rate limit exceeded errors."""
    return jsonify({
        "status": "error",
        "message": "Rate limit exceeded. Please try again later.",
        "details": str(e),
        "timestamp": datetime.utcnow().isoformat()
    }), 429

@app.errorhandler(Exception)
def handle_exception(e):
    """Global exception handler."""
    logger.error(f"Unhandled exception: {str(e)}")
    logger.error(f"Stack trace: {traceback.format_exc()}")
    
    return jsonify({
        "status": "error",
        "message": "An unexpected error occurred",
        "details": str(e),
        "timestamp": datetime.utcnow().isoformat()
    }), 500

async def cleanup_expired_conversations():
    """Periodic task to cleanup expired conversations."""
    while True:
        try:
            current_time = datetime.utcnow()
            expired_conversations = [
                phone for phone, last_activity in conversation_manager.active_conversations.items()
                if (current_time - last_activity).seconds >= config.CONVERSATION_TIMEOUT
            ]
            
            for phone in expired_conversations:
                conversation_manager.end_conversation(phone)
                logger.info(f"Cleaned up expired conversation for {phone}")
                
            await asyncio.sleep(300)  # Run every 5 minutes
            
        except Exception as e:
            logger.error(f"Error in cleanup task: {str(e)}")
            await asyncio.sleep(60)  # Wait a minute before retrying

if __name__ == "__main__":
    # Add required imports for async operation
    import aiohttp
    from aiohttp import ClientTimeout
    import uvicorn
    
    # Start background tasks
    asyncio.create_task(cleanup_expired_conversations())
    
    # Run the application
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        workers=int(os.getenv("WEB_CONCURRENCY", 1))
    )
