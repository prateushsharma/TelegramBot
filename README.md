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


## Feature #2 — Compress Photo

Current user flow:

1. Send `/start`
2. Press **🗜️ Compress Photo**
3. Send one image
4. SanjeevBot shows the original file size
5. Enter a target such as `500 KB`, `1 MB`, or `1.5 MB`
6. SanjeevBot compresses the image to at most that target when possible
7. The compressed JPEG is sent back with before/after size and resolution

The compressor first searches for the highest JPEG quality that fits the requested
size. If quality reduction alone is not enough, it progressively reduces image
dimensions until it can meet the target.


## Feature #3 — Compress PDF

Current user flow:

1. Send `/start`
2. Press **📦 Compress PDF**
3. Send one PDF
4. SanjeevBot shows the original file size
5. Enter a target such as `500 KB`, `1 MB`, or `2.5 MB`
6. SanjeevBot compresses the PDF using Ghostscript
7. The compressed PDF is returned with before/after size details

SanjeevBot tries progressively stronger image downsampling and JPEG compression
profiles. Some PDFs—especially text/vector-heavy PDFs that are already optimized—
may not be compressible to an arbitrary requested size. In that case, the bot sends
the smallest result it was able to produce and clearly reports that the requested
target was not reached.

### System dependency

PDF compression requires Ghostscript:

```bash
sudo apt install ghostscript
```

Patch #003 installs it automatically on Ubuntu when needed.

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
