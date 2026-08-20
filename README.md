# SanjeevBot

**SanjeevBot** is an expandable Telegram utility bot written in Python.

Photo → PDF is the first feature. The project is intentionally modular so new tools can be added without turning the main bot file into one huge script.

## Feature #1 — Photo to PDF

Current user flow:

1. Send `/start`
2. Press **📸 Photo to PDF**
3. Send one or more photos
4. Press **✅ Convert N photos**
5. Enter the desired PDF filename
6. Press **📄 Create PDF**
7. SanjeevBot sends the generated PDF back

Temporary images and generated PDFs are removed from the computer after the operation finishes.

## Structure

```text
SanjeevBot/
├── bot.py
├── config.py
├── features/
│   ├── __init__.py
│   └── photo_to_pdf.py
├── keyboards/
│   ├── __init__.py
│   └── main_menu.py
├── requirements.txt
├── .env.example
├── .gitignore
├── start_bot.sh
└── README.md
```

Future features should normally get their own module under `features/`.

## Ubuntu setup

```bash
sudo apt install python3-venv -y

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env
```

Set:

```env
TELEGRAM_BOT_TOKEN=YOUR_REAL_BOTFATHER_TOKEN
MAX_PHOTOS=30
```

Run:

```bash
python bot.py
```

Or:

```bash
./start_bot.sh
```

## GitHub

Create an empty GitHub repository named `SanjeevBot`, then:

```bash
git remote add origin https://github.com/YOUR_USERNAME/SanjeevBot.git
git push -u origin main
```

Never commit `.env`. It is already ignored by Git.
# TelegramBot
