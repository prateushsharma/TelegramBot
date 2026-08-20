import asyncio
import io
import logging
import re
import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageOps
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from keyboards.main_menu import main_menu_keyboard

logger = logging.getLogger(__name__)

WAITING_IMAGE, WAITING_TARGET = range(2)

MIN_TARGET_BYTES = 10 * 1024
MAX_TARGET_BYTES = 20 * 1024 * 1024


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Cancel", callback_data="cp_cancel")]]
    )


def human_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.2f} MB"


def parse_target_size(text: str) -> int | None:
    value = text.strip().lower().replace(" ", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(kb|k|mb|m)?", value)
    if not match:
        return None

    number = float(match.group(1))
    unit = match.group(2) or "kb"

    if unit in ("mb", "m"):
        size = int(number * 1024 * 1024)
    else:
        size = int(number * 1024)

    if not (MIN_TARGET_BYTES <= size <= MAX_TARGET_BYTES):
        return None

    return size


def cleanup(context: ContextTypes.DEFAULT_TYPE) -> None:
    workdir = context.user_data.pop("cp_workdir", None)
    if workdir:
        shutil.rmtree(workdir, ignore_errors=True)

    for key in (
        "cp_image_path",
        "cp_original_name",
        "cp_original_size",
    ):
        context.user_data.pop(key, None)


async def begin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    cleanup(context)

    workdir = tempfile.mkdtemp(
        prefix=f"sanjeevbot_compress_{update.effective_user.id}_"
    )
    context.user_data["cp_workdir"] = workdir

    await query.edit_message_text(
        "🗜️ Compress Photo\n\n"
        "Send me one photo.\n\n"
        "You can send it as a normal Telegram photo or as an image file.",
        reply_markup=cancel_keyboard(),
    )
    return WAITING_IMAGE


async def receive_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    workdir = context.user_data.get("cp_workdir")
    if not workdir:
        workdir = tempfile.mkdtemp(
            prefix=f"sanjeevbot_compress_{update.effective_user.id}_"
        )
        context.user_data["cp_workdir"] = workdir

    if update.message.photo:
        item = update.message.photo[-1]
        tg_file = await item.get_file()
        original_name = f"{item.file_unique_id}.jpg"
    elif (
        update.message.document
        and update.message.document.mime_type
        and update.message.document.mime_type.startswith("image/")
    ):
        item = update.message.document
        tg_file = await item.get_file()
        original_name = item.file_name or f"{item.file_unique_id}.img"
    else:
        await update.message.reply_text(
            "Please send an image/photo.",
            reply_markup=cancel_keyboard(),
        )
        return WAITING_IMAGE

    path = Path(workdir) / original_name
    await tg_file.download_to_drive(custom_path=path)

    size = path.stat().st_size
    context.user_data["cp_image_path"] = str(path)
    context.user_data["cp_original_name"] = original_name
    context.user_data["cp_original_size"] = size

    await update.message.reply_text(
        f"✅ Photo received.\n\n"
        f"Original size: {human_size(size)}\n\n"
        "Now enter the maximum size you want.\n"
        "Examples:\n"
        "• 500 KB\n"
        "• 1 MB\n"
        "• 1.5 MB",
        reply_markup=cancel_keyboard(),
    )
    return WAITING_TARGET


def jpeg_bytes(image: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    image.save(
        buf,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
    )
    return buf.getvalue()


def compress_to_target(input_path: Path, target_bytes: int) -> tuple[bytes, tuple[int, int], int]:
    with Image.open(input_path) as source:
        image = ImageOps.exif_transpose(source)

        if image.mode != "RGB":
            if image.mode in ("RGBA", "LA"):
                background = Image.new("RGB", image.size, "white")
                alpha = image.getchannel("A") if "A" in image.getbands() else None
                background.paste(image.convert("RGB"), mask=alpha)
                image = background
            else:
                image = image.convert("RGB")
        else:
            image = image.copy()

    original_w, original_h = image.size

    # Try progressively smaller dimensions. At each dimension, binary-search JPEG quality.
    scale = 1.0
    best: bytes | None = None
    best_quality = 1
    best_dims = image.size

    for _ in range(18):
        if scale == 1.0:
            working = image
        else:
            width = max(64, int(original_w * scale))
            height = max(64, int(original_h * scale))
            working = image.resize((width, height), Image.Resampling.LANCZOS)

        low, high = 1, 95
        local_best = None
        local_quality = 1

        while low <= high:
            quality = (low + high) // 2
            data = jpeg_bytes(working, quality)

            if len(data) <= target_bytes:
                local_best = data
                local_quality = quality
                low = quality + 1
            else:
                high = quality - 1

        if local_best is not None:
            best = local_best
            best_quality = local_quality
            best_dims = working.size
            if working is not image:
                working.close()
            break

        if working is not image:
            working.close()

        scale *= 0.85

    if best is None:
        # Last-resort minimum-size JPEG.
        width = max(64, int(original_w * scale))
        height = max(64, int(original_h * scale))
        working = image.resize((width, height), Image.Resampling.LANCZOS)
        best = jpeg_bytes(working, 1)
        best_quality = 1
        best_dims = working.size
        working.close()

    image.close()
    return best, best_dims, best_quality


async def receive_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    target = parse_target_size(update.message.text)
    if target is None:
        await update.message.reply_text(
            "I couldn't understand that size.\n\n"
            "Enter something like `500 KB`, `1 MB`, or `1.5 MB`.\n"
            "Allowed range: 10 KB to 20 MB.",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard(),
        )
        return WAITING_TARGET

    image_path = context.user_data.get("cp_image_path")
    original_size = context.user_data.get("cp_original_size", 0)

    if not image_path or not Path(image_path).exists():
        cleanup(context)
        await update.message.reply_text(
            "⚠️ Your image session expired. Please start again.",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END

    if target >= original_size:
        await update.message.reply_text(
            f"ℹ️ Your photo is already {human_size(original_size)}, "
            f"which is smaller than your requested {human_size(target)}.\n\n"
            "I'll send the original image back instead."
        )
        with Path(image_path).open("rb") as file:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=file,
                filename=context.user_data.get("cp_original_name", "photo.jpg"),
                caption=f"Original size: {human_size(original_size)}",
            )

        cleanup(context)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Choose another SanjeevBot tool:",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"⏳ Compressing to at most {human_size(target)}..."
    )

    try:
        data, dims, quality = await asyncio.to_thread(
            compress_to_target,
            Path(image_path),
            target,
        )

        output_name = "compressed_photo.jpg"

        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=io.BytesIO(data),
            filename=output_name,
            caption=(
                "✅ Compression complete!\n\n"
                f"Before: {human_size(original_size)}\n"
                f"After: {human_size(len(data))}\n"
                f"Target: {human_size(target)}\n"
                f"Resolution: {dims[0]}×{dims[1]}\n"
                f"JPEG quality: {quality}"
            ),
        )

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Choose another SanjeevBot tool:",
            reply_markup=main_menu_keyboard(),
        )
    except Exception:
        logger.exception("Photo compression failed")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ I couldn't compress that image. Please try another photo.",
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
    await update.message.reply_text(
        "Please send one image/photo.",
        reply_markup=cancel_keyboard(),
    )
    return WAITING_IMAGE


def build_compress_photo_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(begin, pattern=r"^compress_photo$")
        ],
        states={
            WAITING_IMAGE: [
                CallbackQueryHandler(cancel, pattern=r"^cp_cancel$"),
                MessageHandler(
                    filters.PHOTO | filters.Document.IMAGE,
                    receive_image,
                ),
                MessageHandler(filters.ALL & ~filters.COMMAND, reject),
            ],
            WAITING_TARGET: [
                CallbackQueryHandler(cancel, pattern=r"^cp_cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_target),
            ],
        },
        fallbacks=[],
        allow_reentry=True,
    )
