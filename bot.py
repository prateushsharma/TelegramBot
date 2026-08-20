import logging

from telegram.ext import Application, CommandHandler, ConversationHandler

from config import BOT_TOKEN
from features.compress_photo import build_compress_photo_handler
from features.photo_to_pdf import build_photo_to_pdf_handler
from keyboards.main_menu import send_main_menu

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def start(update, context):
    await send_main_menu(update, context)
    return ConversationHandler.END


def build_application() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing. Copy .env.example to .env and add your bot token."
        )

    application = Application.builder().token(BOT_TOKEN).build()

    # Every feature owns its own conversation handler.
    application.add_handler(build_photo_to_pdf_handler())
    application.add_handler(build_compress_photo_handler())
    application.add_handler(CommandHandler("start", start))

    return application


if __name__ == "__main__":
    app = build_application()
    logger.info("SanjeevBot is starting...")
    app.run_polling(drop_pending_updates=False)
