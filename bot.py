import os
import io
import asyncio
import logging
import traceback
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
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
URL = os.environ.get("RENDER_EXTERNAL_URL")
PORT = int(os.environ.get("PORT", 8000))

if not TOKEN or not OPENAI_API_KEY:
    raise EnvironmentError("BOT_TOKEN and OPENAI_API_KEY must be set in environment variables")

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# --- AI Image Generation with Full Error Reporting ---
async def generate_dalle_image(prompt: str) -> bytes:
    try:
        logger.info(f"Calling DALL·E 3 with prompt: {prompt[:50]}...")
        response = await client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard",
            style="vivid",
            n=1,
        )
    except Exception as e:
        # Capture full traceback for logging
        error_details = traceback.format_exc()
        logger.error(f"OpenAI API call failed:\n{error_details}")
        raise Exception(f"OpenAI Error: {str(e)}")

    image_url = response.data[0].url
    logger.info(f"Image URL: {image_url}")

    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(image_url) as resp:
                if resp.status == 200:
                    return await resp.read()
                else:
                    text = await resp.text()
                    raise Exception(f"Download failed ({resp.status}): {text[:200]}")
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"Image download failed:\n{error_details}")
        raise Exception(f"Download Error: {str(e)}")

# --- Telegram Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎨 *AI Image Generator*\n\n"
        "Send me any description and I'll create an image using DALL·E 3.\n\n"
        "If something goes wrong, I'll show you the exact error for debugging.",
        parse_mode="Markdown"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = update.message.text.strip()
    if not prompt:
        await update.message.reply_text("Please send a description.")
        return

    status_msg = await update.message.reply_text("⏳ Generating...")

    try:
        image_bytes = await generate_dalle_image(prompt)
        await update.message.reply_photo(
            photo=io.BytesIO(image_bytes),
            caption=f"✅ *{prompt[:100]}*",
            parse_mode="Markdown"
        )
    except Exception as e:
        # Send the full error back to the user
        error_text = f"❌ *Generation Failed*\n\n`{str(e)}`"
        await update.message.reply_text(error_text, parse_mode="Markdown")
        logger.error(f"User-facing error: {e}")
    finally:
        await status_msg.delete()

# --- Web Server ---
async def main():
    app = Application.builder().token(TOKEN).updater(None).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    webhook_url = f"{URL}/telegram"
    await app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
    logger.info(f"Webhook set to {webhook_url}")

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
    config = uvicorn.Config(starlette_app, host="0.0.0.0", port=PORT, log_level="info")
    server = uvicorn.Server(config)

    async with app:
        await app.start()
        await server.serve()
        await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
