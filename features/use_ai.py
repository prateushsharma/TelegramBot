import asyncio
import base64
import logging
import mimetypes
import shutil
import tempfile
from pathlib import Path

from groq import Groq
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import GROQ_API_KEY, GROQ_MODEL
from keyboards.main_menu import main_menu_keyboard

logger = logging.getLogger(__name__)

WAITING_INPUT, WAITING_PROMPT = range(2)

MAX_IMAGE_BYTES = 20 * 1024 * 1024
TELEGRAM_TEXT_CHUNK = 3900

SYSTEM_PROMPT = """You are the AI assistant inside SanjeevBot.

Help the user with the prompt they provide. If an image is included, carefully
inspect the image and use it as evidence for your answer.

For charts, graphs, financial-market screenshots, or stock-market images:
- describe what is actually visible,
- distinguish observation from inference,
- explain trends, levels, momentum, patterns, and uncertainty where relevant,
- never pretend to know hidden or unreadable values,
- do not guarantee future market movements,
- make clear that analysis is informational rather than personalized financial advice.

Be practical, clear, and concise unless the user asks for more detail.
"""


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Cancel", callback_data="ai_cancel")]]
    )


def prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🗑 Remove Photo", callback_data="ai_remove_photo")],
            [InlineKeyboardButton("❌ Cancel", callback_data="ai_cancel")],
        ]
    )


def cleanup(context: ContextTypes.DEFAULT_TYPE) -> None:
    workdir = context.user_data.pop("ai_workdir", None)
    if workdir:
        shutil.rmtree(workdir, ignore_errors=True)

    for key in ("ai_image_path", "ai_image_mime"):
        context.user_data.pop(key, None)


def encode_image_data_url(path: Path, mime_type: str) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def ask_groq(prompt: str, image_path: str | None, image_mime: str | None) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is missing. Add it to .env and restart SanjeevBot."
        )

    client = Groq(api_key=GROQ_API_KEY)

    if image_path:
        path = Path(image_path)
        mime_type = image_mime or "image/jpeg"
        image_url = encode_image_data_url(path, mime_type)

        user_content = [
            {
                "type": "text",
                "text": prompt,
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": image_url,
                },
            },
        ]
    else:
        user_content = prompt

    completion = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": user_content,
            },
        ],
        temperature=0.7,
        max_completion_tokens=2048,
        top_p=1,
        stream=False,
    )

    content = completion.choices[0].message.content

    if not content:
        return "I received an empty response from the AI model."

    return content.strip()


def split_message(text: str, limit: int = TELEGRAM_TEXT_CHUNK) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit

        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()

    return chunks


async def send_answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prompt: str,
) -> int:
    image_path = context.user_data.get("ai_image_path")
    image_mime = context.user_data.get("ai_image_mime")

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING,
    )

    status = await update.effective_message.reply_text(
        "🤖 SanjeevBot AI is thinking..."
    )

    try:
        answer = await asyncio.to_thread(
            ask_groq,
            prompt,
            image_path,
            image_mime,
        )

        try:
            await status.delete()
        except Exception:
            pass

        for chunk in split_message(answer):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=chunk,
            )

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Choose another SanjeevBot tool:",
            reply_markup=main_menu_keyboard(),
        )

    except Exception as exc:
        logger.exception("Groq AI request failed")

        message = str(exc)
        if "GROQ_API_KEY is missing" in message:
            user_message = (
                "❌ Groq is not configured yet.\n\n"
                "Add `GROQ_API_KEY` to SanjeevBot's `.env` file and restart the bot."
            )
        else:
            user_message = (
                "❌ The AI request failed. Please try again.\n\n"
                "If this keeps happening, check your Groq API key/model and the terminal logs."
            )

        try:
            await status.edit_text(user_message, parse_mode="Markdown")
        except Exception:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=user_message,
                parse_mode="Markdown",
            )

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Choose another SanjeevBot tool:",
            reply_markup=main_menu_keyboard(),
        )

    finally:
        cleanup(context)

    return ConversationHandler.END


async def begin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    cleanup(context)

    context.user_data["ai_workdir"] = tempfile.mkdtemp(
        prefix=f"sanjeevbot_ai_{update.effective_user.id}_"
    )

    await query.edit_message_text(
        "🤖 Use AI\n\n"
        "You can:\n"
        "• send a text prompt, or\n"
        "• send one photo/image first, then send your prompt.\n\n"
        "Example:\n"
        "Send a stock-market chart, then say:\n"
        "\"Analyse this stock market graph.\"",
        reply_markup=cancel_keyboard(),
    )
    return WAITING_INPUT


async def receive_text_prompt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    prompt = update.message.text.strip()

    if not prompt:
        await update.message.reply_text(
            "Please send a non-empty prompt.",
            reply_markup=cancel_keyboard(),
        )
        return WAITING_INPUT

    return await send_answer(update, context, prompt)


async def receive_image(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    workdir_value = context.user_data.get("ai_workdir")
    if not workdir_value:
        workdir_value = tempfile.mkdtemp(
            prefix=f"sanjeevbot_ai_{update.effective_user.id}_"
        )
        context.user_data["ai_workdir"] = workdir_value

    workdir = Path(workdir_value)

    if update.message.photo:
        photo = update.message.photo[-1]
        tg_file = await photo.get_file()
        file_name = f"{photo.file_unique_id}.jpg"
        mime_type = "image/jpeg"

    elif (
        update.message.document
        and update.message.document.mime_type
        and update.message.document.mime_type.startswith("image/")
    ):
        document = update.message.document
        tg_file = await document.get_file()
        file_name = document.file_name or f"{document.file_unique_id}.img"
        mime_type = (
            document.mime_type
            or mimetypes.guess_type(file_name)[0]
            or "image/jpeg"
        )

    else:
        await update.message.reply_text(
            "Please send a text prompt or one image.",
            reply_markup=cancel_keyboard(),
        )
        return WAITING_INPUT

    path = workdir / file_name
    await tg_file.download_to_drive(custom_path=path)

    if path.stat().st_size > MAX_IMAGE_BYTES:
        path.unlink(missing_ok=True)
        await update.message.reply_text(
            "❌ That image is larger than Groq's 20 MB vision input limit.\n"
            "Please send a smaller image.",
            reply_markup=cancel_keyboard(),
        )
        return WAITING_INPUT

    old_path = context.user_data.get("ai_image_path")
    if old_path and old_path != str(path):
        Path(old_path).unlink(missing_ok=True)

    context.user_data["ai_image_path"] = str(path)
    context.user_data["ai_image_mime"] = mime_type

    caption = (update.message.caption or "").strip()

    if caption:
        # A caption counts as the prompt, so image + prompt can be sent together.
        return await send_answer(update, context, caption)

    await update.message.reply_text(
        "✅ Photo received.\n\n"
        "Now send the prompt/question you want SanjeevBot AI to answer about this image.",
        reply_markup=prompt_keyboard(),
    )
    return WAITING_PROMPT


async def receive_prompt_for_image(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    prompt = update.message.text.strip()

    if not prompt:
        await update.message.reply_text(
            "Please send a non-empty prompt for the photo.",
            reply_markup=prompt_keyboard(),
        )
        return WAITING_PROMPT

    return await send_answer(update, context, prompt)


async def replace_image(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    # If the user sends another image before the prompt, use the newest one.
    return await receive_image(update, context)


async def remove_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    await query.answer()

    image_path = context.user_data.pop("ai_image_path", None)
    context.user_data.pop("ai_image_mime", None)

    if image_path:
        Path(image_path).unlink(missing_ok=True)

    await query.edit_message_text(
        "🗑 Photo removed.\n\n"
        "Send a text prompt, or send another photo.",
        reply_markup=cancel_keyboard(),
    )
    return WAITING_INPUT


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    cleanup(context)

    await query.edit_message_text(
        "Cancelled. Choose a SanjeevBot tool:",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


async def reject_initial(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    await update.message.reply_text(
        "Send a text prompt or one photo/image.",
        reply_markup=cancel_keyboard(),
    )
    return WAITING_INPUT


async def reject_prompt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    await update.message.reply_text(
        "I already have the photo. Now send a text prompt about it.",
        reply_markup=prompt_keyboard(),
    )
    return WAITING_PROMPT


def build_use_ai_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(begin, pattern=r"^use_ai$")
        ],
        states={
            WAITING_INPUT: [
                CallbackQueryHandler(cancel, pattern=r"^ai_cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text_prompt),
                MessageHandler(
                    filters.PHOTO | filters.Document.IMAGE,
                    receive_image,
                ),
                MessageHandler(filters.ALL & ~filters.COMMAND, reject_initial),
            ],
            WAITING_PROMPT: [
                CallbackQueryHandler(remove_photo, pattern=r"^ai_remove_photo$"),
                CallbackQueryHandler(cancel, pattern=r"^ai_cancel$"),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_prompt_for_image,
                ),
                MessageHandler(
                    filters.PHOTO | filters.Document.IMAGE,
                    replace_image,
                ),
                MessageHandler(filters.ALL & ~filters.COMMAND, reject_prompt),
            ],
        },
        fallbacks=[],
        allow_reentry=True,
    )
