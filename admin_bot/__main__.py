# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Entry point for admin bot — app setup, handler registration, run_polling."""
import logging
from datetime import time as datetime_time, timezone

from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters,
)

from .config import TOKEN, ADMIN_USER_ID
from .helpers import _recover_inflight
from .commands import (
    cmd_status, cmd_restart, cmd_restart_bot, cmd_logs, cmd_digest,
    cmd_new, cmd_session, cmd_stop, cmd_domain,
    cmd_approve, cmd_reset_phase,
    cmd_q, cmd_scout, cmd_model, cmd_pull, cmd_version,
    cmd_health, cmd_cron, cmd_rerun, cmd_disk, cmd_panel,
    cmd_watchdog, cmd_homein, cmd_homeout,
    cmd_config, cmd_trends, cmd_usage, cmd_export, cmd_unsent, cmd_skills, cmd_menu,
    cmd_library, cmd_goals, cmd_redteamstart, cmd_redteamstop,
    cmd_redteamoffline, cmd_redteamofflinestop, cmd_redteameval,
    cmd_redteamgenerate, cmd_redteamofflinegen,
    cmd_autoreplyon, cmd_autoreplyoff, cmd_autolist,
    cmd_bg, cmd_bgkill,
    cmd_content, cmd_draft, cmd_checkpoint_view,
    cmd_sh, cmd_eli5,
)
from .bridge import claude_bridge
from .callbacks import (
    handle_stop, handle_retry, handle_commit_deploy,
    handle_review_action, handle_ai_learn, handle_model_select, handle_switch,
    handle_xvote, handle_dvote, handle_noop, handle_status_read, handle_panel,
    handle_restart_cmd, handle_skill, handle_menu, handle_model_switch,
    handle_tweetdraft,
)
from .voice import handle_voice
from .youtube import cmd_yt

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
log = logging.getLogger("admin")


def main():
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .build()
    )
    # Post-init: recover inflight + register command menu
    from telegram import BotCommand, MenuButtonCommands
    async def _post_init(application):
        await _recover_inflight(application)
        # Register heartbeat — writes .admin_heartbeat every 30s for watchdog
        from .schedulers import _health_check
        application.job_queue.run_repeating(_health_check, interval=30, first=5)
        # Set command menu sorted by usage
        from .usage_tracker import get_sorted_commands
        sorted_cmds = get_sorted_commands()
        await application.bot.set_my_commands([
            BotCommand(cmd, desc) for cmd, desc in sorted_cmds[:27]
        ])

        # Pin the menu button — shows command list directly in bottom-left
        await application.bot.set_chat_menu_button(
            chat_id=ADMIN_USER_ID,
            menu_button=MenuButtonCommands(),
        )

        # Re-sort the command menu every 6 hours based on usage stats
        async def _resort_menu(context):
            from .usage_tracker import get_sorted_commands as _get
            cmds = _get()
            await context.bot.set_my_commands([
                BotCommand(c, d) for c, d in cmds[:27]
            ])
        application.job_queue.run_repeating(_resort_menu, interval=21600, first=60)

        # Weekly usage report — Sundays at 10:00 HKT (02:00 UTC)
        from .schedulers import _weekly_usage_report
        application.job_queue.run_daily(
            _weekly_usage_report,
            time=datetime_time(hour=2, minute=0, tzinfo=timezone.utc),
            days=(6,),  # Sunday
        )

        # Stale goal reminders — daily at 10:00 HKT (02:00 UTC)
        from .cognitive import check_stale_goals
        from .config import PERSONAL_GROUP, _HEARTBEAT_THREAD

        async def _stale_goals_job(context):
            await check_stale_goals(context.bot, PERSONAL_GROUP, _HEARTBEAT_THREAD)

        application.job_queue.run_daily(
            _stale_goals_job,
            time=datetime_time(hour=2, minute=0, tzinfo=timezone.utc),
        )

        # Startup notification — alert Bernard that admin bot restarted
        import time as _t
        from .config import VERSION_STR
        try:
            await application.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text=f"🟧 Admin bot restarted ({VERSION_STR})\n<code>{_t.strftime('%H:%M:%S HKT', _t.gmtime(_t.time() + 8*3600))}</code>",
                parse_mode="HTML",
            )
        except Exception as _e:
            log.warning("Startup notification failed: %s", _e)
    app.post_init = _post_init

    # Post-shutdown: disconnect all SDK clients cleanly
    async def _post_shutdown(application):
        from .sdk_client import sdk_disconnect_all
        await sdk_disconnect_all()
    app.post_shutdown = _post_shutdown

    # Command handlers
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("restart", cmd_restart))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("session", cmd_session))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("domain", cmd_domain))
    app.add_handler(CommandHandler("scout", cmd_scout))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("resetphase", cmd_reset_phase))
    app.add_handler(CommandHandler("q", cmd_q))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("pull", cmd_pull))
    app.add_handler(CommandHandler("version", cmd_version))
    app.add_handler(CommandHandler("yt", cmd_yt))
    app.add_handler(CommandHandler("restart_daliu", cmd_restart_bot))
    app.add_handler(CommandHandler("restart_sbf", cmd_restart_bot))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("cron", cmd_cron))
    app.add_handler(CommandHandler("rerun", cmd_rerun))
    app.add_handler(CommandHandler("disk", cmd_disk))
    app.add_handler(CommandHandler("panel", cmd_panel))
    app.add_handler(CommandHandler("watchdog", cmd_watchdog))
    app.add_handler(CommandHandler("homein", cmd_homein))
    app.add_handler(CommandHandler("homeout", cmd_homeout))
    app.add_handler(CommandHandler("config", cmd_config))
    app.add_handler(CommandHandler("trends", cmd_trends))
    app.add_handler(CommandHandler("usage", cmd_usage))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("unsent", cmd_unsent))
    app.add_handler(CommandHandler("skills", cmd_skills))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("library", cmd_library))
    app.add_handler(CommandHandler("goals", cmd_goals))
    # Background tasks
    app.add_handler(CommandHandler("bg", cmd_bg))
    app.add_handler(CommandHandler("bgkill", cmd_bgkill))
    # Content pipeline (ops-guard-mcp lib)
    app.add_handler(CommandHandler("content", cmd_content))
    app.add_handler(CommandHandler("draft", cmd_draft))
    app.add_handler(CommandHandler("checkpoint", cmd_checkpoint_view))
    app.add_handler(CommandHandler("sh", cmd_sh))
    app.add_handler(CommandHandler("eli5", cmd_eli5))
    # Auto-reply controls (short + long names)
    app.add_handler(CommandHandler("autoon", cmd_autoreplyon))
    app.add_handler(CommandHandler("autooff", cmd_autoreplyoff))
    app.add_handler(CommandHandler("autoreplyon", cmd_autoreplyon))
    app.add_handler(CommandHandler("autoreplyoff", cmd_autoreplyoff))
    app.add_handler(CommandHandler("autolist", cmd_autolist))
    # Red team — short names
    app.add_handler(CommandHandler("redon", cmd_redteamoffline))        # offline (default, background)
    app.add_handler(CommandHandler("redoff", cmd_redteamofflinestop))   # stop offline
    app.add_handler(CommandHandler("redontg", cmd_redteamstart))       # live TG (sends real msgs)
    app.add_handler(CommandHandler("redofftg", cmd_redteamstop))       # stop live TG
    # Red team — evaluate + generate
    app.add_handler(CommandHandler("redteameval", cmd_redteameval))
    app.add_handler(CommandHandler("redteamgenerate", cmd_redteamgenerate))
    app.add_handler(CommandHandler("redteamofflinegen", cmd_redteamofflinegen))
    # Old verbose names as aliases
    app.add_handler(CommandHandler("redteamon", cmd_redteamstart))
    app.add_handler(CommandHandler("redteamoff", cmd_redteamstop))
    app.add_handler(CommandHandler("redteamstart", cmd_redteamstart))
    app.add_handler(CommandHandler("redteamstop", cmd_redteamstop))
    app.add_handler(CommandHandler("redteamofflineon", cmd_redteamoffline))
    app.add_handler(CommandHandler("redteamofflineoff", cmd_redteamofflinestop))
    app.add_handler(CommandHandler("redteamoffline", cmd_redteamoffline))
    app.add_handler(CommandHandler("redteamofflinestop", cmd_redteamofflinestop))

    # Callback query handlers
    app.add_handler(CallbackQueryHandler(handle_stop, pattern=r"^stop:"))
    app.add_handler(CallbackQueryHandler(handle_retry, pattern=r"^retry:"))
    app.add_handler(CallbackQueryHandler(handle_commit_deploy, pattern=r"^commit_"))
    app.add_handler(CallbackQueryHandler(handle_review_action, pattern=r"^review:"))
    app.add_handler(CallbackQueryHandler(handle_ai_learn, pattern=r"^evolve:"))
    app.add_handler(CallbackQueryHandler(handle_model_select, pattern=r"^model:"))
    app.add_handler(CallbackQueryHandler(handle_switch, pattern=r"^switch:"))
    app.add_handler(CallbackQueryHandler(handle_xvote, pattern=r"^x(up|dn):"))
    app.add_handler(CallbackQueryHandler(handle_dvote, pattern=r"^dvote:"))
    app.add_handler(CallbackQueryHandler(handle_restart_cmd, pattern=r"^restart_cmd:"))
    app.add_handler(CallbackQueryHandler(handle_tweetdraft, pattern=r"^tweetdraft:"))
    app.add_handler(CallbackQueryHandler(handle_panel, pattern=r"^panel:"))
    app.add_handler(CallbackQueryHandler(handle_status_read, pattern=r"^status_read$"))
    app.add_handler(CallbackQueryHandler(handle_noop, pattern=r"^noop$"))
    app.add_handler(CallbackQueryHandler(handle_skill, pattern=r"^skill:"))
    app.add_handler(CallbackQueryHandler(handle_menu, pattern=r"^menu:"))
    app.add_handler(CallbackQueryHandler(handle_model_switch, pattern=r"^model_switch:"))

    # Usage tracking — fires before command handlers (group=-1)
    async def _track_command(update, context):
        if update.message and update.message.text and update.message.text.startswith('/'):
            cmd = update.message.text.split()[0].lstrip('/').split('@')[0]
            from .usage_tracker import track_usage
            track_usage(cmd)
    app.add_handler(MessageHandler(filters.COMMAND, _track_command), group=-1)

    # Message handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, claude_bridge))
    app.add_handler(MessageHandler(filters.PHOTO, claude_bridge))
    app.add_handler(MessageHandler(filters.Document.ALL, claude_bridge))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE & filters.TEXT, claude_bridge))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))

    # Error handler — log ALL unhandled errors
    async def _error_handler(update, context):
        log.error("Unhandled error: %s", context.error, exc_info=context.error)
    app.add_error_handler(_error_handler)

    # Outreach admin commands (/outreach)
    try:
        from outreach.admin_commands import register_outreach_commands
        register_outreach_commands(app)
    except ImportError:
        pass

    from .config import VERSION_STR
    log.info("Admin bot %s starting...", VERSION_STR)
    app.run_polling(
        drop_pending_updates=True,
        bootstrap_retries=5,
        allowed_updates=["message", "callback_query", "edited_message"],
    )


if __name__ == "__main__":
    from pidlock import acquire_lock
    if not acquire_lock("admin_bot", kill_existing=False):
        print("Another admin_bot instance is running. Exiting.")
        import sys; sys.exit(1)
    main()
