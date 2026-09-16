import json
import os
from datetime import datetime
from firebase_admin import credentials, firestore
import firebase_admin
from flask import Flask, jsonify
import requests

app = Flask(__name__)

# Firebase Baglantisi (Bulut ve Yerel uyumlu)
if os.environ.get("FIREBASE_KEY"):
    cred_json = json.loads(os.environ.get("FIREBASE_KEY"))
    cred = credentials.Certificate(cred_json)
else:
    cred = credentials.Certificate("firebase_key.json")

firebase_admin.initialize_app(cred)
db = firestore.client()