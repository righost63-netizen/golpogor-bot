import os
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore
from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    ChatMember
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- কনফিগারেশন এবং লগিং ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()

# এনভায়রনমেন্ট ভেরিয়েবল লোড
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
PREMIUM_CHANNEL_ID = os.getenv("PREMIUM_CHANNEL_ID")
FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON")

# --- ফায়ারবেস ইনিশিয়ালাইজেশন ---
import json
firebase_cred_dict = json.loads(FIREBASE_CREDENTIALS_JSON)
cred = credentials.Certificate(firebase_cred_dict)
firebase_admin.initialize_app(cred)
db = firestore.client()

# --- হেল্পার ফাংশনসমূহ ---

async def get_settings():
    """Firestore থেকে বটের সেটিংস লোড করে"""
    doc = db.collection("settings").document("config").get()
    return doc.to_dict() if doc.exists else {}

def is_admin(user_id: int) -> bool:
    """ইউজার অ্যাডমিন কি না চেক করে"""
    return user_id == ADMIN_ID

async def admin_only(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """অ্যাডমিন চেক করার ডেকোরেটর এর বিকল্প"""
    if update.effective_user.id != ADMIN_ID:
        return False
    return True

# --- ইউজার কমান্ড হ্যান্ডলারস ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start কমান্ড হ্যান্ডলার - ইউজার ডাটাবেসে সেভ করে ও ওয়েলকাম মেসেজ দেখায়"""
    user = update.effective_user
    user_ref = db.collection("users").document(str(user.id))
    
    settings = await get_settings()
    welcome_msg = settings.get("welcome_message", "স্বাগতম আমাদের সাবস্ক্রিপশন বটে!")

    # ইউজার যদি নতুন হয় তবে সেভ করা
    if not user_ref.get().exists:
        user_ref.set({
            "user_id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "status": "new",
            "created_at": datetime.now(timezone.utc)
        }, merge=True)

    keyboard = [
        [InlineKeyboardButton("📦 প্যাকেজ দেখুন", callback_data="view_packages")],
        [InlineKeyboardButton("📊 আমার স্ট্যাটাস", callback_data="my_status")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_msg, reply_markup=reply_markup)

async def show_packages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """অ্যাক্টিভ প্যাকেজগুলো লিস্ট করে দেখায়"""
    query = update.callback_query
    if query: await query.answer()

    packages = db.collection("packages").where("is_active", "==", True).stream()
    
    keyboard = []
    text = "🚀 **আমাদের প্রিমিয়াম প্যাকেজসমূহ:**\n\n"
    
    for pkg in packages:
        p = pkg.to_dict()
        text += f"🔹 {p['name']} - {p['price']} টাকা ({p['days']} দিন)\n"
        keyboard.append([InlineKeyboardButton(f"💳 {p['name']}", callback_data=f"pkg_{p['package_id']}")])

    if not keyboard:
        text = "দুঃখিত, বর্তমানে কোনো প্যাকেজ এভেইলঅ্যাবল নেই।"
    
    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def package_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """নির্দিষ্ট প্যাকেজের ডিটেইল এবং পেমেন্ট ইনস্ট্রাকশন দেখায়"""
    query = update.callback_query
    package_id = query.data.replace("pkg_", "")
    await query.answer()

    pkg_doc = db.collection("packages").document(package_id).get()
    settings = await get_settings()
    
    if pkg_doc.exists:
        p = pkg_doc.to_dict()
        payment_num = settings.get("payment_number", "Not Set")
        method = settings.get("payment_method", "বিকাশ")
        
        detail_text = (
            f"📦 **প্যাকেজ:** {p['name']}\n"
            f"💰 **দাম:** {p['price']} টাকা\n"
            f"⏳ **মেয়াদ:** {p['days']} দিন\n"
            f"📝 **বিবরণ:** {p['description']}\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💳 **পেমেন্ট ইনস্ট্রাকশন:**\n"
            f"১. আপনার {method} অ্যাপ থেকে {payment_num} নম্বরে {p['price']} টাকা 'Send Money' করুন।\n"
            f"২. পেমেন্ট সফল হলে নিচের ফরমেটে মেসেজ দিন:\n\n"
            f"`/verify {payment_num} {p['price']} TransactionID {package_id}`\n\n"
            f"উদাহরণ: `/verify 01700000000 {p['price']} TRX12345 {package_id}`"
        )
        
        keyboard = [[InlineKeyboardButton("✅ পেমেন্ট করেছি", callback_data="check_verify_info")]]
        await query.edit_message_text(detail_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def verify_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ইউজারের পেমেন্ট ভেরিফিকেশন রিকোয়েস্ট প্রসেস করে"""
    user = update.effective_user
    args = context.args # /verify {number} {amount} {trx_id} {pkg_id}

    if len(args) < 4:
        await update.message.reply_text("❌ সঠিক ফরম্যাটে তথ্য দিন।\nউদাহরণ: `/verify 01712345678 199 ABC123XYZ 30days`", parse_mode="Markdown")
        return

    bkash_num, amount, trx_id, pkg_id = args[0], args[1], args[2], args[3]

    # ট্রানজেকশন ডুপ্লিকেট চেক
    txn_check = db.collection("transactions").where("transaction_id", "==", trx_id).get()
    if txn_check:
        await update.message.reply_text("❌ এই Transaction ID টি আগে ব্যবহার করা হয়েছে।")
        return

    pkg_doc = db.collection("packages").document(pkg_id).get()
    if not pkg_doc.exists:
        await update.message.reply_text("❌ ভুল প্যাকেজ আইডি।")
        return
    
    pkg_data = pkg_doc.to_dict()

    # Firestore আপডেট
    try:
        db.collection("users").document(str(user.id)).update({
            "status": "pending",
            "package_id": pkg_id,
            "package_days": pkg_data['days'],
            "amount_paid": amount,
            "transaction_id": trx_id,
            "bkash_number": bkash_num,
            "created_at": datetime.now(timezone.utc)
        })

        db.collection("transactions").add({
            "user_id": user.id,
            "transaction_id": trx_id,
            "amount": amount,
            "package_id": pkg_id,
            "status": "pending",
            "created_at": datetime.now(timezone.utc)
        })

        # অ্যাডমিনকে নোটিফিকেশন
        admin_msg = (
            f"🔔 **নতুন পেমেন্ট রিকোয়েস্ট**\n"
            f"━━━━━━━━━━━━━━━\n"
            f"👤 নাম: {user.full_name}\n"
            f"🆔 User ID: `{user.id}`\n"
            f"📦 প্যাকেজ: {pkg_data['name']}\n"
            f"💰 পরিমাণ: {amount} টাকা\n"
            f"📱 নম্বর: {bkash_num}\n"
            f"🔑 TRX: `{trx_id}`\n"
            f"━━━━━━━━━━━━━━━"
        )
        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"adm_app_{user.id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"adm_rej_{user.id}")
            ]
        ]
        await context.bot.send_message(ADMIN_ID, admin_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        
        await update.message.reply_text("✅ আপনার পেমেন্ট ভেরিফিকেশনের জন্য পাঠানো হয়েছে। ২-৬ ঘণ্টার মধ্যে অ্যাক্সেস পাবেন।")
    
    except Exception as e:
        logger.error(f"Verification Error: {e}")
        await update.message.reply_text("❌ ডাটাবেস এরর। আবার চেষ্টা করুন।")

async def my_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ইউজারের বর্তমান সাবস্ক্রিপশন স্ট্যাটাস দেখায়"""
    query = update.callback_query
    user_id = update.effective_user.id
    if query: await query.answer()

    user_doc = db.collection("users").document(str(user_id)).get()
    if not user_doc.exists:
        text = "আপনার কোনো তথ্য পাওয়া যায়নি। /start চাপুন।"
    else:
        u = user_doc.to_dict()
        status = u.get("status", "নতুন")
        emoji = {"active": "✅", "pending": "⏳", "expired": "❌", "rejected": "🚫"}.get(status, "ℹ️")
        
        text = f"📊 **আপনার স্ট্যাটাস:**\n\n"
        text += f"অবস্থা: {status.capitalize()} {emoji}\n"
        if status == "active":
            expiry = u.get("expiry_date")
            if expiry:
                remaining = expiry - datetime.now(timezone.utc)
                text += f"প্যাকেজ: {u.get('package_id')}\n"
                text += f"মেয়াদ শেষ: {expiry.strftime('%d-%m-%Y %H:%M')}\n"
                text += f"বাকি দিন: {remaining.days} দিন\n"
        
    if query:
        await query.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

# --- অ্যাডমিন কমান্ড হ্যান্ডলারস ---

async def approve_user(update: Update, context: ContextTypes.DEFAULT_TYPE, target_id: str):
    """ইউজারকে অ্যাপ্রুভ করে এবং ইনভাইট লিংক পাঠায়"""
    user_ref = db.collection("users").document(target_id)
    u_data = user_ref.get().to_dict()
    
    if not u_data: return

    days = u_data.get("package_days", 30)
    expiry_date = datetime.now(timezone.utc) + timedelta(days=days)
    
    # ইনভাইট লিংক জেনারেশন
    try:
        invite_link_obj = await context.bot.create_chat_invite_link(
            chat_id=PREMIUM_CHANNEL_ID,
            member_limit=1,
            expire_date=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        invite_link = invite_link_obj.invite_link
        
        # ডাটাবেস আপডেট
        user_ref.update({
            "status": "active",
            "start_date": datetime.now(timezone.utc),
            "expiry_date": expiry_date,
            "joined_channel": True
        })

        settings = await get_settings()
        msg = settings.get("approval_message", "অভিনন্দন! আপনার সাবস্ক্রিপশনটি সফলভাবে সক্রিয় করা হয়েছে।")
        
        await context.bot.send_message(
            target_id, 
            f"🎉 {msg}\n\n🔗 চ্যানেল লিংক: {invite_link}\n(লিংকটির মেয়াদ ১ ঘণ্টা এবং ১ বার ব্যবহারযোগ্য)",
        )
        return True
    except Exception as e:
        logger.error(f"Approval failed: {e}")
        return False

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """অ্যাডমিন প্যানেলের বাটন ক্লিক হ্যান্ডেল করে"""
    if not await admin_only(update, context): return
    
    query = update.callback_query
    data = query.data
    await query.answer()

    if data.startswith("adm_app_"):
        target_id = data.replace("adm_app_", "")
        success = await approve_user(update, context, target_id)
        if success:
            await query.edit_message_text(f"✅ ইউজার {target_id} কে অ্যাপ্রুভ করা হয়েছে।")
            
    elif data.startswith("adm_rej_"):
        target_id = data.replace("adm_rej_", "")
        db.collection("users").document(target_id).update({"status": "rejected"})
        settings = await get_settings()
        rej_msg = settings.get("rejection_message", "দুঃখিত, আপনার পেমেন্ট রিকোয়েস্টটি রিজেক্ট করা হয়েছে।")
        await context.bot.send_message(target_id, f"❌ {rej_msg}")
        await query.edit_message_text(f"❌ ইউজার {target_id} কে রিজেক্ট করা হয়েছে।")

async def add_package(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """অ্যাডমিন কর্তৃক নতুন প্যাকেজ যোগ করা"""
    if not await admin_only(update, context): return
    # /addpackage {id} {days} {price} {name} {desc}
    args = context.args
    if len(args) < 5:
        await update.message.reply_text("ব্যবহারবিধি: `/addpackage 30days 30 500 Gold_Package Description`", parse_mode="Markdown")
        return
    
    pkg_id, days, price, name = args[0], int(args[1]), int(args[2]), args[3]
    desc = " ".join(args[4:])
    
    db.collection("packages").document(pkg_id).set({
        "package_id": pkg_id,
        "days": days,
        "price": price,
        "name": name,
        "description": desc,
        "is_active": True,
        "created_at": datetime.now(timezone.utc)
    })
    await update.message.reply_text(f"✅ প্যাকেজ '{name}' সফলভাবে যোগ করা হয়েছে।")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """বটের বর্তমান পরিসংখ্যান দেখায়"""
    if not await admin_only(update, context): return
    
    users = db.collection("users").get()
    active = [u for u in users if u.to_dict().get("status") == "active"]
    pending = [u for u in users if u.to_dict().get("status") == "pending"]
    
    total_income = sum([int(u.to_dict().get("amount_paid", 0)) for u in users if u.to_dict().get("status") == "active"])

    msg = (
        f"📊 **বট পরিসংখ্যান**\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👥 মোট ইউজার: {len(users)}\n"
        f"✅ একটিভ মেম্বার: {len(active)}\n"
        f"⏳ পেন্ডিং রিকোয়েস্ট: {len(pending)}\n"
        f"💰 মোট আয়: {total_income} টাকা"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# --- ব্যাকগ্রাউন্ড জবস (APScheduler) ---

async def expiry_check_job(context: ContextTypes.DEFAULT_TYPE):
    """মেয়াদ শেষ হওয়া ইউজারদের চ্যানেল থেকে কিক করে"""
    now = datetime.now(timezone.utc)
    active_users = db.collection("users").where("status", "==", "active").stream()

    for doc in active_users:
        u = doc.to_dict()
        expiry = u.get("expiry_date")
        
        if expiry and now > expiry:
            user_id = u['user_id']
            try:
                # চ্যানেল থেকে কিক এবং আনব্যান (যাতে পরে আবার জয়েন করতে পারে)
                await context.bot.ban_chat_member(PREMIUM_CHANNEL_ID, user_id)
                await context.bot.unban_chat_member(PREMIUM_CHANNEL_ID, user_id)
                
                db.collection("users").document(str(user_id)).update({"status": "expired"})
                
                settings = await get_settings()
                exp_msg = settings.get("expiry_message", "আপনার সাবস্ক্রিপশনের মেয়াদ শেষ হয়ে গেছে।")
                await context.bot.send_message(user_id, f"❌ {exp_msg}\nরিনিউ করতে /packages দেখুন।")
                logger.info(f"User {user_id} expired and kicked.")
            except Exception as e:
                logger.error(f"Kick failed for {user_id}: {e}")

async def expiry_warning_job(context: ContextTypes.DEFAULT_TYPE):
    """২৪ ঘণ্টা আগে ওয়ার্নিং মেসেজ পাঠায়"""
    warning_threshold = datetime.now(timezone.utc) + timedelta(hours=24)
    active_users = db.collection("users").where("status", "==", "active").stream()

    for doc in active_users:
        u = doc.to_dict()
        expiry = u.get("expiry_date")
        
        # যদি ঠিক ২৪ ঘণ্টার কম বাকি থাকে (কিন্তু ইতিমধ্যে শেষ হয়নি)
        if expiry and datetime.now(timezone.utc) < expiry <= warning_threshold:
            user_id = u['user_id']
            settings = await get_settings()
            warn_msg = settings.get("expiry_warning_message", "আপনার সাবস্ক্রিপশনের মেয়াদ আর মাত্র ২৪ ঘণ্টা বাকি আছে।")
            try:
                await context.bot.send_message(user_id, f"⚠️ {warn_msg}")
            except: pass

# --- ওয়েব সার্ভার (Render এর জন্য) ---
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Bot is running!')
    def log_message(self, format, *args):
        pass

def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logger.info(f"ওয়েব সার্ভার চালু port {port}")
    server.serve_forever()

# --- মেইন ফাংশন ---

async def post_init(application):
    """বট শুরু হওয়ার পরে scheduler চালু করে"""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(expiry_check_job, "interval", hours=1, args=[application])
    scheduler.add_job(expiry_warning_job, "interval", hours=6, args=[application])
    scheduler.start()
    logger.info("Scheduler চালু হয়েছে")

def main():
    # ওয়েব সার্ভার আলাদা থ্রেডে চালু
    threading.Thread(target=run_web_server, daemon=True).start()

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("packages", show_packages))
    application.add_handler(CommandHandler("verify", verify_payment))
    application.add_handler(CommandHandler("mystatus", my_status))
    application.add_handler(CommandHandler("addpackage", add_package))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CallbackQueryHandler(show_packages, pattern="^view_packages$"))
    application.add_handler(CallbackQueryHandler(my_status, pattern="^my_status$"))
    application.add_handler(CallbackQueryHandler(package_detail, pattern="^pkg_"))
    application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^adm_"))

    print("বটটি সফলভাবে চালু হয়েছে...")
    import asyncio

async def main():
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    await application.stop()

if __name__ == "__main__":
    asyncio.run(main())
    
