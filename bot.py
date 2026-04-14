import os
import io
import asyncio
import logging
import aiohttp
from openai import AsyncOpenAI, APIError, AuthenticationError, RateLimitError

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import Response, PlainTextResponse
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction

# --- Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

TOKEN = os.environ["BOT_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
URL = os.environ.get("RENDER_EXTERNAL_URL")
PORT = int(os.environ.get("PORT", 8000))

# Initialize OpenAI client
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# --- AI Image Generation with Robust Error Handling ---
async def generate_dalle_image(prompt: str) -> bytes:
    """
    Generates an image using DALL·E 3 and returns the image bytes.
    Handles API errors, rate limits, and download timeouts gracefully.
    """
    try:
        logger.info(f"Requesting DALL·E 3 generation for prompt: {prompt[:50]}...")
        response = await client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",          # Options: 1024x1024, 1792x1024, 1024x1792
            quality="standard",        # "hd" costs more but higher detail
            style="vivid",             # "natural" for more realistic, less stylized
            n=1,
        )
    except AuthenticationError:
        raise Exception("🔑 Invalid OpenAI API key. Check your OPENAI_API_KEY environment variable.")
    except RateLimitError:
        raise Exception("⏳ OpenAI rate limit exceeded. Please wait a moment and try again.")
    except APIError as e:
        if "billing" in str(e).lower() or "quota" in str(e).lower():
            raise Exception("💳 OpenAI billing issue. Check your credits at platform.openai.com/usage.")
        elif "safety" in str(e).lower():
            raise Exception("🛡️ Content policy violation. Please rephrase your prompt.")
        else:
            raise Exception(f"OpenAI API error: {e}")
    except Exception as e:
        raise Exception(f"OpenAI request failed: {e}")

    image_url = response.data[0].url
    logger.info(f"DALL·E generated image URL: {image_url}")

    # Download image with a 30-second timeout
    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(image_url) as resp:
                if resp.status == 200:
                    image_bytes = await resp.read()
                    logger.info(f"Downloaded image, size: {len(image_bytes)} bytes")
                    return image_bytes
                else:
                    text = await resp.text()
                    raise Exception(f"Image download failed with status {resp.status}: {text[:200]}")
    except asyncio.TimeoutError:
        raise Exception("⌛ Image download timed out. Please try again.")
    except Exception as e:
        raise Exception(f"Download error: {e}")

# --- Telegram Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message when /start is issued."""
    await update.message.reply_text(
        "🎨 *AI Thumbnail Generator*\n\n"
        "Send me a detailed description, and I'll create a custom image using DALL·E 3.\n\n"
        "💡 *Example*: `A serene Japanese garden with a red bridge over a koi pond, cherry blossoms falling, soft morning light`\n\n"
        "⏳ Generation takes ~10–20 seconds.",
        parse_mode="Markdown"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes user text and replies with an AI-generated image."""
    prompt = update.message.text.strip()
    if not prompt:
        await update.message.reply_text("Please send a description for the image.")
        return

    # Send a "working" status message
    status_msg = await update.message.reply_text("✨ Generating your image with AI... Please wait.")

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
        logger.info(f"Successfully sent DALL·E image for prompt: {prompt[:50]}...")

    except Exception as e:
        error_message = str(e)
        logger.error(f"Generation failed: {error_message}")
        # Send user-friendly error message
        await update.message.reply_text(
            f"❌ {error_message}\n\n"
            "If the issue persists, try a shorter or different prompt."
        )
    finally:
        # Clean up status message
        await status_msg.delete()

# --- Web Server for Telegram Webhooks ---
async def main():
    """Initializes the bot, sets webhook, and starts the web server."""
    # Build the PTB application
    app = Application.builder().token(TOKEN).updater(None).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Set the webhook URL
    webhook_url = f"{URL}/telegram"
    await app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
    logger.info(f"Webhook set to: {webhook_url}")

    # Define HTTP endpoints for Starlette
    async def telegram(request: Request) -> Response:
        """Receives updates from Telegram and puts them into the PTB queue."""
        await app.update_queue.put(Update.de_json(await request.json(), app.bot))
        return Response()

    async def health(_: Request) -> PlainTextResponse:
        """Health check endpoint for Render."""
        return PlainTextResponse("OK")

    starlette_app = Starlette(routes=[
        Route("/telegram", telegram, methods=["POST"]),
        Route("/healthcheck", health, methods=["GET"]),
    ])

    # Run the web server (Uvicorn) and the bot concurrently
    import uvicorn
    config = uvicorn.Config(starlette_app, host="0.0.0.0", port=PORT, log_level="info")
    web_server = uvicorn.Server(config)

    async with app:
        await app.start()
        await web_server.serve()
        await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
