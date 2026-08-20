import asyncio
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

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

WAITING_PDF, WAITING_TARGET = range(2)

MIN_TARGET_BYTES = 20 * 1024
MAX_TARGET_BYTES = 100 * 1024 * 1024


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Cancel", callback_data="pdfc_cancel")]]
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
    workdir = context.user_data.pop("pdfc_workdir", None)
    if workdir:
        shutil.rmtree(workdir, ignore_errors=True)

    for key in (
        "pdfc_input_path",
        "pdfc_original_name",
        "pdfc_original_size",
    ):
        context.user_data.pop(key, None)


async def begin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    cleanup(context)

    workdir = tempfile.mkdtemp(
        prefix=f"sanjeevbot_pdfcompress_{update.effective_user.id}_"
    )
    context.user_data["pdfc_workdir"] = workdir

    await query.edit_message_text(
        "🗜️ Compress PDF\n\n"
        "Send me one PDF file.",
        reply_markup=cancel_keyboard(),
    )
    return WAITING_PDF


async def receive_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    document = update.message.document

    if not document or document.mime_type != "application/pdf":
        await update.message.reply_text(
            "Please send a PDF file.",
            reply_markup=cancel_keyboard(),
        )
        return WAITING_PDF

    workdir = context.user_data.get("pdfc_workdir")
    if not workdir:
        workdir = tempfile.mkdtemp(
            prefix=f"sanjeevbot_pdfcompress_{update.effective_user.id}_"
        )
        context.user_data["pdfc_workdir"] = workdir

    tg_file = await document.get_file()
    original_name = document.file_name or "document.pdf"
    path = Path(workdir) / original_name
    await tg_file.download_to_drive(custom_path=path)

    size = path.stat().st_size

    context.user_data["pdfc_input_path"] = str(path)
    context.user_data["pdfc_original_name"] = original_name
    context.user_data["pdfc_original_size"] = size

    await update.message.reply_text(
        f"✅ PDF received.\n\n"
        f"Original size: {human_size(size)}\n\n"
        "Now enter the maximum size you want.\n"
        "Examples:\n"
        "• 500 KB\n"
        "• 1 MB\n"
        "• 2.5 MB",
        reply_markup=cancel_keyboard(),
    )
    return WAITING_TARGET


def run_ghostscript(
    input_path: Path,
    output_path: Path,
    dpi: int,
    jpeg_quality: int,
) -> None:
    command = [
        "gs",
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        "-dDetectDuplicateImages=true",
        "-dCompressFonts=true",
        "-dSubsetFonts=true",
        "-dEmbedAllFonts=true",
        "-dDownsampleColorImages=true",
        "-dColorImageDownsampleType=/Bicubic",
        f"-dColorImageResolution={dpi}",
        "-dDownsampleGrayImages=true",
        "-dGrayImageDownsampleType=/Bicubic",
        f"-dGrayImageResolution={dpi}",
        "-dDownsampleMonoImages=true",
        "-dMonoImageDownsampleType=/Subsample",
        f"-dMonoImageResolution={max(dpi * 2, 150)}",
        f"-dJPEGQ={jpeg_quality}",
        f"-sOutputFile={output_path}",
        str(input_path),
    ]

    subprocess.run(
        command,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def compress_pdf_to_target(
    input_path: Path,
    workdir: Path,
    target_bytes: int,
) -> tuple[Path, int, int, bool]:
    # Profiles move from quality-preserving to increasingly aggressive.
    profiles = [
        (200, 85),
        (170, 80),
        (150, 75),
        (130, 70),
        (110, 65),
        (96, 60),
        (85, 55),
        (72, 50),
        (60, 45),
        (50, 40),
    ]

    best_path = None
    best_size = input_path.stat().st_size
    best_profile = profiles[0]

    for index, (dpi, quality) in enumerate(profiles, start=1):
        candidate = workdir / f"compressed_{index}.pdf"

        run_ghostscript(
            input_path=input_path,
            output_path=candidate,
            dpi=dpi,
            jpeg_quality=quality,
        )

        if not candidate.exists():
            continue

        size = candidate.stat().st_size

        if size < best_size:
            best_path = candidate
            best_size = size
            best_profile = (dpi, quality)

        if size <= target_bytes:
            return candidate, dpi, quality, True

    if best_path is None:
        raise RuntimeError("Ghostscript did not produce a valid compressed PDF.")

    return best_path, best_profile[0], best_profile[1], False


def output_filename(original_name: str) -> str:
    stem = Path(original_name).stem
    safe = re.sub(r'[\\/:*?"<>|]+', "_", stem).strip(" .")
    if not safe:
        safe = "document"
    return f"{safe}_compressed.pdf"


async def receive_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    target = parse_target_size(update.message.text)

    if target is None:
        await update.message.reply_text(
            "I couldn't understand that size.\n\n"
            "Enter something like `500 KB`, `1 MB`, or `2.5 MB`.\n"
            "Allowed range: 20 KB to 100 MB.",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard(),
        )
        return WAITING_TARGET

    input_path_value = context.user_data.get("pdfc_input_path")
    original_size = context.user_data.get("pdfc_original_size", 0)
    original_name = context.user_data.get("pdfc_original_name", "document.pdf")
    workdir_value = context.user_data.get("pdfc_workdir")

    if (
        not input_path_value
        or not workdir_value
        or not Path(input_path_value).exists()
    ):
        cleanup(context)
        await update.message.reply_text(
            "⚠️ Your PDF session expired. Please start again.",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END

    input_path = Path(input_path_value)
    workdir = Path(workdir_value)

    if target >= original_size:
        await update.message.reply_text(
            f"ℹ️ Your PDF is already {human_size(original_size)}, "
            f"which is smaller than your requested {human_size(target)}.\n\n"
            "I'll send the original PDF back instead."
        )

        with input_path.open("rb") as file:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=file,
                filename=original_name,
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
        f"⏳ Compressing your PDF toward {human_size(target)}..."
    )

    try:
        compressed_path, dpi, quality, reached_target = await asyncio.to_thread(
            compress_pdf_to_target,
            input_path,
            workdir,
            target,
        )

        final_size = compressed_path.stat().st_size
        filename = output_filename(original_name)

        if reached_target:
            status = "✅ Compression complete!"
        else:
            status = (
                "⚠️ I compressed the PDF as much as this method safely could, "
                "but couldn't reach the exact requested size."
            )

        with compressed_path.open("rb") as file:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=file,
                filename=filename,
                caption=(
                    f"{status}\n\n"
                    f"Before: {human_size(original_size)}\n"
                    f"After: {human_size(final_size)}\n"
                    f"Target: {human_size(target)}\n"
                    f"Image DPI: {dpi}\n"
                    f"JPEG quality: {quality}"
                ),
            )

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Choose another SanjeevBot tool:",
            reply_markup=main_menu_keyboard(),
        )

    except subprocess.CalledProcessError as exc:
        logger.exception("Ghostscript failed: %s", exc.stderr)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ I couldn't compress that PDF. The file may be damaged, encrypted, or unsupported.",
            reply_markup=main_menu_keyboard(),
        )

    except Exception:
        logger.exception("PDF compression failed")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ I couldn't compress that PDF. Please try another file.",
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
        "Please send one PDF file.",
        reply_markup=cancel_keyboard(),
    )
    return WAITING_PDF


def build_compress_pdf_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(begin, pattern=r"^compress_pdf$")
        ],
        states={
            WAITING_PDF: [
                CallbackQueryHandler(cancel, pattern=r"^pdfc_cancel$"),
                MessageHandler(filters.Document.PDF, receive_pdf),
                MessageHandler(filters.ALL & ~filters.COMMAND, reject),
            ],
            WAITING_TARGET: [
                CallbackQueryHandler(cancel, pattern=r"^pdfc_cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_target),
            ],
        },
        fallbacks=[],
        allow_reentry=True,
    )
