---
name: slack-notifier
description: Send concise ARGOS-related Slack notifications via an incoming webhook, including general ARGOS notices, errors, task results, follow-up items, and URL-heavy content that should not be read aloud. Use this skill when Slack notification is requested or when ARGOS should notify the user outside the current voice/chat flow.
---

# Slack Notifier

Use this skill to send a short Slack notification through an incoming webhook.

## When to Use

- The user asks to notify Slack.
- ARGOS needs to send a general notice, warning, error, or task result.
- The response contains URLs that are not useful to read aloud.
- The user wants a link, result, or short summary sent somewhere they can open later.

## Configuration

Read the webhook URL from:

```text
SLACK_WEBHOOK_URL
```

Do not print the webhook URL. If it is missing, ask the user to set it.

## Send a Notification

Use the bundled script:

```bash
python scripts/send_slack_notification.py --text "通知本文"
```

Optional fields:

```bash
python scripts/send_slack_notification.py \
  --kind link \
  --title "タイトル" \
  --text "短い説明" \
  --url "https://example.com"
```

Notification kinds:

- `info`: normal ARGOS notice
- `warning`: user attention needed
- `error`: failure or blocked task
- `task`: completed or scheduled task result
- `link`: URL or reference to open later

Keep Slack messages short. Put long details in the current chat or linked document, and send only the actionable summary plus URL.
