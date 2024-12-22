from config import *
from main import logger
from datetime import datetime, timezone
import requests
import json
import string
import random

def get_customer_id_by_email(email):
    url = f"https://dev.sellpass.io/self/{SHOP_ID}/customers?email={email}"
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json'
    }
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            customers = data.get('data', [])
            for customer in customers:
                if customer['customer']['email'] == email:
                    return customer.get("id")
        return None
    except requests.RequestException as e:
        logger.error(f"Error fetching customer ID: {e}")
        return None

def get_customer_data_by_email(email):
    url = f"https://dev.sellpass.io/self/{SHOP_ID}/customers?email={email}"
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json'
    }
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            customers = data.get('data', [])
            for customer in customers:
                if customer['customer']['email'] == email:
                    return customer
        return None
    except requests.RequestException as e:
        logger.error(f"Error fetching customer info: {e}")
        return None
    
def add_balance_to_user(customer_id, amount):
    url = f'https://dev.sellpass.io/self/{SHOP_ID}/customers/{customer_id}/balance/add'
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json'
    }
    payload = {"amount": amount}
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            return f"Added ${amount} to customer ID {customer_id}.", response.status_code
        else:
            return response.json().get('errors', [response.text])[0], response.status_code
    except requests.RequestException as e:
        logger.error(f"Error adding balance: {e}")
        return str(e), None

def remove_balance_to_user(customer_id, amount):
    url = f'https://dev.sellpass.io/self/{SHOP_ID}/customers/{customer_id}/balance/remove'
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json'
    }
    payload = {"amount": amount}
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            return f"Removed ${amount} to customer ID {customer_id}.", response.status_code
        else:
            return response.json().get('errors', [response.text])[0], response.status_code
    except requests.RequestException as e:
        logger.error(f"Error adding balance: {e}")
        return str(e), None
    
def add_balance_to_user_by_email(email, amount):
    customer_id = get_customer_id_by_email(email)
    if customer_id:
        return add_balance_to_user(customer_id, amount)
    return f"Customer with email {email} not found.", None

def remove_balance_to_user_by_email(email, amount):
    customer_id = get_customer_id_by_email(email)
    if customer_id:
        return remove_balance_to_user(customer_id, amount)
    return f"Customer with email {email} not found.", None

def generate_random_code():
    return 'BUFF-' + ''.join(random.choice(
                        string.ascii_letters.upper() + string.ascii_letters.lower() + string.digits) for _ in range(18))