import logging
import re
import os
import time
import tempfile
import asyncio
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ══════════════════════════════════════
#         ⭐ STAR TIKTOK SAVER ⭐
# ══════════════════════════════════════
BOT_TOKEN = "8099066772:AAG5QMnkwrHElkSrT_-Fd4N9A5a-AiwiGos"
BOT_NAME  = "⭐ Star TikTok Saver"
DIV       = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"

AUDIO_CD_SECONDS = 600  # 10 хвилин

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# { chat_id: { "show_description": bool, "spoiler": bool, "title": str, "owner_id": int } }
group_settings: dict[int, dict] = {}

# Зберігаємо інфо для кнопки аудіо
# Ключ: "audio_{chat_id}_{message_id}" -> { "url": str, "requester_id": int, "requested_at": float }
audio_requests: dict[str, dict] = {}

# КД для завантаження аудіо: { user_id: timestamp }
audio_cooldowns: dict[int, float] = {}

TIKTOK_REGEX = re.compile(
    r"https?://(www\.)?(vm\.tiktok\.com|tiktok\.com|vt\.tiktok\.com)/[^\s]+"
)


# ──────────────────────────────────────
#  Утиліти
# ──────────────────────────────────────

def get_settings(chat_id: int, title: str = "", owner_id: int = 0) -> dict:
    if chat_id not in group_settings:
        group_settings[chat_id] = {
            "show_description": True,
            "spoiler": False,
            "title": title,
            "owner_id": owner_id,
        }
    else:
        if title:
            group_settings[chat_id]["title"] = title
        if owner_id:
            group_settings[chat_id]["owner_id"] = owner_id
    return group_settings[chat_id]


def settings_text(chat_title: str) -> str:
    return (
        BOT_NAME + "\n" + DIV + "\n\n"
        + "⚙️  <b>Налаштування групи</b>\n"
        + "💬  <b>" + chat_title + "</b>\n\n"
        + "Натисніть перемикач, щоб змінити параметр:"
    )


def build_settings_keyboard(chat_id: int, from_group: bool = False) -> InlineKeyboardMarkup:
    s = get_settings(chat_id)
    desc_icon  = "🟢" if s["show_description"] else "🔴"
    spoil_icon = "🟢" if s["spoiler"] else "🔴"
    rows = [
        [InlineKeyboardButton(
            desc_icon + "  Опис відео — " + ("увімкнено" if s["show_description"] else "вимкнено"),
            callback_data="toggle_desc:" + str(chat_id)
        )],
        [InlineKeyboardButton(
            spoil_icon + "  Спойлер — " + ("увімкнено" if s["spoiler"] else "вимкнено"),
            callback_data="toggle_spoiler:" + str(chat_id)
        )],
    ]
    if from_group:
        rows.append([InlineKeyboardButton("✖️  Закрити", callback_data="close_settings")])
    return InlineKeyboardMarkup(rows)


async def is_owner(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status == "creator"
    except Exception:
        return False


def download_tiktok_video(url: str) -> tuple:
    tmp_dir = tempfile.mkdtemp()
    output_path = os.path.join(tmp_dir, "video.mp4")
    ydl_opts = {
        "outtmpl": output_path,
        "format": "mp4/best[ext=mp4]/best",
        "quiet": False,
        "no_warnings": False,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            description = info.get("description") or info.get("title") or ""
            actual_path = output_path
            if not os.path.exists(actual_path):
                for f in os.listdir(tmp_dir):
                    actual_path = os.path.join(tmp_dir, f)
                    break
            return actual_path, description.strip()
    except Exception as e:
        logger.error("Помилка завантаження відео: " + str(e))
        return None, None


def download_tiktok_audio(url: str) -> str | None:
    import shutil
    tmp_dir = tempfile.mkdtemp()
    output_path = os.path.join(tmp_dir, "audio.%(ext)s")

    has_ffmpeg = shutil.which("ffmpeg") is not None
    logger.info("ffmpeg доступний: " + str(has_ffmpeg))

    ydl_opts = {
        "outtmpl": output_path,
        "format": "bestaudio/best",
        "quiet": False,
        "no_warnings": False,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
    }

    if has_ffmpeg:
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)

        files = os.listdir(tmp_dir)
        logger.info("Файли після завантаження: " + str(files))

        # Пріоритет: mp3 → m4a → aac → будь-який файл
        for ext in (".mp3", ".m4a", ".aac", ".ogg", ".webm"):
            for f in files:
                if f.endswith(ext):
                    return os.path.join(tmp_dir, f)
        for f in files:
            return os.path.join(tmp_dir, f)

        logger.error("Аудіо файл не знайдено у: " + tmp_dir)
        return None

    except Exception as e:
        logger.error("Помилка завантаження аудіо: " + str(e), exc_info=True)
        return None


# ──────────────────────────────────────
#  /start
# ──────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    user = update.effective_user
    user_id = user.id
    first_name = user.first_name or "друже"

    owned: list[tuple[int, str]] = []
    for chat_id, s in group_settings.items():
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            if member.status == "creator":
                owned.append((chat_id, s.get("title") or str(chat_id)))
        except Exception:
            pass

    if len(owned) == 1:
        chat_id, chat_title = owned[0]
        keyboard = build_settings_keyboard(chat_id, from_group=False)
        await update.message.reply_text(
            BOT_NAME + "\n" + DIV + "\n\n"
            + "👋  Вітаємо, <b>" + first_name + "</b>!\n\n"
            + "⚙️  <b>Налаштування групи</b>\n"
            + "💬  <b>" + chat_title + "</b>\n\n"
            + "Натисніть перемикач, щоб змінити параметр:",
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    elif len(owned) > 1:
        rows = []
        for chat_id, title in owned:
            rows.append([InlineKeyboardButton(
                "💬  " + title,
                callback_data="open_settings:" + str(chat_id)
            )])
        rows.append([InlineKeyboardButton(
            "➕  Додати до нової групи",
            url="https://t.me/share/url?url=Додайте мене до вашої групи!"
        )])
        await update.message.reply_text(
            BOT_NAME + "\n" + DIV + "\n\n"
            + "👋  Вітаємо, <b>" + first_name + "</b>!\n\n"
            + "📋  Бот активний у <b>" + str(len(owned)) + "</b> групах.\n"
            + "Оберіть групу для керування:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    else:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "➕  Додати до групи",
                url="https://t.me/share/url?url=Додайте мене до вашої групи!"
            )
        ]])
        await update.message.reply_text(
            BOT_NAME + "\n" + DIV + "\n\n"
            + "👋  Вітаємо, <b>" + first_name + "</b>!\n\n"
            + "Я автоматично завантажую відео з TikTok прямо у групу — "
            + "без реклами та водяних знаків.\n\n"
            + DIV + "\n\n"
            + "📥  Завантаження TikTok відео\n"
            + "📄  Опис відео під кожним постом\n"
            + "🫥  Приховування під спойлер\n"
            + "🎵  Завантаження аудіо окремою кнопкою\n"
            + "🔗  Посилання на оригінал\n\n"
            + DIV + "\n\n"
            + "<i>Додайте мене до групи як адміністратора, щоб розпочати.</i>",
            parse_mode="HTML",
            reply_markup=keyboard,
        )


# ──────────────────────────────────────
#  Бот доданий до групи
# ──────────────────────────────────────

async def bot_added_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            chat = update.effective_chat
            chat_id = chat.id
            chat_title = chat.title or "Група"

            owner_id = None
            try:
                admins = await context.bot.get_chat_administrators(chat_id)
                for admin in admins:
                    if admin.status == "creator":
                        owner_id = admin.user.id
                        break
            except Exception as e:
                logger.warning("Адміністратори: " + str(e))

            get_settings(chat_id, chat_title, owner_id or 0)

            if owner_id:
                try:
                    keyboard = build_settings_keyboard(chat_id, from_group=False)
                    await context.bot.send_message(
                        chat_id=owner_id,
                        text=(
                            BOT_NAME + "\n" + DIV + "\n\n"
                            + "🎉  <b>Бот успішно доданий!</b>\n\n"
                            + "💬  Група: <b>" + chat_title + "</b>\n\n"
                            + DIV + "\n\n"
                            + "Налаштуйте параметри під ваші потреби:"
                        ),
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                except Exception as e:
                    logger.warning("Не вдалося надіслати власнику: " + str(e))

            await update.message.reply_text(
                BOT_NAME + "\n" + DIV + "\n\n"
                + "🎵  Надсилайте посилання на TikTok — я миттєво завантажу відео!\n\n"
                + "<i>Власник керує налаштуваннями через</i> /settings",
                parse_mode="HTML",
            )


# ──────────────────────────────────────
#  /settings у групі
# ──────────────────────────────────────

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user_id = update.effective_user.id

    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("⚠️  Ця команда доступна лише в групах.")
        return

    chat_title = chat.title or "Група"
    get_settings(chat.id, chat_title)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "⚙️  Відкрити налаштування",
            callback_data="group_settings:" + str(chat.id)
        )
    ]])
    await update.message.reply_text(
        BOT_NAME + "\n" + DIV + "\n\n"
        + "⚙️  Налаштування групи <b>" + chat_title + "</b>\n\n"
        + "<i>Доступно лише для власника групи.</i>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ──────────────────────────────────────
#  Завантаження TikTok відео
# ──────────────────────────────────────

async def handle_tiktok_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return

    text = message.text or message.caption or ""
    match = TIKTOK_REGEX.search(text)
    if not match:
        return

    url = match.group(0)
    requester_id = update.effective_user.id
    logger.info("TikTok URL: " + url)

    s = get_settings(chat.id)

    status_msg = await message.reply_text(
        "⏳  <i>Завантажую відео...</i>",
        parse_mode="HTML",
    )

    loop = asyncio.get_event_loop()
    video_path, description = await loop.run_in_executor(None, download_tiktok_video, url)

    if not video_path or not os.path.exists(video_path):
        await status_msg.edit_text(
            "❌  <b>Не вдалося завантажити відео</b>\n\n"
            "<i>Відео може бути приватним або недоступним.</i>",
            parse_mode="HTML",
        )
        return

    source_line = "\n\n" + chr(0x1F517) + ' <a href="' + url + '">Source: Video Link</a>'

    if s["show_description"] and description:
        max_desc = 1024 - len(source_line)
        caption = chr(0x1F4C4) + " " + description[:max_desc] + source_line
    else:
        caption = source_line.strip()

    # Кнопка завантаження аудіо
    audio_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎵  Завантажити аудіо", callback_data="dl_audio:PLACEHOLDER")
    ]])

    try:
        with open(video_path, "rb") as video_file:
            sent = await context.bot.send_video(
                chat_id=chat.id,
                video=video_file,
                caption=caption,
                parse_mode="HTML",
                has_spoiler=s["spoiler"],
                supports_streaming=True,
                reply_markup=audio_keyboard,
            )

        # Зберігаємо інфо про запит
        key = "audio_" + str(chat.id) + "_" + str(sent.message_id)
        audio_requests[key] = {
            "url": url,
            "requester_id": requester_id,
            "requested_at": time.time(),
        }

        # Оновлюємо callback_data з реальним message_id
        real_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "🎵  Завантажити аудіо",
                callback_data="dl_audio:" + str(chat.id) + ":" + str(sent.message_id)
            )
        ]])
        await context.bot.edit_message_reply_markup(
            chat_id=chat.id,
            message_id=sent.message_id,
            reply_markup=real_keyboard,
        )

        await status_msg.delete()

    except Exception as e:
        logger.error("Помилка надсилання відео: " + str(e))
        await status_msg.edit_text(
            "❌  <b>Не вдалося надіслати відео</b>\n\n"
            "<i>Файл може бути завеликим — ліміт Telegram 50 МБ.</i>",
            parse_mode="HTML",
        )
    finally:
        try:
            os.remove(video_path)
            os.rmdir(os.path.dirname(video_path))
        except Exception:
            pass


# ──────────────────────────────────────
#  Обробка кнопок
# ──────────────────────────────────────

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id

    # ── Завантажити аудіо ──
    if data.startswith("dl_audio:"):
        parts = data.split(":")
        if len(parts) != 3:
            return
        try:
            chat_id = int(parts[1])
            msg_id  = int(parts[2])
        except ValueError:
            return

        key = "audio_" + str(chat_id) + "_" + str(msg_id)
        info = audio_requests.get(key)

        if not info:
            await query.answer("❌ Інформація про це відео вже недоступна.", show_alert=True)
            return

        # Перевірка — тільки та людина яка надіслала посилання
        if user_id != info["requester_id"]:
            await query.answer(
                "🚫 Завантажити аудіо може лише той, хто надіслав посилання.",
                show_alert=True
            )
            return

        # Перевірка КД
        now = time.time()
        last = audio_cooldowns.get(user_id, 0)
        remaining = AUDIO_CD_SECONDS - (now - last)
        if remaining > 0:
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            await query.answer(
                "⏳ Зачекайте ще " + str(mins) + " хв " + str(secs) + " сек перед наступним завантаженням.",
                show_alert=True
            )
            return

        # Ставимо КД одразу
        audio_cooldowns[user_id] = now

        url = info["url"]

        # Повідомлення про завантаження
        loading_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="⏳  <i>Завантажую аудіо...</i>",
            parse_mode="HTML",
        )

        loop = asyncio.get_event_loop()
        audio_path = await loop.run_in_executor(None, download_tiktok_audio, url)

        if not audio_path or not os.path.exists(audio_path):
            await loading_msg.edit_text(
                "❌  <b>Не вдалося завантажити аудіо</b>\n\n"
                "<i>Спробуйте пізніше.</i>",
                parse_mode="HTML",
            )
            # Знімаємо КД при помилці
            audio_cooldowns.pop(user_id, None)
            return

        try:
            with open(audio_path, "rb") as af:
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=af,
                    caption=chr(0x1F517) + ' <a href="' + url + '">Source: Video Link</a>',
                    parse_mode="HTML",
                )
            await loading_msg.delete()
        except Exception as e:
            logger.error("Помилка надсилання аудіо: " + str(e))
            await loading_msg.edit_text(
                "❌  <b>Не вдалося надіслати аудіо</b>\n\n"
                "<i>Файл може бути завеликим.</i>",
                parse_mode="HTML",
            )
            audio_cooldowns.pop(user_id, None)
        finally:
            try:
                os.remove(audio_path)
                os.rmdir(os.path.dirname(audio_path))
            except Exception:
                pass
        return

    # ── Закрити налаштування ──
    if data == "close_settings":
        try:
            await query.message.delete()
        except Exception:
            await query.edit_message_reply_markup(reply_markup=None)
        return

    # ── Кнопка "Налаштування" з групи ──
    if data.startswith("group_settings:"):
        try:
            chat_id = int(data.split(":")[1])
        except ValueError:
            return

        if not await is_owner(user_id, chat_id, context):
            await query.answer("🔒 Тільки власник групи може відкрити налаштування.", show_alert=True)
            return

        try:
            chat_info = await context.bot.get_chat(chat_id)
            chat_title = chat_info.title or "Група"
        except Exception:
            chat_title = group_settings.get(chat_id, {}).get("title", "Група")

        get_settings(chat_id, chat_title)
        keyboard = build_settings_keyboard(chat_id, from_group=True)
        await query.edit_message_text(
            settings_text(chat_title),
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    # ── Вибір групи зі списку (особисті) ──
    if data.startswith("open_settings:"):
        try:
            chat_id = int(data.split(":")[1])
        except ValueError:
            return

        if not await is_owner(user_id, chat_id, context):
            await query.answer("🔒 Тільки власник групи може відкрити налаштування.", show_alert=True)
            return

        try:
            chat_info = await context.bot.get_chat(chat_id)
            chat_title = chat_info.title or "Група"
        except Exception:
            chat_title = group_settings.get(chat_id, {}).get("title", "Група")

        get_settings(chat_id, chat_title)
        keyboard = build_settings_keyboard(chat_id, from_group=False)
        await query.edit_message_text(
            settings_text(chat_title),
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    # ── Перемикачі налаштувань ──
    if data.startswith("toggle_"):
        try:
            action, chat_id_str = data.split(":")
            chat_id = int(chat_id_str)
        except ValueError:
            return

        if not await is_owner(user_id, chat_id, context):
            await query.answer("🔒 Тільки власник може змінювати налаштування.", show_alert=True)
            return

        s = get_settings(chat_id)

        if action == "toggle_desc":
            s["show_description"] = not s["show_description"]
            await query.answer(
                "📄 Опис — " + ("увімкнено ✅" if s["show_description"] else "вимкнено 🔴")
            )
        elif action == "toggle_spoiler":
            s["spoiler"] = not s["spoiler"]
            await query.answer(
                "🫥 Спойлер — " + ("увімкнено ✅" if s["spoiler"] else "вимкнено 🔴")
            )

        try:
            chat_info = await context.bot.get_chat(chat_id)
            chat_title = chat_info.title or "Група"
        except Exception:
            chat_title = s.get("title", "Група")

        # Зберігаємо тип (з групи чи особисті) по наявності кнопки "Закрити"
        from_group = False
        if query.message.reply_markup:
            for row in query.message.reply_markup.inline_keyboard:
                for btn in row:
                    if btn.callback_data == "close_settings":
                        from_group = True

        keyboard = build_settings_keyboard(chat_id, from_group=from_group)
        await query.edit_message_text(
            settings_text(chat_title),
            parse_mode="HTML",
            reply_markup=keyboard,
        )


# ──────────────────────────────────────
#  Запуск
# ──────────────────────────────────────

async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start", "⭐ Головне меню"),
        BotCommand("settings", "⚙️ Налаштування групи"),
    ])


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS, bot_added_to_group
    ))
    app.add_handler(MessageHandler(
        filters.TEXT & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
        handle_tiktok_link,
    ))
    app.add_handler(CallbackQueryHandler(button_callback))

    logger.info("⭐ Star TikTok Saver запущено!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
