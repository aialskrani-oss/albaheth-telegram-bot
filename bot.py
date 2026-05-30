import os
import time
import logging
import threading
import telebot
from telebot import types as tgtypes
from openai import OpenAI, RateLimitError, APIStatusError, APIConnectionError
from flask import Flask

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── Environment variables ──────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
FORCE_SUB_CHANNEL  = os.environ.get("FORCE_SUB_CHANNEL", "").strip()
BOT_NAME           = os.environ.get("BOT_NAME", "بوت البحث الأكاديمي العراقي")
PORT               = int(os.environ.get("PORT", 8080))

# ── AI configuration (multi-key fallback) ─────────────────────────────────────
# AI_BASE_URL  : OpenAI-compatible base URL
#   Default    : Google Gemini OpenAI-compatible endpoint
#   FreeLLMAPI : https://your-freellmapi.onrender.com/v1
AI_BASE_URL = os.environ.get(
    "AI_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai/"
)
AI_MODEL = os.environ.get("AI_MODEL", "gemini-2.5-flash")

# AI_API_KEYS: comma-separated list of API keys (tried in order, fallback on failure)
# Example: "key1,key2,key3"
_raw_keys = os.environ.get("AI_API_KEYS", os.environ.get("GEMINI_API_KEY", ""))
AI_API_KEYS = [k.strip() for k in _raw_keys.split(",") if k.strip()]

if not AI_API_KEYS:
    raise RuntimeError("No AI API keys configured. Set AI_API_KEYS environment variable.")

logger.info("Loaded %d AI API key(s). Model: %s  Base: %s", len(AI_API_KEYS), AI_MODEL, AI_BASE_URL)

# ── System instruction ─────────────────────────────────────────────────────────
SYSTEM_INSTRUCTION = os.environ.get("SYSTEM_INSTRUCTION", """أنت خبير في البحث الأكاديمي المتخصص في الشأن العراقي. مهمتك هي مساعدة الباحثين والطلاب في العثور على مصادر موثوقة من الجامعات والمجلات العلمية العراقية.

قواعد العمل الثابتة:
عند استلام عنوان بحث أو نص، ابحث عن المصادر في:
- بوابة المجلات الأكاديمية العلمية العراقية (IASJ): وهي المرجع الرئيسي لكل المجلات المحكمة في العراق.
- المستودعات الرقمية للجامعات: مثل مستودع جامعة بغداد، البصرة، الموصل، بابل، المستنصرية، والكوفة.
- المجلات العراقية المفهرسة عالمياً: (مثل المجلات المدرجة في Scopus أو Clarivate).

طريقة تقديم المصادر:
- ابدأ بذكر اسم البحث/المقال.
- اسم الباحث (الباحثين).
- اسم المجلة العلمية والجامعة الصادرة عنها.
- سنة النشر، المجلد، والعدد.
- رابط مباشر للبحث إن وجد (خاصة روابط iasj.net).

تنسيق المراجع:
قدم المصادر بتنسيق APA أو التنسيق الذي يطلبه المستخدم.

الذكاء في التحليل:
- إذا كان الموضوع 'هندسي'، ركز على مجلات الهندسة مثل 'مجلة الهندسة والتكنولوجيا' (الجامعة التكنولوجية).
- إذا كان 'طبياً'، ركز على 'مجلة بابل الطبية' أو 'مجلة بغداد للعلوم'.
- إذا لم تجد مصدراً عراقياً مباشراً، اقترح أقرب الأطاريح والرسائل الجامعية المتعلقة بالموضوع من المستودعات الوطنية.

اللغة: قدم النتائج باللغة الإنجليزية مع إبقاء المصطلحات التقنية والروابط كما هي.""")

# ── Telegram bot ───────────────────────────────────────────────────────────────
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=True)

user_histories: dict[int, list[dict]] = {}


# ── AI client builder ──────────────────────────────────────────────────────────
def _make_client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=AI_BASE_URL)


def chat_with_ai(user_id: int, user_message: str) -> str:
    """Call the AI with automatic multi-key fallback."""
    if user_id not in user_histories:
        user_histories[user_id] = []

    user_histories[user_id].append({"role": "user", "content": user_message})

    messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}] + user_histories[user_id]

    last_error: Exception = RuntimeError("No API keys configured.")
    for idx, api_key in enumerate(AI_API_KEYS):
        client = _make_client(api_key)
        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=AI_MODEL,
                    messages=messages,
                    temperature=0.7,
                )
                reply = response.choices[0].message.content
                user_histories[user_id].append({"role": "assistant", "content": reply})
                if idx > 0:
                    logger.info("Succeeded with API key #%d after %d key(s) failed.", idx + 1, idx)
                return reply

            except RateLimitError as e:
                last_error = e
                logger.warning("Key #%d attempt %d: rate limited — %s", idx + 1, attempt + 1, e)
                # Move to the next key immediately on 429
                break

            except (APIStatusError, APIConnectionError) as e:
                last_error = e
                wait = 5 * (2 ** attempt)
                logger.warning(
                    "Key #%d attempt %d: %s — retrying in %ds", idx + 1, attempt + 1, e, wait
                )
                if attempt < 2:
                    time.sleep(wait)

            except Exception as e:
                last_error = e
                logger.error("Key #%d attempt %d: unexpected error — %s", idx + 1, attempt + 1, e)
                break

    # All keys exhausted
    # Roll back the user message we added so history stays clean
    if user_histories[user_id] and user_histories[user_id][-1]["role"] == "user":
        user_histories[user_id].pop()
    raise last_error


# ── Force Subscribe helpers ────────────────────────────────────────────────────
def is_subscribed(user_id: int) -> bool:
    if not FORCE_SUB_CHANNEL:
        return True
    try:
        member = bot.get_chat_member(FORCE_SUB_CHANNEL, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return True


def get_channel_link() -> str:
    ch = FORCE_SUB_CHANNEL
    if ch.startswith("@"):
        return f"https://t.me/{ch[1:]}"
    return f"https://t.me/{ch}"


def send_force_sub_message(message) -> None:
    markup = tgtypes.InlineKeyboardMarkup()
    markup.add(tgtypes.InlineKeyboardButton("📢 اشترك في القناة", url=get_channel_link()))
    markup.add(tgtypes.InlineKeyboardButton("✅ تحققت من اشتراكي", callback_data="check_sub"))
    bot.send_message(
        message.chat.id,
        f"⚠️ للاستخدام البوت يجب أن تكون مشتركاً في القناة أولاً:\n\n"
        f"القناة: {FORCE_SUB_CHANNEL}\n\n"
        f"اشترك ثم اضغط على زر التحقق أدناه.",
        reply_markup=markup,
    )


def check_subscription(func):
    """Decorator: gate a handler behind force-sub check."""
    def wrapper(message):
        if FORCE_SUB_CHANNEL and not is_subscribed(message.from_user.id):
            send_force_sub_message(message)
            return
        func(message)
    return wrapper


# ── Handlers ───────────────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda call: call.data == "check_sub")
def handle_check_sub(call):
    user_id = call.from_user.id
    if is_subscribed(user_id):
        bot.answer_callback_query(call.id, "✅ تم التحقق! يمكنك الآن استخدام البوت.")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        bot.send_message(
            call.message.chat.id,
            f"مرحباً! اشتراكك مُفعَّل الآن. أرسل لي موضوع بحثك وسأجد لك أفضل المصادر الأكاديمية العراقية.",
        )
    else:
        bot.answer_callback_query(
            call.id,
            "❌ لم نتمكن من التحقق من اشتراكك. تأكد من الاشتراك أولاً.",
            show_alert=True,
        )


@bot.message_handler(commands=["start"])
@check_subscription
def send_welcome(message):
    bot.reply_to(
        message,
        f"مرحباً بك في {BOT_NAME} 📚\n\n"
        "أنا خبير في البحث الأكاديمي المتخصص في الشأن العراقي.\n"
        "يمكنني مساعدتك في العثور على مصادر موثوقة من:\n"
        "• بوابة المجلات الأكاديمية العراقية (IASJ)\n"
        "• مستودعات الجامعات العراقية\n"
        "• المجلات العراقية المفهرسة عالمياً\n\n"
        "فقط أرسل لي عنوان بحثك أو الموضوع الذي تريد البحث فيه.\n\n"
        "استخدم /help للمساعدة أو /reset لبدء محادثة جديدة.",
    )


@bot.message_handler(commands=["help"])
@check_subscription
def send_help(message):
    bot.reply_to(
        message,
        "كيفية استخدام البوت:\n\n"
        "1. أرسل عنوان بحثك أو موضوعك الأكاديمي مباشرة.\n"
        "2. سأبحث لك في المصادر الأكاديمية العراقية الموثوقة.\n"
        "3. يمكنك تحديد نوع التنسيق المطلوب (APA، IEEE، إلخ).\n\n"
        "أمثلة:\n"
        "• 'أريد مصادر عن معالجة المياه في العراق'\n"
        "• 'ابحث عن دراسات حول التعليم الإلكتروني في الجامعات العراقية'\n"
        "• 'مصادر بتنسيق IEEE عن الطاقة الشمسية في العراق'\n\n"
        "/reset - لبدء محادثة جديدة\n"
        "/start - للعودة للبداية",
    )


@bot.message_handler(commands=["reset"])
@check_subscription
def reset_chat(message):
    user_id = message.from_user.id
    user_histories.pop(user_id, None)
    bot.reply_to(message, "تم إعادة تعيين المحادثة. يمكنك البدء بإرسال موضوع بحثك الجديد.")


@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_id = message.from_user.id
    if FORCE_SUB_CHANNEL and not is_subscribed(user_id):
        send_force_sub_message(message)
        return
    try:
        bot.send_chat_action(message.chat.id, "typing")
        reply_text = chat_with_ai(user_id, message.text)
        _send_long_message(message.chat.id, reply_text, reply_to=message)
    except Exception as e:
        logger.error("Error handling message from user %d: %s", user_id, e)
        user_histories.pop(user_id, None)
        bot.reply_to(
            message,
            "عذراً، حدث خطأ أثناء معالجة طلبك. يرجى المحاولة مرة أخرى لاحقاً.\n\n"
            f"الخطأ: {str(e)[:200]}",
        )


def _send_long_message(chat_id: int, text: str, reply_to=None) -> None:
    chunks = [text[i:i + 4096] for i in range(0, len(text), 4096)]
    for i, chunk in enumerate(chunks):
        if i == 0 and reply_to:
            bot.reply_to(reply_to, chunk)
        else:
            bot.send_message(chat_id, chunk)


# ── Flask keep-alive (required by Render to pass health checks) ────────────────
flask_app = Flask(__name__)


@flask_app.route("/")
def home():
    return f"{BOT_NAME} is running!", 200


@flask_app.route("/health")
def health():
    return "OK", 200


def run_flask() -> None:
    flask_app.run(host="0.0.0.0", port=PORT)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("Force Subscribe: %s", FORCE_SUB_CHANNEL or "disabled")
    logger.info("Bot name      : %s", BOT_NAME)
    logger.info("AI base URL   : %s", AI_BASE_URL)
    logger.info("AI model      : %s", AI_MODEL)
    logger.info("API keys      : %d key(s) loaded", len(AI_API_KEYS))
    logger.info("Flask port    : %d", PORT)

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Web server started on port %d", PORT)

    logger.info("Starting Telegram bot polling...")
    bot.infinity_polling(
        timeout=60,
        long_polling_timeout=60,
        restart_on_change=False,
        skip_pending=True,
    )
