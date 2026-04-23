import os
import json
import logging
import requests
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# === CONFIG ===
BOT_TOKEN = os.getenv('BOT_TOKEN')  # Reads from Railway Variables
ADMIN_IDS = [8770974330, 6031406805]
WEBAPP_URL = "https://cashplugpro.netlify.app" # Updated to your new URL

# === ADMIN CONTROLLED SETTINGS ===
settings = {
    "vnum_api_key": "uw1w7rscldpmkrhp9lmuf5a8f2yc1lhv",
    "vnum_api_url": "https://no1verify.com/api",
    "vnum_server": 2,
    "referral_bonus": 200,
    "listing_fee": 200,
    "deposit_bank": "Opay",
    "deposit_account": "9136098875",
    "deposit_name": "Simon Chukwuemeka Ezugwu",
    "vnum_markup": 50,
    # New settings from admin9090.html
    "platform_fee": 5.0,
    "task_fee": 100,
    "marketplace_fee": 50,
    "paystack_enabled": False,
    "paystack_public_key": "",
    "paystack_secret_key": "",
    "auto_withdraw_enabled": False,
    "min_withdraw": 1000,
    "max_withdraw": 100000,
    "withdraw_fee": 2.0,
    "app_name": "CashPlug Pro",
    "support_telegram": "@support",
    "support_whatsapp1": "",
    "support_whatsapp2": ""
}

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# === IN-MEMORY DB ===
users = {}
products = []
orders = []
vnum_orders = []
vnum_prices = {}
banned_users = set()
pending_deposits = []
pending_withdrawals = []

def is_admin(user_id):
    return user_id in ADMIN_IDS

def is_banned(user_id):
    return user_id in banned_users

# === VIRTUAL NUMBER API FUNCTIONS ===
def get_vnum_price(country, service="whatsapp"):
    try:
        res = requests.get(f"{settings['vnum_api_url']}/getPrices", params={
            "api_key": settings['vnum_api_key'],
            "service": service,
            "country": country
        }, timeout=10)
        data = res.json()
        base_price = float(data.get("price", 0))
        final_price = int((base_price * 1600) + settings["vnum_markup"])
        return max(final_price, 100)
    except Exception as e:
        logging.error(f"Price fetch error: {e}")
        return 150

def buy_vnum_number(country, service="whatsapp"):
    try:
        res = requests.get(f"{settings['vnum_api_url']}/getNumber", params={
            "api_key": settings['vnum_api_key'],
            "service": service,
            "country": country,
            "server": settings['vnum_server']
        }, timeout=10)
        data = res.json()
        if data.get("status") == "success":
            return {"id": data["id"], "number": data["number"]}
        logging.warning(f"Number buy failed: {data}")
        return None
    except Exception as e:
        logging.error(f"Buy number error: {e}")
        return None

def get_vnum_status(order_id):
    try:
        res = requests.get(f"{settings['vnum_api_url']}/getStatus", params={
            "api_key": settings['vnum_api_key'],
            "id": order_id
        }, timeout=10)
        data = res.json()
        if data.get("status") == "sms_received":
            return f"STATUS_OK:{data.get('code')}"
        return data.get("status", "waiting")
    except Exception as e:
        logging.error(f"Status check error: {e}")
        return "ERROR"

# === BACKGROUND SMS POLLER ===
async def poll_sms_codes(app):
    while True:
        for order in vnum_orders:
            if order["status"] == "waiting":
                status = get_vnum_status(order["order_id"])
                if status.startswith("STATUS_OK:"):
                    code = status.split(":")[1]
                    order["status"] = "completed"
                    try:
                        await app.bot.send_message(
                            order["user_id"],
                            f"📲 SMS CODE RECEIVED\n\nService: {order['service'].title()}\nNumber: +{order['number']}\n\nCode: <code>{code}</code>",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logging.error(f"Failed to send code to {order['user_id']}: {e}")
        await asyncio.sleep(5)

# === START COMMAND ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    if is_banned(user.id):
        return await update.message.reply_text("❌ You are banned from using this bot.")

    if user.id not in users:
        users[user.id] = {
            "name": user.first_name,
            "username": user.username or f"user{user.id}",
            "balance": 0,
            "referrals": 0,
            "referred_by": None
        }

        if args and args[0].startswith("ref"):
            try:
                referrer_id = int(args[0][3:])
                if referrer_id in users and referrer_id!= user.id:
                    users[referrer_id]["balance"] += settings["referral_bonus"]
                    users[referrer_id]["referrals"] += 1
                    users[user.id]["referred_by"] = referrer_id
                    await context.bot.send_message(
                        referrer_id,
                        f"🎉 +₦{settings['referral_bonus']}! {user.first_name} joined. Balance: ₦{users[referrer_id]['balance']}"
                    )
            except: pass

    keyboard = [[InlineKeyboardButton(
        "🚀 Open Cash Plug",
        web_app=WebAppInfo(url=f'{WEBAPP_URL}/?id={user.id}&name={user.first_name}')
    )]]

    if is_admin(user.id):
        keyboard.append([InlineKeyboardButton("⚙️ Admin Panel", web_app=WebAppInfo(url=f'{WEBAPP_URL}/admin9090.html'))])

    await update.message.reply_text(
        f"Welcome to {settings['app_name']}, {user.first_name}! 💰\n\nEarn, shop, and withdraw instantly.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# === HANDLE WEB APP DATA ===
async def web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = json.loads(update.effective_message.web_app_data.data)
    action = data.get("action")
    user_id = int(data.get("user_id", user.id))

    if user_id not in users:
        users[user_id] = {"name": user.first_name, "username": user.username or f"user{user.id}", "balance": 0, "referrals": 0}

    u = users[user_id]

    # === USER ACTIONS ===
    if action == "get_user_data":
        await update.effective_message.reply_web_app(json.dumps({
            "balance": u["balance"], "referrals": u["referrals"], "username": u["username"], "name": u["name"]
        }))

    elif action == "get_profile_data":
        badge = "Admin" if is_admin(user_id) else "Member" if u["referrals"] == 0 else "VIP"
        await update.effective_message.reply_web_app(json.dumps({
            "balance": u["balance"], "name": u["name"], "username": u["username"], "badge": badge
        }))

    elif action == "get_wallet_data":
        await update.effective_message.reply_web_app(json.dumps({
            "balance": u["balance"],
            "deposit_bank": settings["opay"],
            "deposit_account": settings["9136098875"],
            "deposit_name": settings["Simon Chukwuemeka Ezugwu"],
            "paystack_enabled": settings["paystack_enabled"],
            "paystack_public_key": settings["paystack_public_key"]
        }))

    elif action == "get_earn_data":
        bot_info = await context.bot.get_me()
        await update.effective_message.reply_web_app(json.dumps({
            "balance": u["balance"], "referrals": u["referrals"], "username": u["username"], "bot_username": bot_info.username
        }))

    elif action == "get_products":
        await update.effective_message.reply_web_app(json.dumps({"products": products}))

    elif action == "filter_products":
        cat = data.get("category")
        filtered = products if cat == "all" else [p for p in products if p["category"] == cat]
        await update.effective_message.reply_web_app(json.dumps({"products": filtered}))

    elif action == "post_product":
        fee = settings["marketplace_fee"]
        if u["balance"] < fee:
            return await update.effective_message.reply_text(f"❌ Insufficient balance. Fee: ₦{fee}")

        u["balance"] -= fee
        products.append({
            "id": len(products) + 1, "seller_id": user_id, "seller": u["name"], "name": data["name"],
            "description": data["description"], "price": int(data["price"]), "phone": data["phone"],
            "category": data["category"], "payment_method": data["payment_method"], "image_url": "https://via.placeholder.com/150"
        })
        await update.effective_message.reply_text(f"✅ Product posted! -₦{fee} fee deducted")
        for admin_id in ADMIN_IDS:
            if admin_id!= user_id:
                try: await context.bot.send_message(admin_id, f"📦 New product: {data['name']} by {u['name']} ₦{data['price']}")
                except: pass

    elif action == "buy_product":
        price = int(data["price"])
        if u["balance"] < price and data.get("payment_method") == "instant":
            return await update.effective_message.reply_text("❌ Insufficient balance")
        orders.append({"buyer_id": user_id, "product_id": data["product_id"], "price": price, "payment_method": data.get("payment_method", "instant"), "status": "pending"})
        if data.get("payment_method") == "instant":
            u["balance"] -= price
            await update.effective_message.reply_text(f"✅ Order placed! -₦{price}")
        else:
            await update.effective_message.reply_text(f"✅ Order created. Payment: {data.get('payment_method')}")

    # === VIRTUAL NUMBERS ===
    elif action == "get_vnum_data":
        await update.effective_message.reply_web_app(json.dumps({"balance": u["balance"]}))

    elif action == "get_vnum_prices":
        service = data.get("service", "whatsapp")
        country_list = [
            {"code": "187", "name": "USA", "flag": "🇺🇸", "dial_code": "1"},
            {"code": "16", "name": "UK", "flag": "🇬🇧", "dial_code": "44"},
            {"code": "40", "name": "Nigeria", "flag": "🇳🇬", "dial_code": "234"},
            {"code": "6", "name": "Indonesia", "flag": "🇮🇩", "dial_code": "62"},
            {"code": "0", "name": "Russia", "flag": "🇷🇺", "dial_code": "7"},
        ]

        for c in country_list:
            custom = vnum_prices.get(c["code"], {}).get(service)
            c["price"] = custom if custom else get_vnum_price(c["code"], service)

        await update.effective_message.reply_web_app(json.dumps({"countries": country_list}))

    elif action == "buy_vnum":
        price = int(data["price"])
        if u["balance"] < price:
            return await update.effective_message.reply_text("❌ Insufficient balance")

        result = buy_vnum_number(data["country"], data["service"])
        if not result:
            return await update.effective_message.reply_text("❌ No numbers available. Try another country.")

        u["balance"] -= price
        vnum_orders.append({
            "user_id": user_id, "order_id": result["id"], "number": result["number"],
            "service": data["service"], "country": data["country"], "price": price, "status": "waiting"
        })
        await update.effective_message.reply_text(
            f"✅ Number: +{result['number']}\n\nWaiting for SMS...\nCode will be sent here automatically.\nValid 20 mins."
        )

    elif action == "confirm_deposit":
        pending_deposits.append({"user_id": user_id, "amount": data.get("amount", 0), "proof": data.get("proof", "")})
        await update.effective_message.reply_text("✅ Deposit submitted. Awaiting admin approval.")
        for admin_id in ADMIN_IDS:
            try: await context.bot.send_message(admin_id, f"💵 New deposit: {u['name']} ({user_id}) ₦{data.get('amount', 'N/A')}")
            except: pass

    elif action == "request_withdraw":
        amt = int(data["amount"])
        if u["balance"] < amt: return await update.effective_message.reply_text("❌ Insufficient balance")
        if amt < settings["min_withdraw"]: return await update.effective_message.reply_text(f"❌ Min ₦{settings['min_withdraw']}")
        if amt > settings["max_withdraw"]: return await update.effective_message.reply_text(f"❌ Max ₦{settings['max_withdraw']}")

        fee = int(amt * settings["withdraw_fee"] / 100)
        total = amt + fee
        if u["balance"] < total: return await update.effective_message.reply_text(f"❌ Insufficient balance. Need ₦{total} (includes {settings['withdraw_fee']}% fee)")

        u["balance"] -= total
        pending_withdrawals.append({
            "user_id": user_id, "amount": amt, "fee": fee,
            "bank": data['bank'], "account_number": data['account_number'], "account_name": data['account_name']
        })
        await update.effective_message.reply_text(f"✅ Withdrawal requested!\n₦{amt} to {data['bank']} {data['account_number']}\nFee: ₦{fee}")
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(admin_id, f"💸 WITHDRAW\nUser: {u['name']} ({user_id})\nAmount: ₦{amt}\nFee: ₦{fee}\nBank: {data['bank']}\nAcc: {data['account_number']}\nName: {data['account_name']}")
            except: pass

    # === ADMIN ACTIONS FROM admin9090.html ===
    if not is_admin(user_id):
        return

    elif action == "admin_load_dashboard":
        await update.effective_message.reply_web_app(json.dumps({
            "total_users": len(users),
            "total_balance": sum(u['balance'] for u in users.values()),
            "pending_deposits": len(pending_deposits),
            "pending_withdraws": len(pending_withdrawals),
            "settings": settings,
            "vnum_prices": vnum_prices,
            "admins": ADMIN_IDS,
            "recent_users": list(users.items())[-10:]
        }))

    elif action == "admin_update_deposit_account":
        settings["deposit_bank"] = data["bank_name"]
        settings["deposit_account"] = data["account_number"]
        settings["deposit_name"] = data["account_name"]
        await update.effective_message.reply_text("✅ Deposit account updated")

    elif action == "admin_save_paystack":
        settings["paystack_enabled"] = data["enabled"]
        settings["paystack_public_key"] = data["public_key"]
        settings["paystack_secret_key"] = data["secret_key"]
        await update.effective_message.reply_text("✅ Paystack config saved")

    elif action == "admin_update_withdraw_settings":
        settings["auto_withdraw_enabled"] = data["auto_enabled"]
        settings["min_withdraw"] = data["min_amount"]
        settings["max_withdraw"] = data["max_amount"]
        settings["withdraw_fee"] = data["fee_percent"]
        await update.effective_message.reply_text("✅ Withdraw settings saved")

    elif action == "admin_save_no1verify":
        settings["vnum_api_key"] = data["api_key"]
        settings["vnum_server"] = data["server"]
        await update.effective_message.reply_text("✅ No1Verify config saved")

    elif action == "admin_set_vnum_price":
        country, service, price = data["country"], data["service"], data["price"]
        if country not in vnum_prices: vnum_prices[country] = {}
        vnum_prices[country][service] = price
        await update.effective_message.reply_text(f"✅ Price set: {country} {service} ₦{price}")

    elif action == "admin_get_vnum_prices":
        await update.effective_message.reply_web_app(json.dumps({"vnum_prices": vnum_prices}))

    elif action == "admin_search_user":
        query = data["query"].lower()
        found = None
        for uid, udata in users.items():
            if str(uid) == query or udata["username"].lower() == query or udata["name"].lower() == query:
                found = {"user_id": uid, **udata, "banned": uid in banned_users}
                break
        await update.effective_message.reply_web_app(json.dumps({"user": found}))

    elif action == "admin_ban_user":
        target_id = int(data["user_id"])
        banned_users.add(target_id)
        await update.effective_message.reply_text(f"✅ User {target_id} banned")
        try: await context.bot.send_message(target_id, "❌ You have been banned by admin.")
        except: pass

    elif action == "admin_unban_user":
        target_id = int(data["user_id"])
        banned_users.discard(target_id)
        await update.effective_message.reply_text(f"✅ User {target_id} unbanned")

    elif action == "admin_reset_password":
        target_id = int(data["user_id"])
        await update.effective_message.reply_text(f"✅ Password reset sent to {target_id}")
        try: await context.bot.send_message(target_id, "🔑 Your password has been reset. Contact support.")
        except: pass

    elif action == "admin_credit_user":
        target_id = int(data["user_id"])
        amount = int(data["amount"])
        if target_id in users:
            users[target_id]["balance"] += amount
            await update.effective_message.reply_text(f"✅ Credited ₦{amount} to {target_id}")
            try: await context.bot.send_message(target_id, f"💰 Admin credited ₦{amount}!\nBalance: ₦{users[target_id]['balance']}")
            except: pass

    elif action == "admin_add_admin":
        new_admin_id = int(data["admin_id"])
        if new_admin_id not in ADMIN_IDS:
            ADMIN_IDS.append(new_admin_id)
            await update.effective_message.reply_text(f"✅ Added {new_admin_id} as admin")
            try: await context.bot.send_message(new_admin_id, "🎉 You are now admin!")
            except: pass

    elif action == "admin_update_settings":
        settings["platform_fee"] = data["platform_fee"]
        settings["task_fee"] = data["task_fee"]
        settings["marketplace_fee"] = data["marketplace_fee"]
        settings["vnum_markup"] = data["vnum_markup"]
        settings["app_name"] = data["app_name"]
        settings["support_telegram"] = data["support_telegram"]
        settings["support_whatsapp1"] = data["support_whatsapp1"]
        settings["support_whatsapp2"] = data["support_whatsapp2"]
        await update.effective_message.reply_text("✅ All settings saved")

    elif action == "admin_broadcast":
        msg = data["message"]
        count = 0
        for uid in users:
            try:
                await context.bot.send_message(uid, f"📢 {msg}")
                count += 1
            except: pass
        await update.effective_message.reply_text(f"✅ Broadcast sent to {count} users")

# === ADMIN COMMANDS ===
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return await update.message.reply_text("❌ Admin only")

    await update.message.reply_text(
        f"⚙️ ADMIN PANEL\n\n"
        f"Users: {len(users)}\nTotal Balance: ₦{sum(u['balance'] for u in users.values())}\n"
        f"Products: {len(products)}\nOrders: {len(orders)}\nVNum Orders: {len(vnum_orders)}\n\n"
        f"=== SETTINGS ===\n"
        f"Referral: ₦{settings['referral_bonus']}\nListing Fee: ₦{settings['listing_fee']}\n"
        f"VNum Markup: ₦{settings['vnum_markup']}\nPlatform Fee: {settings['platform_fee']}%\n"
        f"Task Fee: ₦{settings['task_fee']}\nMarketplace Fee: ₦{settings['marketplace_fee']}\n\n"
        f"Use the Web App Admin Panel for full control."
    )

# === MAIN ===
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data))

    async def post_init(application):
        asyncio.create_task(poll_sms_codes(application))

    app.post_init = post_init

    print("Cashplug bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
