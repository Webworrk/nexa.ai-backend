from flask import Flask, request, jsonify
from pymongo import MongoClient

app = Flask(__name__)

# MongoDB Configuration
client = MongoClient("mongodb+srv://webworrkteam:Ranjith%40003@cluster0.yr247.mongodb.net/Nexa")
db = client["Nexa"]  # Database name
users_collection = db["Users"]  # Collection for users

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Welcome to Nexa Backend!"}), 200

# Endpoint to register a new user
@app.route("/register", methods=["POST"])
def register_user():
    data = request.json
    name = data.get("name")
    email = data.get("email")
    phone = data.get("phone")

    if not all([name, email, phone]):
        return jsonify({"error": "All fields (name, email, phone) are required!"}), 400

    user = {
        "Name": name,
        "Email": email,
        "Phone": phone,
        "nexa_id": f"NEXA{users_collection.count_documents({}) + 1:04d}",
        "Signup Status": "Completed",
        "Calls": []
    }

    users_collection.insert_one(user)
    return jsonify({"message": "User registered successfully!", "nexa_id": user["nexa_id"]}), 201

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
