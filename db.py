from pymongo import MongoClient

# Replace <connection_string> with your MongoDB connection string
client = MongoClient("<connection_string>")
db = client["nexa"]

users_collection = db["users"]
