from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler, ContextTypes, InvalidCallbackData, CallbackContext
from config import *
from telegram.constants import ParseMode
from telegram.error import NetworkError, TelegramError
from captcha_solver import solve_captcha
from datetime import datetime, timezone
from func import *
import asyncio
import logging
import aiohttp
import re
import json
import requests
import jwt

AUTHORIZED_USER_IDS = [5847781069, 5211092406]
MAX_OTP_ATTEMPTS = 3

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', mode='w'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def load_codes():
    try:
        with open('codes.json', "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_codes(codes):
    with open('codes.json', "w", encoding="utf-8") as file:
        json.dump(codes, file, indent=4)

codes = load_codes()
code_hold = {}

def log_message(update: Update, context: CallbackContext) -> None:
    user = update.message.from_user
    logger.info("Message from %s (%s) (@%s): %s", user.first_name, user.id, user.username, update.message.text)

def log_command(update: Update, context: CallbackContext, command_name: str) -> None:
    user = update.message.from_user
    logger.info("Command from %s (%s) (@%s): %s", user.first_name, user.id, user.username, update.message.text)

async def start(update: Update, context):
    log_command(update, context, 'start')
    welcome_message = """
Welcome to Buffed Credit Bot! Here are the available commands:
1. /login <email> - Login with your Sellpass email. You'll receive a 6-digit code.
2. /status - Check your account logged in status.
3. /stats - Check your shop stats.
4. /transfer <to_email> <amount> - Transfer balance to another email after logging in.
5. /redeem <code> - Redeem a code and add the specified amount to your balance.
    """

    welcome_message2= """
Welcome to Buffed Credit Bot! Here are the available commands:
1. /login <email> - Login with your Sellpass email. You'll receive a 6-digit code.
2. /status - Check your account logged in status.
3. /stats - Check your shop stats.
4. /transfer <to_email> <amount> - Transfer balance to another email after logging in.
5. /redeem <code> - Redeem a code and add the specified amount to your balance.
6. /info <email> - Get customer info, including balance, total spent, etc.
7. /manualgen <code> <amount> <duration> - Generate a manual code with amount and duration.
8. /gencode <amount> <duration> <quantity> - Generate bulk codes with amount and duration.
9. /reset <telegram_user_id> - Admin command to reset a user's login.
    """
    
    if update.message.from_user.id in AUTHORIZED_USER_IDS:
        await update.message.reply_text(welcome_message2)
    else:
        await update.message.reply_text(welcome_message)

async def stats(update: Update, context):
    user_id = update.message.from_user.id
    
    valid_accounts = load_user_data(user_id)

    if not valid_accounts:
        await update.message.reply_text("You're not logged in. Please use /login to log in.")
        return
    
    headers = {"Authorization": f"Bearer {valid_accounts[0]['token']}"}

    async with aiohttp.ClientSession() as session:
        async with session.get(f'https://api.sellpass.io/{SHOP_ID}/customers/dashboard/balance', headers=headers) as balance_response:
            if balance_response.status != 200:
                await update.message.reply_text("Failed to load user stats.")
                return

            balance_data = await balance_response.json()
            real_balance = balance_data.get("data", {}).get("balance", {}).get("realBalance", 0)
            manual_balance = balance_data.get("data", {}).get("balance", {}).get("manualBalance", 0)
            currency = balance_data.get("data", {}).get("balance", {}).get("currency", "USD")

        async with session.get(f'https://api.sellpass.io/{SHOP_ID}/customers/dashboard', headers=headers) as dashboard_response:
            if dashboard_response.status == 200:
                response_data = await dashboard_response.json()
                customer_since = response_data.get("data", {}).get("createdAt", "N/A")
                customer_rank = response_data.get("data", {}).get("topPosition", "N/A")
                amount_to_next_rank = response_data.get("data", {}).get("spendMoreForNextPosition", "N/A")
                favorite_product = response_data.get("data", {}).get("favoriteProduct", {}).get("product", {}).get("title", "N/A")
                total_spent = response_data.get("data", {}).get("totalSpent", "N/A")
                total_purchases = response_data.get("data", {}).get("totalPurchases", "N/A")

                if customer_since != "N/A":
                    customer_since = datetime.strptime(customer_since, "%Y-%m-%dT%H:%M:%S.%fZ").strftime("%Y-%m-%d %H:%M:%S")

                dashboard_text = (
                    f"📧 Email: {valid_accounts[0]['email']}\n\n"
                    f"💰 Balance: ${float(real_balance+manual_balance)} {currency}\n"
                    f"📆 Customer Since: {customer_since}\n"
                    f"🏆 Customer Rank: {customer_rank}\n"
                    f"📈 Amount to next rank: ${amount_to_next_rank}\n"
                    f"🛍️ Favorite Product: {favorite_product}\n"
                    f"💳 Total Spent: ${total_spent}\n"
                    f"🛒 Total Purchases: {total_purchases}\n"
                )

                await update.message.reply_text(dashboard_text)
            else:
                await update.message.reply_text("Failed to load dashboard data. Please try again.")

async def login(update: Update, context):
    user_id = update.message.from_user.id


    valid_accounts = load_user_data(user_id)

    if valid_accounts:
        await update.message.reply_text("You are already logged in as " + valid_accounts[0]['email'] + ". Please /logout first to log in again.")
        return
    context.user_data['state'] = 'waiting_for_email'
    context.user_data['otp_attempts'] = 0
    await update.message.reply_text(text="Please enter your email:")

async def message_handler(update: Update, context):
    state = context.user_data.get('state')

    if state == 'waiting_for_email':
        log_command(update, context, 'login')
        email = update.message.text
        if re.match(r"[^@]+@[^@]+\.[^@]+", email):
            await update.message.reply_text("Email validated. Solving captcha and requesting OTP...")

            context.user_data['state'] = 'waiting_for_captcha'
            context.user_data['email'] = email

            captcha_task = asyncio.create_task(handle_captcha_solution(update, context, email))
        else:
            await update.message.reply_text("Invalid email. Please try again.")
    elif state == 'waiting_for_otp':
        log_command(update, context, 'otp')
        otp = update.message.text

        if context.user_data['otp_attempts'] > MAX_OTP_ATTEMPTS:
            await update.message.reply_text(f"Maximum OTP attempts exceeded. Please try again.")
            context.user_data['state'] = None
            return

        if len(otp) == 6 and otp.isdigit():
            context.user_data['otp_attempts'] += 1
            await update.message.reply_text("Validating OTP...")

            email = context.user_data.get('email')
            recaptcha_token = await solve_captcha()  # Re-solve captcha for verification

            if recaptcha_token:
                otp_verification_status, expiry_time = verify_otp(email, otp, recaptcha_token, update)

                if otp_verification_status:
                    context.user_data['state'] = None
                    await update.message.reply_text(
                        f"You have successfully logged in! Your session expires at {expiry_time} UTC.")
                else:
                    await update.message.reply_text(f"Invalid OTP. {MAX_OTP_ATTEMPTS - context.user_data['otp_attempts']} attempts left.")
            else:
                await update.message.reply_text("Captcha solving failed for OTP verification.")
        else:
            await update.message.reply_text(f"Invalid OTP format. {MAX_OTP_ATTEMPTS - context.user_data['otp_attempts']} attempts left.")
    elif state == 'waiting_for_email_code':
        email = update.message.text
        code = context.user_data.get('redeeming_code')
        user_id = update.message.from_user.id

        context.user_data['state'] = None
        if not get_customer_id_by_email(email):
            await update.message.reply_text("Invalid email. Please provide a valid Sellpass email:")
            context.user_data['state'] = 'waiting_for_email_code'
            return
        
        code_data = codes.pop(code)
        save_codes(codes)
        amount = code_data["amount"]

        msg, response_code = add_balance_to_user_by_email(email, amount)
        
        if response_code == 200:
            with open("redeemed_codes.txt", "a") as file:
                file.write(f"{code}: Redeemed by user {user_id}, Email={email}, Amount=${amount}\n")
            
            await update.message.reply_text(f"Code {code} redeemed successfully. ${amount} added to your balance.")
            logger.info(f"Code {code} redeemed successfully by user {user_id}")
        else:
            await update.message.reply_text(f"Failed to redeem code. Error: {msg}")
            logger.error(f"Failed to redeem code {code} by user {user_id}: {msg}")

async def handle_captcha_solution(update: Update, context, email):
    recaptcha_token = await solve_captcha()

    if recaptcha_token:
        otp_request_status = send_otp_request(email, recaptcha_token)

        if otp_request_status:
            await update.message.reply_text("OTP sent to your email. Please enter the 6-digit OTP:")
            context.user_data['state'] = 'waiting_for_otp'
        else:
            await update.message.reply_text("Failed to send OTP. Please try again.")
            context.user_data['state'] = 'waiting_for_email'  # Reset state to wait for a new email
    else:
        await update.message.reply_text("Captcha solving failed.")
        context.user_data['state'] = 'waiting_for_email'  # Reset state to wait for a new email

def send_otp_request(email, recaptcha_token):
    postdata = {
        "email": email,
        "recaptcha": recaptcha_token,
        "referralCode": None
    }
    url = f"https://api.sellpass.io/{SHOP_ID}/customers/auth/otp/request/"

    try:
        response = requests.post(url, json=postdata)
        if response.status_code == 200:
            return True
        else:
            print(f"OTP Request failed: {response.text}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"Error in OTP request: {e}")
        return False

def verify_otp(email, otp, recaptcha_token, update):
    postdata = {
        "email": email,
        "otp": otp,
        "recaptcha": recaptcha_token,
        "referralCode": None,
        "tsId": None
    }
    url = f"https://api.sellpass.io/{SHOP_ID}/customers/auth/otp/login/"

    try:
        response = requests.post(url, json=postdata)
        if response.status_code == 200:
            data = response.json()
            token = data["data"]
            expiry = jwt.decode(token, options={"verify_signature": False})["exp"]
            expiry_date = datetime.fromtimestamp(expiry)
            expiry_time = datetime.fromtimestamp(expiry, tz=timezone.utc)

            # Save user data
            save_user_data(email, update.effective_user.id, token, expiry_date, expiry)

            return True, expiry_time
        else:
            print(f"OTP Verification failed: {response.text}")
            return False, None
    except requests.exceptions.RequestException as e:
        print(f"Error in OTP verification: {e}")
        return False, None
    
def save_user_data(email, user_id, token, expiry, expiry_raw):
    user_info = {
        "email": email,
        "user_id": user_id,
        "token": token,
        "expiry": expiry.strftime("%Y-%m-%d %H:%M:%S"),
        "expiry_raw": expiry_raw
    }
    with open("user_data.txt", "a") as file:
        file.write(json.dumps(user_info) + "\n")

def load_user_data(user_id):
    valid_accounts = []
    try:
        with open("user_data.txt", "r") as file:
            user_data = [json.loads(line) for line in file.readlines()]
            for data in user_data:
                if data["user_id"] == user_id:
                    expiry_time = datetime.fromtimestamp(data["expiry_raw"], tz=timezone.utc)
                    if expiry_time > datetime.now(timezone.utc):
                        valid_accounts.append(data)
                    else:
                        print(f"Token expired for {data['email']}, removing.")
    except FileNotFoundError:
        pass
    return valid_accounts

def logout(user_id):
    try:
        with open("user_data.txt", "r") as file:
            user_data = [json.loads(line) for line in file.readlines()]
        
        with open("user_data.txt", "w") as file:
            for data in user_data:
                if data["user_id"] != user_id:
                    file.write(json.dumps(data) + "\n")
    except FileNotFoundError:
        pass

async def logout(update: Update, context: CallbackContext) -> None:
    log_command(update, context, 'logout')

    user_id = update.message.from_user.id
    
    valid_accounts = load_user_data(user_id)

    if not valid_accounts:
        await update.message.reply_text("You're not logged in. Please use /login to log in.")
        return
    
    try:
        with open("user_data.txt", "r") as file:
            user_data = [json.loads(line) for line in file.readlines()]
        
        with open("user_data.txt", "w") as file:
            for data in user_data:
                if data["user_id"] != update.effective_user.id:
                    file.write(json.dumps(data) + "\n")

        await update.effective_message.reply_text("Logged out successfully!")
    except FileNotFoundError:
        pass

async def reset(update: Update, context: CallbackContext) -> None:
    log_command(update, context, 'reset')
    
    user_id = update.message.from_user.id
    
    if user_id in AUTHORIZED_USER_IDS:
        args = context.args
        if len(args) != 1:
            await update.message.reply_text("Usage: /reset <telegram_user_id>")
            return

        target_user_id = args[0]
        valid_accounts = load_user_data(target_user_id)

        if not valid_accounts:
            await update.message.reply_text("No active session found for user {target_user_id}.")
            return
        
        try:
            with open("user_data.txt", "r") as file:
                user_data = [json.loads(line) for line in file.readlines()]
            
            with open("user_data.txt", "w") as file:
                for data in user_data:
                    if data["user_id"] != update.effective_user.id:
                        file.write(json.dumps(data) + "\n")

            await update.message.reply_text(f"User {target_user_id}'s email login has been reset.")
            logger.info(f"Admin {user_id} reset login for user {target_user_id}")
        except FileNotFoundError:
            pass

def remove_expired_tokens():
    try:
        while True:
            with open("user_data.txt", "r") as file:
                user_data = [json.loads(line) for line in file.readlines()]
            with open("user_data.txt", "w") as file:
                for data in user_data:
                    expiry_time = datetime.fromtimestamp(data["expiry_raw"], tz=timezone.utc)
                    if expiry_time > datetime.now(timezone.utc):
                        file.write(json.dumps(data) + "\n")
    except FileNotFoundError:
        pass

async def cancel(update: Update, context):
    log_command(update, context, 'cancel')
    context.user_data['state'] = None
    await update.effective_message.reply_text("Cancelled!")

async def clear(update: Update, context):
    log_command(update, context, 'clear')
    context.user_data['state'] = None
    await update.effective_message.reply_text("Cleared!")

async def status(update: Update, context: CallbackContext) -> None:
    log_command(update, context, 'status')
    user_id = update.message.from_user.id
    
    valid_accounts = load_user_data(user_id)

    if not valid_accounts:
        await update.message.reply_text("You're not logged in. Please use /login to log in.")
        return
    
    await update.message.reply_text(f"Logged in as: {valid_accounts[0]['email']}, your session expires in {valid_accounts[0]['expiry']}")

async def transfer(update: Update, context: CallbackContext) -> None:
    log_command(update, context, 'transfer')
    user_id = update.message.from_user.id
    
    valid_accounts = load_user_data(user_id)

    if not valid_accounts:
        await update.message.reply_text("You're not logged in. Please use /login to log in.")
        return

    elif len(valid_accounts) != 1:
        await update.message.reply_text("Multiple logged in accounts found. Please use /logout and log in again.")
        return
    
    try:
        args = context.args
        if len(args) != 2:
            await update.message.reply_text("Usage: /transfer <to_email> <amount>")
            return

        to_email = args[0]
        amount = float(args[1])
        from_email = valid_accounts[0]['email']

        if from_email == to_email:
            await update.message.reply_text("You cannot transfer balance to yourself.")
            return
        
        sender_customer_data = get_customer_data_by_email(from_email)
        if not sender_customer_data:
            await update.message.reply_text(f"No customer found with email: {from_email}")
            return
        
        receiver_customer_data = get_customer_data_by_email(to_email)
        if not receiver_customer_data:
            await update.message.reply_text(f"No customer found with email: {to_email}")
            return

        balances = sender_customer_data.get("customerForShopAccount", {}).get("balances", [{}])[0]
        real_balance = balances.get("realBalance", 0)
        manual_balance = balances.get("manualBalance", 0)
        balance = real_balance + manual_balance
        
        if amount > float(balance):
            await update.message.reply_text(f"Insufficient balance to transfer ${amount} to {to_email}.\nCurrent balance in account {from_email}: ${float(balance)}")
            return
        
        msgg, statuss = remove_balance_to_user_by_email(from_email, amount)

        if statuss != 200:
            print(f'An error occurred while transfering from {from_email} to {to_email}: {msgg}')
            await update.message.reply_text(f"Failed to transfer balance.")
            return
        
        msg, status = add_balance_to_user_by_email(to_email, amount)

        if status == 200:
            with open("transfers.txt", "a") as file:
                file.write(f"{datetime.now(timezone.utc)} | {from_email} -> {to_email}: ${amount}\n")
            await update.message.reply_text(f"Successfully transferred ${amount} to {to_email}.")
            logger.info(f"Successfully transferred ${amount} from {from_email} to {to_email}")
        else:
            await update.message.reply_text(f"Failed to transfer balance. Error: {msg}")
    except ValueError as e:
        await update.message.reply_text("Please provide a valid amount.")
        logger.error(f"Error in /transfer command: {e}")

async def info_command(update: Update, context: CallbackContext) -> None:
    if update.message.from_user.id in AUTHORIZED_USER_IDS:
        try:
            args = context.args
            if len(args) != 1:
                await update.message.reply_text("Usage: /info <email>")
                return

            email = args[0]
            customer_data = get_customer_data_by_email(email)
            if not customer_data:
                await update.message.reply_text(f"No customer found with email: {email}")
                return

            customer_id = customer_data.get("id")
            total_spent = customer_data.get("totalSpent", 0)
            total_purchases = customer_data.get("totalPurchases", 0)
            is_blocked = customer_data.get("isBlocked", False)
            balances = customer_data.get("customerForShopAccount", {}).get("balances", [{}])[0]
            real_balance = balances.get("realBalance", 0)
            manual_balance = balances.get("manualBalance", 0)

            info_message = f"""
🆔 Customer ID: {customer_id}
💵 Total Spent: ${total_spent}
🛒 Total Purchases: {total_purchases}
🚫 Blocked: {'Yes' if is_blocked else 'No'}
💰 Real Balance: ${real_balance}
💳 Manual Balance: ${manual_balance}
            """
            await update.message.reply_text(info_message)
            logger.info(f"Displayed info for customer {customer_id}")
        except Exception as e:
            await update.message.reply_text(f"An error occurred: {e}")
            logger.error(f"Error in /info command: {e}")

async def manualgen_command(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    
    if user_id in AUTHORIZED_USER_IDS:
        try:
            args = context.args
            if len(args) != 3:
                await update.message.reply_text("Usage: /manualgen <code> <amount> <duration>")
                return
            
            code = args[0]
            amount = float(args[1])
            duration = int(args[2])

            codes[code] = {"amount": amount, "duration": duration}
            save_codes(codes)

            await update.message.reply_text(f"Manual code {code} generated with amount ${amount} and duration {duration} days.")
            logger.info(f"Manual code {code} generated with amount ${amount} and duration {duration}")
        except ValueError as e:
            await update.message.reply_text("Please provide valid values for code, amount, and duration.")
            logger.error(f"Error in /manualgen command: {e}")

async def gencode_command(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    
    if user_id in AUTHORIZED_USER_IDS:
        try:
            args = context.args
            if len(args) != 3:
                await update.message.reply_text("Usage: /gencode <amount> <duration> <quantity>")
                return

            amount = float(args[0])
            duration = int(args[1])
            quantity = int(args[2])

            generated_codes = []
            for i in range(quantity):
                code = generate_random_code()
                generated_codes.append(code)
                codes[code] = {"amount": amount, "duration": duration}
                save_codes(codes)

            codes_message = "\n".join(generated_codes)
            await update.message.reply_text(codes_message)
            logger.info(f"Generated {quantity} bulk codes with amount {amount} and duration {duration}")
        except ValueError as e:
            await update.message.reply_text("Please provide valid values for amount, duration, and quantity.")
            logger.error(f"Error in /gencode command: {e}")

async def redeem_command(update: Update, context: CallbackContext) -> None:
    try:
        args = context.args
        if len(args) != 1:
            await update.message.reply_text("Usage: /redeem <code>")
            return
        
        code = args[0]
        
        if code not in codes:
            await update.message.reply_text("SAAR DON'T REDEEM THE FUCKING CODE!! DO NOT REDEEM IT SAAR!!! (the code ur trying to redeem is invalid ❌)")
            return
        
        await update.message.reply_text(f"Please provide your Sellpass email to redeem code {code}:")
        context.user_data['redeeming_code'] = code
        context.user_data['state'] = 'waiting_for_email_code'
        logger.info(f"Redeem command initiated for code {code}")
    except Exception as e:
        await update.message.reply_text(f"An error occurred: {e}")
        logger.error(f"Error in /redeem command: {e}")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', start))
    app.add_handler(CommandHandler('login', login))
    app.add_handler(CommandHandler('stats', stats))
    app.add_handler(CommandHandler('status', status))
    app.add_handler(CommandHandler('logout', logout))
    app.add_handler(CommandHandler('clear', clear))
    app.add_handler(CommandHandler('cancel', cancel))
    app.add_handler(CommandHandler('transfer', transfer))
    app.add_handler(CommandHandler('info', info_command))
    app.add_handler(CommandHandler('gencode', gencode_command))
    app.add_handler(CommandHandler('manualgen', manualgen_command))
    app.add_handler(CommandHandler('redeem', redeem_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    try:
        app.run_polling()
        remove_expired_tokens()
        print('Bot is now Online!')
    except NetworkError:
        print("Network error occurred. Retrying...")
    except TelegramError as e:
        print(f"Telegram error occurred: {e}")

if __name__ == '__main__':
    main()