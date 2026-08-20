import asyncio
import logging
import re
import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageOps
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import MAX_PHOTOS
from keyboards.main_menu import main_menu_keyboard

logger = logging.getLogger(__name__)

COLLECTING_PHOTOS, WAITING_FILENAME, READY_TO_CONVERT = range(3)


def collecting_keyboard(photo_count: int) -> InlineKeyboardMarkup:
    rows = []
    if photo_count:
        rows.append([
            InlineKeyboardButton(
                f"✅ Convert {photo_count} photo{'s' if photo_count != 1 else ''}",
                callback_data="ptp_finish",
            )
        ])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="ptp_cancel")])
    return InlineKeyboardMarkup(rows)


def convert_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Create PDF", callback_data="ptp_create")],
        [InlineKeyboardButton("✏️ Change filename", callback_data="ptp_rename")],
        [InlineKeyboardButton("❌ Cancel", callback_data="ptp_cancel")],
    ])


def safe_filename(name: str) -> str:
    name = name.strip()
    if name.lower().endswith(".pdf"):
        name = name[:-4]
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name[:80] or "photos") + ".pdf"


def cleanup(context: ContextTypes.DEFAULT_TYPE) -> None:
    workdir = context.user_data.pop("ptp_workdir", None)
    if workdir:
        shutil.rmtree(workdir, ignore_errors=True)
    context.user_data.pop("ptp_photos", None)
    context.user_data.pop("ptp_filename", None)


async def begin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    cleanup(context)

    context.user_data["ptp_workdir"] = tempfile.mkdtemp(
        prefix=f"sanjeevbot_ptp_{update.effective_user.id}_"
    )
    context.user_data["ptp_photos"] = []

    await query.edit_message_text(
        "📸 Photo to PDF\n\n"
        "Send your photos one by one or multiple at a time.\n"
        f"You can send up to {MAX_PHOTOS} photos.\n\n"
        "When you're finished, press Convert.",
        reply_markup=collecting_keyboard(0),
    )
    return COLLECTING_PHOTOS


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photos = context.user_data.setdefault("ptp_photos", [])
    workdir = context.user_data.get("ptp_workdir")

    if len(photos) >= MAX_PHOTOS:
        await update.message.reply_text(
            f"⚠️ Maximum {MAX_PHOTOS} photos reached.",
            reply_markup=collecting_keyboard(len(photos)),
        )
        return COLLECTING_PHOTOS

    photo = update.message.photo[-1]
    tg_file = await photo.get_file()
    path = Path(workdir) / f"{len(photos)+1:03d}_{photo.file_unique_id}.jpg"
    await tg_file.download_to_drive(custom_path=path)
    photos.append(str(path))

    await update.message.reply_text(
        f"✅ Added photo {len(photos)}.\nSend more, or press Convert.",
        reply_markup=collecting_keyboard(len(photos)),
    )
    return COLLECTING_PHOTOS


async def finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    count = len(context.user_data.get("ptp_photos", []))

    if not count:
        await query.edit_message_text(
            "Send at least one photo first.",
            reply_markup=collecting_keyboard(0),
        )
        return COLLECTING_PHOTOS

    await query.edit_message_text(
        f"Great — I have {count} photo{'s' if count != 1 else ''}.\n\n"
        "✏️ Send the filename you want for the PDF.\n"
        "Example: My Notes"
    )
    return WAITING_FILENAME


async def filename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = safe_filename(update.message.text)
    context.user_data["ptp_filename"] = name

    await update.message.reply_text(
        f"📄 Filename: {name}\n\nPress Create PDF when ready.",
        reply_markup=convert_keyboard(),
    )
    return READY_TO_CONVERT


async def rename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✏️ Send the new PDF filename:")
    return WAITING_FILENAME


def build_pdf(photo_paths: list[str], output: Path) -> None:
    images = []
    try:
        for path in photo_paths:
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source)
                image = image.convert("RGB") if image.mode != "RGB" else image.copy()
                images.append(image)

        if not images:
            raise ValueError("No images to convert.")

        images[0].save(
            output,
            "PDF",
            resolution=100.0,
            save_all=True,
            append_images=images[1:],
        )
    finally:
        for image in images:
            image.close()


async def create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    photos = context.user_data.get("ptp_photos", [])
    workdir = context.user_data.get("ptp_workdir")
    name = context.user_data.get("ptp_filename", "photos.pdf")

    if not photos or not workdir:
        cleanup(context)
        await query.edit_message_text(
            "⚠️ Session expired. Please start again.",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END

    output = Path(workdir) / name
    await query.edit_message_text("⏳ Creating your PDF...")
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.UPLOAD_DOCUMENT,
    )

    try:
        await asyncio.to_thread(build_pdf, photos, output)
        with output.open("rb") as file:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=file,
                filename=name,
                caption=f"✅ Done! {len(photos)} photo(s) converted to {name}.",
            )
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Choose another SanjeevBot tool:",
            reply_markup=main_menu_keyboard(),
        )
    except Exception:
        logger.exception("Photo-to-PDF conversion failed")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ I couldn't create the PDF. Please try again.",
            reply_markup=main_menu_keyboard(),
        )
    finally:
        cleanup(context)

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    cleanup(context)
    await query.edit_message_text(
        "Cancelled. Choose a SanjeevBot tool:",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    count = len(context.user_data.get("ptp_photos", []))
    await update.message.reply_text(
        "Please send a Telegram photo here.",
        reply_markup=collecting_keyboard(count),
    )
    return COLLECTING_PHOTOS


def build_photo_to_pdf_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(begin, pattern=r"^photo_to_pdf$")
        ],
        states={
            COLLECTING_PHOTOS: [
                CallbackQueryHandler(finish, pattern=r"^ptp_finish$"),
                CallbackQueryHandler(cancel, pattern=r"^ptp_cancel$"),
                MessageHandler(filters.PHOTO, receive_photo),
                MessageHandler(filters.ALL & ~filters.COMMAND, reject),
            ],
            WAITING_FILENAME: [
                CallbackQueryHandler(cancel, pattern=r"^ptp_cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, filename),
            ],
            READY_TO_CONVERT: [
                CallbackQueryHandler(create, pattern=r"^ptp_create$"),
                CallbackQueryHandler(rename, pattern=r"^ptp_rename$"),
                CallbackQueryHandler(cancel, pattern=r"^ptp_cancel$"),
            ],
        },
        fallbacks=[],
        allow_reentry=True,
    )
