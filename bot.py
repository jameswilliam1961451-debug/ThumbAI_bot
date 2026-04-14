import os
import io
import asyncio
import logging
import aiohttp
from openai import AsyncOpenAI

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
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
URL = os.environ.get("RENDER_EXTERNAL_URL")
PORT = int(os.environ.get("PORT", 8000))

# Initialize OpenAI client
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# --- AI Image Generation ---
async def generate_dalle_image(prompt: str) -> bytes:
    """
    Generates an image using DALL·E 3 based on the prompt.
    Returns the image as bytes (JPEG/PNG).
    """
    response = await client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size="1024x1024",          # You can also use 1792x1024 or 1024x1792
        quality="standard",
        n=1,
    )
    image_url = response.data[0].url

    # Download the image from the URL
    async with aiohttp.ClientSession() as session:
        async with session.get(image_url) as resp:
            if resp.status == 200:
                return await resp.read()
            else:
                raise Exception(f"Failed to download image: {resp.status}")

# --- Telegram Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎨 *AI Thumbnail Generator*\n\n"
        "Send me a description of the image you want, and I'll create it using DALL·E 3.\n\n"
        "Example: `A cozy cabin in a snowy forest at sunset, digital art style`",
        parse_mode="Markdown"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives a prompt and replies with an AI-generated image."""
    prompt = update.message.text.strip()
    if not prompt:
        await update.message.reply_text("Please send a description for the image.")
        return

    # Let user know we're working
    status_msg = await update.message.reply_text("✨ Generating your image... This may take 10-20 seconds.")

    try:
        # Generate image using DALL·E
        image_bytes = await generate_dalle_image(prompt)

        # Send the image back
        await update.message.reply_photo(
            photo=io.BytesIO(image_bytes),
            filename="ai_thumbnail.png",
            caption=f"🖼️ *{prompt[:100]}*",
            parse_mode="Markdown"
        )
        logging.info(f"Generated DALL·E image for prompt: {prompt[:50]}...")

    except Exception as e:
        logging.error(f"DALL·E generation failed: {e}")
        await update.message.reply_text(
            "❌ Sorry, I couldn't generate that image. "
            "The prompt might be against OpenAI's content policy, or there was a technical issue."
        )
    finally:
        # Delete the "Generating..." status message
        await status_msg.delete()

# --- Web Server (same as before) ---
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
