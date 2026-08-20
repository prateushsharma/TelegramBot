from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Main SanjeevBot menu. Add future features here."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📸 Photo to PDF", callback_data="photo_to_pdf")],
            [InlineKeyboardButton("🗜️ Compress Photo", callback_data="compress_photo")],
            [InlineKeyboardButton("📦 Compress PDF", callback_data="compress_pdf")],
        ]
    )


async def send_main_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str = "👋 Welcome to SanjeevBot!\n\nChoose a tool:",
) -> None:
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=main_menu_keyboard(),
        )
    else:
        await update.effective_message.reply_text(
            text,
            reply_markup=main_menu_keyboard(),
        )
