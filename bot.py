import os
import io
import asyncio
import logging
from PIL import Image, ImageDraw, ImageFont

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import Response, PlainTextResponse
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction

# --- Configuration ---
logging.basicConfig(level=logging.INFO)
TOKEN = os.environ["BOT_TOKEN"]
URL = os.environ.get("RENDER_EXTERNAL_URL")
PORT = int(os.environ.get("PORT", 8000))

# Thumbnail settings (16:9 YouTube style)
WIDTH, HEIGHT = 1280, 720
BACKGROUND_COLOR = (30, 30, 30)      # Dark gray
TEXT_COLOR = (255, 255, 255)         # White

# --- Text-to-Thumbnail Generator ---
def create_text_thumbnail(text: str) -> io.BytesIO:
    """Generates a centered text image and returns it as a JPEG byte stream."""
    # Create a blank canvas
    img = Image.new("RGB", (WIDTH, HEIGHT), color=BACKGROUND_COLOR)
    draw = ImageDraw.Draw(img)

    # Try to load a nice font; fallback to default if not available
    try:
        # You can change the path to a custom .ttf file if you include one in your repo
        font = ImageFont.truetype("arial.ttf", size=80)
    except IOError:
        font = ImageFont.load_default()

    # Wrap text to fit within the image width (simple word wrap)
    lines = []
    words = text.split()
    current_line = ""
    for word in words:
        test_line = f"{current_line} {word}".strip()
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] <= WIDTH - 100:  # 50px padding on each side
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)

    # If no lines (empty text), use a placeholder
    if not lines:
        lines = ["Your Text Here"]

    # Calculate total text height
    total_height = sum(draw.textbbox((0, 0), line, font=font)[3] for line in lines)
    y_offset = (HEIGHT - total_height) // 2

    # Draw each line centered
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_width = bbox[2] - bbox[0]
        x = (WIDTH - text_width) // 2
        draw.text((x, y_offset), line, fill=TEXT_COLOR, font=font)
        y_offset += bbox[3] - bbox[1] + 10  # line spacing

    # Save to bytes buffer
    img_io = io.BytesIO()
    img.save(img_io, format="JPEG", quality=95)
    img_io.seek(0)
    return img_io

# --- Telegram Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🖼️ *Text to Thumbnail Bot*\n\n"
        "Send me any text and I'll generate a 1280x720 thumbnail with your words.",
        parse_mode="Markdown"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives text and replies with a generated thumbnail."""
    user_text = update.message.text.strip()
    if not user_text:
        await update.message.reply_text("Please send some text.")
        return

    await update.message.chat.send_action(ChatAction.UPLOAD_PHOTO)
    try:
        thumb_io = create_text_thumbnail(user_text)
        await update.message.reply_photo(
            photo=thumb_io,
            filename="thumbnail.jpg",
            caption=f"✅ Thumbnail for: _{user_text[:50]}..._" if len(user_text) > 50 else f"✅ Thumbnail for: _{user_text}_",
            parse_mode="Markdown"
        )
        logging.info(f"Generated thumbnail for: {user_text[:30]}...")
    except Exception as e:
        logging.error(f"Error generating thumbnail: {e}")
        await update.message.reply_text("❌ Sorry, something went wrong. Please try again.")

# --- Web Server for Webhooks (same as before) ---
async def main():
    app = Application.builder().token(TOKEN).updater(None).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    webhook_url = f"{URL}/telegram"
    await app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
    logging.info(f"Webhook set to: {webhook_url}")

    async def telegram(request: Request) -> Response:
        await app.update_queue.put(Update.de_json(await request.json(), app.bot))
        return Response()

    async def health(_: Request) -> PlainTextResponse:
        return PlainTextResponse("OK")

    starlette_app = Starlette(routes=[
        Route("/telegram", telegram, methods=["POST"]),
        Route("/healthcheck", health, methods=["GET"]),
    ])

    import uvicorn
    web_server = uvicorn.Server(uvicorn.Config(starlette_app, host="0.0.0.0", port=PORT, log_level="info"))

    async with app:
        await app.start()
        await web_server.serve()
        await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
