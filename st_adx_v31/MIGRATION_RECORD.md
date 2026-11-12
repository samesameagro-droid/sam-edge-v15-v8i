# Migration record

Primary GitHub Actions runner switched from SAM EDGE V15 to ST-ADX V3.1 PAPER ONLY. SAM EDGE source, 200-trade journal, summaries, and old workflow are preserved. Existing workflow file path and workflow_dispatch interface remain for cron-job.org. Existing TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID repository Secrets are reused by name; no token values were read or changed. ST-ADX paper state is separate, with 100,000 USDT simulated starting equity. No private exchange endpoint or order API is used.
