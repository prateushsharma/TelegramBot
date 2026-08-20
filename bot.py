import asyncio
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageOps
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
MAX_PHOTOS = int(os.getenv("MAX_PHOTOS", "30"))

CHOOSING, COLLECTING_PHOTOS, WAITING_FILENAME, READY_TO_CONVERT = range(4)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📸 Photo to PDF", callback_data="photo_to_pdf")]]
    )


def collecting_keyboard(photo_count: int) -> InlineKeyboardMarkup:
    rows = []
    if photo_count > 0:
        rows.append(
            [InlineKeyboardButton(
                f"✅ Convert {photo_count} photo{'s' if photo_count != 1 else ''}",
                callback_data="finish_photos",
            )]
        )
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def convert_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📄 Create PDF", callback_data="create_pdf")],
            [InlineKeyboardButton("✏️ Change filename", callback_data="change_filename")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
        ]
    )


def safe_filename(name: str) -> str:
    name = name.strip()
    if name.lower().endswith(".pdf"):
        name = name[:-4]
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name[:80] or "photos") + ".pdf"


def cleanup_user_files(context: ContextTypes.DEFAULT_TYPE) -> None:
    workdir = context.user_data.pop("workdir", None)
    if workdir:
        shutil.rmtree(workdir, ignore_errors=True)
    context.user_data.pop("photos", None)
    context.user_data.pop("pdf_filename", None)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cleanup_user_files(context)
    await update.message.reply_text(
        "👋 Welcome!\n\nChoose what you want to do:",
        reply_markup=main_menu(),
    )
    return CHOOSING


async def photo_to_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    cleanup_user_files(context)

    workdir = tempfile.mkdtemp(prefix=f"tgpdf_{update.effective_user.id}_")
    context.user_data["workdir"] = workdir
    context.user_data["photos"] = []

    await query.edit_message_text(
        "📸 Send me your photos one by one or multiple at a time.\n\n"
        f"I'll collect up to {MAX_PHOTOS} photos.\n"
        "When you're finished, press the Convert button.",
        reply_markup=collecting_keyboard(0),
    )
    return COLLECTING_PHOTOS


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photos = context.user_data.setdefault("photos", [])
    workdir = context.user_data.get("workdir")

    if not workdir:
        workdir = tempfile.mkdtemp(prefix=f"tgpdf_{update.effective_user.id}_")
        context.user_data["workdir"] = workdir

    if len(photos) >= MAX_PHOTOS:
        await update.message.reply_text(
            f"⚠️ Maximum {MAX_PHOTOS} photos reached.",
            reply_markup=collecting_keyboard(len(photos)),
        )
        return COLLECTING_PHOTOS

    photo = update.message.photo[-1]
    telegram_file = await photo.get_file()

    index = len(photos) + 1
    path = Path(workdir) / f"{index:03d}_{photo.file_unique_id}.jpg"
    await telegram_file.download_to_drive(custom_path=path)

    photos.append(str(path))

    await update.message.reply_text(
        f"✅ Added photo {len(photos)}.\n"
        "Send more photos, or press Convert when you're done.",
        reply_markup=collecting_keyboard(len(photos)),
    )
    return COLLECTING_PHOTOS


async def reject_non_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    count = len(context.user_data.get("photos", []))
    await update.message.reply_text(
        "Please send a photo/image here.",
        reply_markup=collecting_keyboard(count),
    )
    return COLLECTING_PHOTOS


async def finish_photos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    count = len(context.user_data.get("photos", []))
    if count == 0:
        await query.edit_message_text(
            "You haven't sent any photos yet.\n\nSend at least one photo.",
            reply_markup=collecting_keyboard(0),
        )
        return COLLECTING_PHOTOS

    await query.edit_message_text(
        f"Great — I have {count} photo{'s' if count != 1 else ''}.\n\n"
        "✏️ Now send the filename you want for the PDF.\n"
        "Example: `My Notes`",
        parse_mode="Markdown",
    )
    return WAITING_FILENAME


async def receive_filename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    filename = safe_filename(update.message.text)
    context.user_data["pdf_filename"] = filename

    await update.message.reply_text(
        f"📄 Filename: `{filename}`\n\n"
        "Press **Create PDF** to convert and receive your file.",
        parse_mode="Markdown",
        reply_markup=convert_keyboard(),
    )
    return READY_TO_CONVERT


async def change_filename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✏️ Send the new PDF filename:")
    return WAITING_FILENAME


def build_pdf(photo_paths: list[str], output_path: Path) -> None:
    images = []
    try:
        for path in photo_paths:
            with Image.open(path) as img:
                img = ImageOps.exif_transpose(img)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                else:
                    img = img.copy()
                images.append(img)

        if not images:
            raise ValueError("No images available to convert.")

        first, *rest = images
        first.save(
            output_path,
            "PDF",
            resolution=100.0,
            save_all=True,
            append_images=rest,
        )
    finally:
        for img in images:
            try:
                img.close()
            except Exception:
                pass


async def create_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    photos = context.user_data.get("photos", [])
    filename = context.user_data.get("pdf_filename", "photos.pdf")
    workdir = context.user_data.get("workdir")

    if not photos or not workdir:
        await query.edit_message_text(
            "⚠️ Your photo session expired. Please start again.",
            reply_markup=main_menu(),
        )
        cleanup_user_files(context)
        return CHOOSING

    output_path = Path(workdir) / filename

    await query.edit_message_text("⏳ Converting your photos to PDF...")
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.UPLOAD_DOCUMENT,
    )

    try:
        await asyncio.to_thread(build_pdf, photos, output_path)

        with output_path.open("rb") as pdf_file:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=pdf_file,
                filename=filename,
                caption=f"✅ Done! Converted {len(photos)} photo(s) into `{filename}`.",
                parse_mode="Markdown",
            )

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="What would you like to do next?",
            reply_markup=main_menu(),
        )
    except Exception:
        logger.exception("PDF conversion failed")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ I couldn't create the PDF. Please try again.",
            reply_markup=main_menu(),
        )
    finally:
        cleanup_user_files(context)

    return CHOOSING


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cleanup_user_files(context)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "Cancelled. Choose an option:",
            reply_markup=main_menu(),
        )
    else:
        await update.message.reply_text(
            "Cancelled. Choose an option:",
            reply_markup=main_menu(),
        )
    return CHOOSING


async def unknown_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Use the buttons below to choose an action.",
        reply_markup=main_menu(),
    )
    return CHOOSING


def build_application() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing. Copy .env.example to .env and add your bot token."
        )

    application = Application.builder().token(BOT_TOKEN).build()

    conversation = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING: [
                CallbackQueryHandler(photo_to_pdf, pattern=r"^photo_to_pdf$"),
                MessageHandler(filters.ALL & ~filters.COMMAND, unknown_choice),
            ],
            COLLECTING_PHOTOS: [
                CallbackQueryHandler(finish_photos, pattern=r"^finish_photos$"),
                CallbackQueryHandler(cancel, pattern=r"^cancel$"),
                MessageHandler(filters.PHOTO, receive_photo),
                MessageHandler(filters.ALL & ~filters.COMMAND, reject_non_photo),
            ],
            WAITING_FILENAME: [
                CallbackQueryHandler(cancel, pattern=r"^cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_filename),
            ],
            READY_TO_CONVERT: [
                CallbackQueryHandler(create_pdf, pattern=r"^create_pdf$"),
                CallbackQueryHandler(change_filename, pattern=r"^change_filename$"),
                CallbackQueryHandler(cancel, pattern=r"^cancel$"),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("cancel", cancel),
        ],
        allow_reentry=True,
    )

    application.add_handler(conversation)
    return application


if __name__ == "__main__":
    app = build_application()
    logger.info("Bot is starting...")
    app.run_polling(drop_pending_updates=False)
