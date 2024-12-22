import os
import time
import json
import tkinter as tk
from tkinter import filedialog
from colorama import init, Fore, Back, Style
import requests
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest
import asyncio
from datetime import datetime, timedelta
import aiohttp
import ssl
import certifi
import re
import logging

init(autoreset=True)

BASE_URL = "https://dev.sellpass.io"
ORDER_FILE = "preorderbot/preorders.json"

logging.basicConfig(
    filename='deliveries.log',
    filemode='a',
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger()

def get_flag_emoji(country_code):
    if country_code.upper() == 'ASIA':
        return '[ASIA]'
    elif country_code.upper() == 'MIXED':
        return '[MIXED]'
    else:
        return f'[{country_code.upper()}]'

def clear_console():
    os.system('cls' if os.name == 'nt' else 'clear')

def select_folder():
    root = tk.Tk()
    root.withdraw()
    folder_path = filedialog.askdirectory()
    return folder_path

def get_stock_interval():
    while True:
        try:
            interval = int(input(f"{Fore.YELLOW}Enter the number of lines after which to stock: "))
            return interval
        except ValueError:
            print(f"{Fore.RED}Please enter a valid number.")

def count_lines(file_path):
    try:
        with open(file_path, 'r', encoding='UTF-8') as file:
            return sum(1 for _ in file)
    except Exception as e:
        print(f"Error counting lines in {file_path}: {e}")
        return 0

def extract_country(hit):
    try:
        match = re.search(r'Country:\s*\[?([A-Z]{2,3})\]?', hit, re.IGNORECASE)
        if match:
            country = match.group(1).strip().upper()
            
            if country in ['C2', 'JP', 'KR', 'TW', 'HK', 'SG']:
                return 'ASIA'
            if country in ['US', 'DE', 'GB', 'CA', 'AU', 'IT', 'ES', 'IL', 'NL', 'GR', 'CH', 'AT', 'FR', 'BR', 'SE']:
                return country
            else:
                return 'MIXED'
        else:
            return 'MIXED'
    except (IndexError, AttributeError):
        return 'MIXED'

def sort_and_stock(hits):
    sorted_hits = sorted(hits, key=extract_country)
    
    grouped_hits = {
        'US': [], 'DE': [], 'GB': [], 'CA': [], 'AU': [], 'IT': [],
        'ASIA': [], 'ES': [], 'IL': [], 'NL': [], 'GR': [],
        'CH': [], 'AT': [], 'FR': [], 'BR': [], 'MIXED': [], 
        'SE': []
    }
    
    for hit in sorted_hits:
        country = extract_country(hit)
        if country not in grouped_hits:
            country = 'MIXED'
        grouped_hits[country].append(hit)
    
    print(f"{Fore.YELLOW}Total hits to be stocked: {len(hits)}")
    print(f"{Fore.YELLOW}Grouped hits count: {sum(len(v) for v in grouped_hits.values())}")
    
    parsed_countries = parse_countries_from_logs(hits)
    return grouped_hits, parsed_countries

def parse_countries_from_logs(hits):
    countries = set()
    for hit in hits:
        try:
            match = re.search(r'Country:\s*\[?([A-Z]{2,3})\]?', hit, re.IGNORECASE)
            if match:
                country = match.group(1).strip().upper()
                if country in ['C2', 'JP', 'KR', 'TW', 'HK', 'SG']:
                    countries.add('ASIA')
                elif country in ['US', 'DE', 'GB', 'CA', 'AU', 'IT', 'ES', 'IL', 'NL', 'GR', 'CH', 'AT', 'FR', 'BR', 'SE']:
                    countries.add(country)
                else:
                    countries.add('MIXED')
            else:
                countries.add('MIXED')
        except (IndexError, AttributeError):
            countries.add('MIXED')
    return countries

def save_orders_to_file(orders):
    with open(ORDER_FILE, 'w') as file:
        json.dump(orders, file, indent=4)
    logger.info(f"Orders updated and saved successfully to {ORDER_FILE}.")

async def update_channel(order, message):
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        bot = Bot(token=config['telegram_bot_token2'])
        await bot.send_message(chat_id=config['telegram_channel_id2'], text=message)
    except Exception as e:
        print(f"Failed to update channel. Error: {e}")

async def send_serials_to_user(order, filename):
    try:
        user_id = order['user_id']
        with open('config.json', 'r') as f:
            config = json.load(f)
        bot = Bot(token=config['telegram_bot_token2'])
        await bot.send_document(chat_id=user_id, document=open(filename, 'rb'),
                          caption=f"Your order for {order['variant_title']} has been delivered!\nInvoice ID: {order['invoice_id']}")
        logger.info(f"Successfully delivered {filename} to user {user_id}.")
    except Exception as e:
        print(f"Failed to send file to user {order['user_id']}. Error: {e}")
        
def create_serial_file(order, serials):
    filename = f"delivery/{order['invoice_id']}.txt"
    with open(filename, 'w', encoding='utf-8') as file:
        for serial in serials:
            file.write(f"{serial}")
    return filename

async def stock_product(shop_id, product_id, grouped_hits, api_key):
    get_url = f"{BASE_URL}/self/{shop_id}/v2/products/{product_id}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.get(get_url, headers=headers)
        response.raise_for_status()
        product_data = response.json()['data']
    except requests.RequestException as e:
        print(f"Failed to fetch product {product_id}. Error: {e}")
        return 0

    if 'product' not in product_data or 'variants' not in product_data['product']:
        print(f"Error: Invalid product data structure for product {product_id}")
        return 0

    variants = product_data['product']['variants']

    if not variants:
        print(f"Error: No variants found for product {product_id}")
        return 0

    total_stocked = 0

    if not os.path.exists(ORDER_FILE):
        print(f"Order file {ORDER_FILE} not found.")
        return

    with open(ORDER_FILE, 'r') as file:
        all_orders = json.load(file)
        pending_orders = [order for order in all_orders if not order['delivered']]

    for order in pending_orders:
        order_variant_id = int(order['variant_id'])

        variant = next((v for v in variants if v['id'] == order_variant_id), None)
        if not variant:
            print(f"Variant ID {order_variant_id} not found for order {order['invoice_id']}.")
            continue

        variant_country_match = re.search(r'\[(.*?)\]|\((.*?)\)', variant['title'])
        if variant_country_match:
            variant_country = variant_country_match.group(1) if variant_country_match.group(1) else variant_country_match.group(2)
            variant_country = variant_country.upper()
        else:
            variant_country = 'MIXED'
        
        if variant_country not in grouped_hits:
            print(f"Country code {variant_country} not found in grouped hits for order {order['invoice_id']}.")
            continue

        if len(grouped_hits[variant_country]) < order['quantity']:
            print(f"Not enough serials in {variant_country} for order {order['invoice_id']}. Needed: {order['quantity']}, Available: {len(grouped_hits[variant_country])}. Skipping delivery")
            logger.info(f"Not enough serials in {variant_country} for order {order['invoice_id']}. Needed: {order['quantity']}, Available: {len(grouped_hits[variant_country])}. Skipping delivery")
            continue

        selected_serials = grouped_hits[variant_country][:order['quantity']]
        grouped_hits[variant_country] = grouped_hits[variant_country][order['quantity']:]

        serial_filename = create_serial_file(order, selected_serials)

        await send_serials_to_user(order, serial_filename)

        order['delivered'] = True
        await update_channel(order, f"📦 Delivered x{order['quantity']} 🛍️ {order['variant_title']} to user 👤 @{order['username']}! 🎉")
        print(f"Delivered {order['quantity']} serials to user {order['user_id']} for order {order['invoice_id']}.")
        logger.info(f"Delivered {order['quantity']} serials to user {order['user_id']} for order {order['invoice_id']}.")

    save_orders_to_file(all_orders)
    
    for variant in variants:
        variant_title = variant.get('title', '').upper()
        matching_country = next((country for country in grouped_hits.keys() if country.upper() in variant_title), None)
        if matching_country:
            country_hits = grouped_hits[matching_country]
        elif 'MIXED' in variant_title:
            country_hits = grouped_hits['MIXED']
        else:
            country_hits = []
        
        if country_hits:
            if variant.get('productType') == 0 and 'asSerials' in variant:
                existing_stock = variant['asSerials'].get('stock', 0)
                new_stock = len(country_hits)
                variant['asSerials']['stock'] = existing_stock + new_stock
                
                existing_serials = variant['asSerials'].get('serials', '')
                delimiter = variant['asSerials'].get('delimiter', '\n')
                
                if country_hits and isinstance(country_hits[0], list):
                    new_serials = delimiter.join([hit[0] for hit in country_hits if hit])
                else:
                    new_serials = delimiter.join([hit for hit in country_hits if hit])
                
                if existing_serials:
                    if isinstance(existing_serials, list):
                        existing_serials = delimiter.join(existing_serials)
                    variant['asSerials']['serials'] = existing_serials + delimiter + new_serials
                else:
                    variant['asSerials']['serials'] = new_serials
                
                print(f"Updated stock for {variant_title} variant: Added {len(country_hits)} hits, Total: {variant['asSerials']['stock']}")
                total_stocked += len(country_hits)
                if 'mixed' in variant_title.lower():
                    grouped_hits['MIXED'] = []
                elif matching_country:
                    grouped_hits[matching_country] = [] 
        else:
            if variant.get('productType') == 0 and 'asSerials' in variant:
                existing_stock = variant['asSerials'].get('stock', 0)
                
                variant['asSerials']['stock'] = existing_stock

                existing_serials = variant['asSerials'].get('serials', '')
                delimiter = variant['asSerials'].get('delimiter', '\n')
                if existing_serials:
                    if isinstance(existing_serials, list):
                        existing_serials = delimiter.join(existing_serials)
                    variant['asSerials']['serials'] = existing_serials + delimiter
                else:
                    variant['asSerials']['serials'] = ''

    update_url = f"{BASE_URL}/self/{shop_id}/v2/products/{product_id}"
    update_data = {
        "id": product_data.get('id'),
        "path": product_data.get('path'),
        "searchWordsMeta": product_data.get('searchWordsMeta'),
        "position": product_data.get('position'),
        "minPrice": product_data.get('minPrice'),
        "main": product_data['product'].get('main', {}),
        "seo": product_data['product'].get('seo', {}),
        "visibility": product_data['product'].get('visibility'),
        "type": product_data['product'].get('type'),
        "title": product_data['product'].get('title'),
        "description": product_data['product'].get('description'),
        "shortDescription": product_data['product'].get('shortDescription'),
        "thumbnailCfImageId": product_data['product'].get('thumbnailCfImageId'),
        "onHold": product_data['product'].get('onHold'),
        "terms": product_data['product'].get('terms'),
        "variants": variants
    }

    try:
        response = requests.put(update_url, headers=headers, json=update_data)
        response.raise_for_status()
        print(f"Successfully updated stock for product {product_id}")
    except requests.RequestException as e:
        print(f"Failed to update stock for product {product_id}. Error: {e}")

    return total_stocked

async def get_current_stock(config):
    get_url = f"{BASE_URL}/self/{config['shop_id']}/v2/products/{config['product_id']}"
    headers = {
        "Authorization": f"Bearer {config['sellpass_api_key']}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.get(get_url, headers=headers)
        response.raise_for_status()
        data = response.json()
        if 'data' in data and 'product' in data['data'] and 'variants' in data['data']['product']:
            variants = data['data']['product']['variants']
            total_stock = sum(variant['asSerials']['stock'] for variant in variants if 'asSerials' in variant)
            return total_stock
        else:
            print("Unexpected response structure")
            return 0
    except requests.RequestException as e:
        print(f"Error fetching current stock: {e}")
        return 0
    except (KeyError, TypeError) as e:
        print(f"Error parsing response: {e}")
        return 0

async def get_stocked_regions(config):
    get_url = f"{BASE_URL}/self/{config['shop_id']}/v2/products/{config['product_id']}"
    headers = {
        "Authorization": f"Bearer {config['sellpass_api_key']}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.get(get_url, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        if 'data' in data and 'product' in data['data'] and 'variants' in data['data']['product']:
            variants = data['data']['product']['variants']
            stocked_regions = [variant['title'].split()[0] for variant in variants if variant.get('asSerials', {}).get('stock', 0) > 0]
            return ' '.join(get_flag_emoji(region) for region in stocked_regions) if stocked_regions else get_flag_emoji('Mixed')
        else:
            return get_flag_emoji('Mixed')
    except Exception as e:
        print(f"Error fetching stocked regions: {e}")
        return get_flag_emoji('Mixed')

async def calculate_average_price(config):
    get_url = f"{BASE_URL}/self/{config['shop_id']}/v2/products/{config['product_id']}"
    headers = {
        "Authorization": f"Bearer {config['sellpass_api_key']}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.get(get_url, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        if 'data' in data and 'product' in data['data'] and 'variants' in data['data']['product']:
            variants = data['data']['product']['variants']
            total_price = 0
            total_stock = 0
            for variant in variants:
                if 'asSerials' in variant and 'stock' in variant['asSerials']:
                    stock = variant['asSerials']['stock']
                    price = variant.get('priceDetails', {}).get('amount')
                    if price is not None:
                        total_price += price * stock
                        total_stock += stock
            if total_stock > 0:
                average_price = total_price / total_stock
                return average_price
            else:
                return 0
        else:
            print("Unexpected response structure")
            return 0
    except Exception as e:
        print(f"Error calculating average price: {e}")
        return 0

async def update_sold_out_message(config, last_message_id, folder_path, stock_interval):
    bot = Bot(token=config['telegram_bot_token'])
    
    hits_file = os.path.join(folder_path, 'Hits.txt')
    current_lines = count_lines(hits_file)
    logs_left = max(0, stock_interval - current_lines)
    
    message = f"""
Sold Out! Waiting for next stock...

Logs until next stock: {logs_left}

Last updated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} 🕒
"""
    try:
        await bot.edit_message_text(
            chat_id=config['telegram_channel_id'],
            message_id=last_message_id,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
    except BadRequest as e:
        print(f"{Fore.RED}Failed to update sold out message: {e}")

async def update_telegram_notification(config, last_message_id, intial_stock, stocked_amount, folder_path, stock_interval, parsed_countries):
    global status
    status = "RUNNING"
    bot = Bot(token=config['telegram_bot_token'])
    
    with open('config.json', 'r') as f:
        config = json.load(f)

    while True:
        try:
            hits_file = os.path.join(folder_path, 'Hits.txt')
            current_lines = count_lines(hits_file)
            logs_left = max(0, stock_interval - current_lines)
            
            current_stock = await get_current_stock(config)
            if current_stock == 0:
                status = "SOLD OUT"
                await update_sold_out_message(config, last_message_id, folder_path, stock_interval)
            else:
                regions = ' '.join(get_flag_emoji(country) for country in parsed_countries)
                average_price = await calculate_average_price(config)

                message = f"""
Product: PayPal 🛒
Status: {status} 📊
Region : {regions} 🌍
Stock Added: {stocked_amount} ➕
Initial Stock: {intial_stock} 📦
Current Stock: {current_stock} (Auto Updated Every Minute ➰)
Logs until next stock: {logs_left}
Average Price: ${average_price:.2f} 💵
Minimum: 10 
Maximum: ♾ 

<a href="https://buffed.fo/products/Paypal-NFA--US--PM-">Purchase now! 🛍️</a>

Last updated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} 🕒
"""

                try:
                    await bot.edit_message_text(
                        chat_id=config['telegram_channel_id'],
                        message_id=last_message_id,
                        text=message,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )
                    print(f"{Fore.GREEN}Telegram notification updated")
                except BadRequest as e:
                    print(f"{Fore.RED}Failed to update Telegram message: {e}")
                    status = f"ERROR: Telegram update failed - {str(e)}"

            await asyncio.sleep(60)
        except Exception as e:
            print(f"Error in update_telegram_notification: {e}")
            status = f"ERROR: {type(e).__name__} - {str(e)}"
            await asyncio.sleep(60)

async def fetch_dashboard_data(config, ssl_context=None):
    shop_id = config['shop_id']
    api_key = config['sellpass_api_key']
    
    url = f"https://dev.sellpass.io/self/{shop_id}/dashboard/advanced"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers, ssl=ssl_context) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    print(f"Error fetching dashboard data: {response.status}")
                    print(f"Response text: {await response.text()}")
                    return None
        except aiohttp.ClientError as e:
            print(f"Error connecting to Sellpass API: {e}")
            return None

def calculate_revenue_and_count_per_gateway(dashboard_data, gateway_mapping):
    if not dashboard_data or 'data' not in dashboard_data:
        return {}

    data = dashboard_data['data']
    gateway_revenue_count = {}

    for gateway in data.get('topGateways', []):
        gateway_id = gateway['key']['gatewayName']
        gateway_name = gateway_mapping.get(gateway_id, "Unknown")
        count = gateway['count']
        revenue = gateway['revenue']

        if gateway_name in gateway_revenue_count:
            gateway_revenue_count[gateway_name]['count'] += count
            gateway_revenue_count[gateway_name]['revenue'] += revenue
        else:
            gateway_revenue_count[gateway_name] = {
                'count': count,
                'revenue': revenue
            }

    return gateway_revenue_count

def filter_hits_with_cards_or_banks(hits):
    filtered_hits = []
    other_hits = []
    for hit in hits:
        has_banks = re.search(r'Banks:\s*\[(.*?)\]', hit, re.IGNORECASE)
        has_cards = re.search(r'Cards:\s*\[(.*?)\]', hit, re.IGNORECASE)
        
        if (has_banks and has_banks.group(1).strip()) or (has_cards and has_cards.group(1).strip()):
            filtered_hits.append(hit)
        else:
            other_hits.append(hit)
    return filtered_hits, other_hits

async def main():
    if not os.path.exists('config.json'):
        config = {
            'shop_id': 0, 
            'sellpass_api_key': 'your_api_key_here',
            'product_id': 0,
            'telegram_bot_token': 'telegram_update_bot',
            'telegram_bot_token2': 'preorder_bot',
            'telegram_channel_id': 'your_channel_id_here',
            'telegram_channel_id2': 'your_channel_id_here'
        }
        with open('config.json', 'w') as f:
            json.dump(config, f, indent=4)
        print(f"{Fore.YELLOW}Config file created. Please fill in your details in config.json")
        input("Press Enter to close...")
        return
    
    with open('config.json', 'r') as f:
        config = json.load(f)

    while True:
        folder_path = select_folder()
        clear_console()
        print(f"{Fore.GREEN}Selected directory: {folder_path}")
        confirm = input(f"{Fore.YELLOW}Is this the correct directory? (Y/N): ").lower()
        if confirm == 'y':
            break

    stock_interval = get_stock_interval()

    gateway_mapping = {0: "Unknown", 7: "Balance", 3: "CashApp", 10: "Hoodpay"}
    status = "RUNNING"
    last_message_id = None
    update_task = None
    intial_stock = 0
    telegram_message_id = ''
    initial_Hoodpay_revenue = 0
    initial_Balance_revenue = 0
    initial_CashApp_revenue = 0
    initial_Unknown_revenue = 0
    present_Hoodpay_revenue = 0
    present_Balance_revenue = 0
    present_CashApp_revenue = 0
    present_Unknown_revenue = 0
    stocked_amount = 0
    total_stocked = 0
    hits = []

    intial_stock = await get_current_stock(config)

    ssl_context = ssl.create_default_context(cafile=certifi.where())
    
    dashboard_data = await fetch_dashboard_data(config, ssl_context)
    
    if dashboard_data is None and ssl_context.verify_mode != ssl.CERT_NONE:
        print("SSL verification failed. Attempting to disable SSL verification...")
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        dashboard_data = await fetch_dashboard_data(config, ssl_context)


    if dashboard_data:
        gateway_revenue_count = calculate_revenue_and_count_per_gateway(dashboard_data, gateway_mapping)
        initial_Hoodpay_revenue = gateway_revenue_count.get('Hoodpay', {}).get('revenue', 0)
        initial_Balance_revenue = gateway_revenue_count.get('Balance', {}).get('revenue', 0)
        initial_CashApp_revenue = gateway_revenue_count.get('CashApp', {}).get('revenue', 0)
        initial_Unknown_revenue = gateway_revenue_count.get('Unknown', {}).get('revenue', 0)
        print("Successfully fetched dashboard data.")
    else:
        print("Failed to fetch dashboard data. Using default values for profits.")

    while True:
        try:
            clear_console()
            print(f"{Back.BLUE}{Fore.WHITE} Buffed Auto-Stock {status} ")
            print(f"{Fore.CYAN}Stocking every {stock_interval} logs")
            
            hits_file = os.path.join(folder_path, 'Hits.txt')
            current_lines = count_lines(hits_file)

            logs_left = stock_interval - current_lines
            print(f"{Fore.GREEN}{logs_left} logs left till stock")
            
            if current_lines >= stock_interval:
                status = "SORTING"
                clear_console()
                print(f"{Back.BLUE}{Fore.WHITE} Buffed Auto-Stock {status} ")
                
                with open(hits_file, 'r', encoding='utf-8') as f:
                    hitss = f.readlines()
                
                hits, _ = filter_hits_with_cards_or_banks(hitss)
                grouped_hits, parsed_countries = sort_and_stock(hits)
                
                status = "STOCKING"
                clear_console()
                print(f"{Back.BLUE}{Fore.WHITE} Buffed Auto-Stock {status} ")
                
                sorted_hits_set = set(hit.strip() for country_hits in grouped_hits.values() for hit in country_hits)
                
                print(f"{Fore.YELLOW}Total hits to be stocked: {len(sorted_hits_set)}")
                
                stocked_amount = await stock_product(config['shop_id'], config['product_id'], grouped_hits, config['sellpass_api_key'])
                total_stocked += stocked_amount
                
                print(f"{Fore.GREEN}Total hits stocked: {stocked_amount}")
                
                with open(hits_file, 'r', encoding='utf-8') as f:
                    all_hitss = f.readlines()
                
                all_hits, other = filter_hits_with_cards_or_banks(all_hitss)

                original_hit_count = len(all_hits)
                
                remaining_hits = [hit for hit in all_hits if hit.strip() not in sorted_hits_set]
                
                with open('nopayments.txt', 'a', encoding='utf-8') as f:
                    for line in other:
                        f.write(line)
                
                with open(hits_file, 'w', encoding='utf-8') as f:
                    for line in remaining_hits:
                        f.write(line)
                
                removed_hits_count = original_hit_count - len(remaining_hits)
                print(f"{Fore.GREEN}Sorted hits removed from Hits.txt")
                print(f"{Fore.GREEN}Removed {removed_hits_count} hits")
                
                script_dir = os.path.dirname(os.path.abspath(__file__))
                hitsfromshop_path = os.path.join(script_dir, 'Hitsfromshop.txt')
                
                with open(hitsfromshop_path, 'a', encoding='utf-8') as f:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"\n--- Stocked on {timestamp} ---\n")
                    for country, country_hits in grouped_hits.items():
                        if country_hits:
                            f.write(f"\n{country}:\n")
                            for hit in country_hits:
                                f.write(f"{hit.strip()}\n")
                
                print(f"{Fore.GREEN}Sorted hits saved to Hitsfromshop.txt")
                
                status = "RUNNING"

                if stocked_amount > 0:
                    if update_task:
                        update_task.cancel()
                    
                    if telegram_message_id != '':
                        await bot.delete_message(chat_id=config['telegram_channel_id'], message_id=telegram_message_id)
                        telegram_message_id = ''
                    
                    bot = Bot(token=config['telegram_bot_token'])
                    
                    initial_message = await bot.send_message(
                        chat_id=config['telegram_channel_id'],
                        text="Initializing stock update...",
                        parse_mode='Markdown',
                        disable_web_page_preview=True
                    )
                    last_message_id = initial_message.message_id
                    telegram_message_id = last_message_id
                    print(f"{Fore.GREEN}New Telegram notification sent")
                    update_task = asyncio.create_task(update_telegram_notification(config, last_message_id, intial_stock, stocked_amount, folder_path, stock_interval, parsed_countries))
            
            current_stock = await get_current_stock(config)
            average_price = await calculate_average_price(config)
            
            print(f"Current stock: {current_stock}")
            print(f"Average price: {average_price}")
            
            dashboard_data = await fetch_dashboard_data(config, ssl_context)
            
            if dashboard_data is None and ssl_context.verify_mode != ssl.CERT_NONE:
                print("SSL verification failed. Attempting to disable SSL verification...")
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                dashboard_data = await fetch_dashboard_data(config, ssl_context)


            if dashboard_data:
                gateway_revenue_count = calculate_revenue_and_count_per_gateway(dashboard_data, gateway_mapping)
                present_Hoodpay_revenue = gateway_revenue_count.get('Hoodpay', {}).get('revenue', 0)
                present_Balance_revenue = gateway_revenue_count.get('Balance', {}).get('revenue', 0)
                present_CashApp_revenue = gateway_revenue_count.get('CashApp', {}).get('revenue', 0)
                present_Unknown_revenue = gateway_revenue_count.get('Unknown', {}).get('revenue', 0)
                # debug
                # print(f'Initial Hoodpay Revenue: {initial_Hoodpay_revenue}')
                # print(f'Present Hoodpay Revenue: {present_Hoodpay_revenue}')
                # print(f'Initial Balance Revenue: {initial_Balance_revenue}')
                # print(f'Present Balance Revenue: {present_Balance_revenue}')
                # print(f'Initial CashApp Revenue: {initial_CashApp_revenue}')
                # print(f'Present CashApp Revenue: {present_CashApp_revenue}')
                # print(f'Initial Unkown Revenue: {initial_Unknown_revenue}')
                # print(f'Present Unkown Revenue: {present_Unknown_revenue}')
                print("Successfully fetched dashboard data.")
            else:
                print("Failed to fetch dashboard data. Using default values for profits.")

            print(f"{Fore.GREEN}Total stocked since start: {total_stocked}")
            print(f"{Fore.GREEN}Initial stock interval: {stock_interval}")
            print(f"{Fore.YELLOW}Hoodpay Profit since start: ${present_Hoodpay_revenue - initial_Hoodpay_revenue:.2f}")
            print(f"{Fore.YELLOW}Balance Profit since start: ${present_Balance_revenue - initial_Balance_revenue:.2f}")
            print(f"{Fore.YELLOW}CashApp Profits since start: ${present_CashApp_revenue - initial_CashApp_revenue:.2f}")
            print(f"{Fore.YELLOW}Unkown Profits since start: ${present_Unknown_revenue - initial_Unknown_revenue:.2f}")
            
            if stocked_amount < stock_interval:
                print(f"{Fore.YELLOW}Warning: Stocked amount ({stocked_amount}) is less than the requested interval ({stock_interval})")

            if status.startswith("ERROR"):
                print(f"{Fore.RED}Error detected: {status}")

            await asyncio.sleep(60)
        except Exception as e:
            print(e.with_traceback)
            error_message = f"An error occurred in main loop: {type(e).__name__} - {str(e)}"
            print(f"{Fore.RED}{error_message}")
            status = f"ERROR: {error_message}"
            
            with open("error_log.txt", "a") as log_file:
                log_file.write(f"{datetime.now()}: {error_message}\n")
            
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
