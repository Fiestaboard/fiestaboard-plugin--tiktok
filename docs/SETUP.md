# TikTok Setup Guide

Connect FiestaBoard to your TikTok account to show your follower, like and video counts.

## Overview

**What it does:**

- Shows your TikTok display name, followers, following, total likes and video count
- Shortens big numbers (`16K`, `1.2M`) so they fit the board

**Prerequisites:**

- A TikTok account
- About 5 minutes, once, to create your own free app on the [TikTok for Developers](https://developers.tiktok.com/apps/) site. Sandbox mode is enough; TikTok doesn't need to review it
- FiestaBoard 9.9.0 or later (the plugin's settings have an **Account connection** section)

FiestaBoard only reads your profile name and counts. It can't post, comment, or change anything in your account.

## Quick Setup

### 1. Enable the Plugin

In the FiestaBoard web UI:

1. Go to **Integrations**
2. Find **TikTok** and toggle it **On**
3. Click **Configure**. The **Account connection** section at the top lists the steps below and shows your **redirect URI** with a copy button.

### 2. Create a TikTok app

1. Go to [TikTok for Developers](https://developers.tiktok.com/apps/), log in, and press **Connect an app** (or **Create app**). Fill in the app name (anything, such as `FiestaBoard Jane`) and pick an organization or individual account if asked.
2. At the top of the app page, switch from **Production** to **Sandbox**, press **Create Sandbox**, and give it a name. Everything below happens in the sandbox.
3. Under **Products**, add **Login Kit**. In its settings, under **Redirect URI** for **Web**, paste the redirect URI you copied from FiestaBoard's **Account connection** section. Copy it from there rather than typing it; TikTok compares it exactly.
4. Under **Scopes**, make sure **user.info.basic** and **user.info.stats** are added.
5. Under **Sandbox settings** > **Target users**, add your own TikTok account. A sandbox app only lets its target users sign in (up to 10). TikTok says this can take up to an hour to apply.
6. Press **Apply changes** (or **Save**), then copy the **Client key** and **Client secret** from the top of the app page.

### 3. Sign in

1. Back in the plugin's **Account connection** section, paste the **Client Key** and **Client Secret**
2. Press **Sign in with TikTok**. It saves both and opens TikTok's sign-in page
3. Log in with the TikTok account you added as a target user and press **Authorize**. TikTok asks to let your app read your profile info and stats
4. You come back to FiestaBoard and the section shows **Connected**

Optional settings:

- **Refresh Interval:** how often to check TikTok, 300 to 86400 seconds (default 900, every 15 minutes)

More about what you see while connecting: [Connecting Accounts](https://fiestaboard.app/docs/features/connecting-accounts).

### 4. Create a Board Template

Create a page from the plugin's demo, or add variables to your own page:

```jinja
{63} TIKTOK {63}
{{tiktok.display_name}}
FOLLOWERS {{tiktok.followers_short}}
LIKES {{tiktok.likes_short}}
VIDEOS {{tiktok.videos_short}}
FOLLOWING {{tiktok.following_short}}
```

On a Note, try:

```jinja
{63} {{tiktok.display_name}}
{{tiktok.followers_line}}
{{tiktok.likes_line}}
```

### 5. View

Open the page on your board. It updates every refresh interval (15 minutes by default).

## Template Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `{{tiktok.display_name}}` | Your display name | `PIXEL OWL` |
| `{{tiktok.followers}}` / `{{tiktok.followers_short}}` | Followers | `15600` / `16K` |
| `{{tiktok.following}}` / `{{tiktok.following_short}}` | Accounts you follow | `321` / `321` |
| `{{tiktok.likes}}` / `{{tiktok.likes_short}}` | Total likes on your videos | `1234567` / `1.2M` |
| `{{tiktok.videos}}` / `{{tiktok.videos_short}}` | Videos posted | `87` / `87` |
| `{{tiktok.followers_line}}` | Followers with a label | `FOLLOWERS 16K` |
| `{{tiktok.likes_line}}` | Likes with a label | `LIKES 1.2M` |
| `{{tiktok.summary}}` | Followers and likes on one line | `16K FANS 1.2M LIKES` |

## Configuration Reference

| Setting | Required | Default | Description |
|---------|----------|---------|-------------|
| Client Key | To sign in | | The **Client key** from your TikTok app's page. Entered in **Account connection** |
| Client Secret | To sign in | | The **Client secret** from the same page. Entered in **Account connection**; kept on your board and sent only to TikTok |
| Refresh Interval (seconds) | No | 900 | How often to check TikTok (300-86400) |

The connection itself is made with the **Sign in with TikTok** button, not a setting. The plugin asks TikTok for two read-only permissions: `user.info.basic` and `user.info.stats`.

**Environment variables:** none. The Client key and secret are entered in the plugin's settings.

## Troubleshooting

**"Not signed in to TikTok"**

- Press **Sign in with TikTok** (or **Reconnect**) in the plugin's **Account connection** section. If the button is greyed out, paste your Client Key and Client Secret first.
- **Reconnect needed** means TikTok stopped accepting the saved sign-in. A TikTok sign-in lasts a year; you also need to reconnect after removing the app's access in the TikTok app (**Settings and privacy** > **Security** > **Apps and services permissions**) or resetting the app's Client secret.

**TikTok shows an error instead of the sign-in page, or says the redirect URI doesn't match**

- The redirect URI in Login Kit must be exactly the one shown in FiestaBoard's **Account connection** section. Copy it again and paste it in, then press **Apply changes**.
- Check you are editing the **Sandbox**, not the Production version, of your app.

**TikTok says you can't authorize this app**

- The account you sign in with must be a **Target user** of the sandbox. Add it under **Sandbox settings**, then wait up to an hour and try again.

**"TikTok did not allow reading your stats"**

- Add the **user.info.stats** scope to your app (step 2.4), press **Apply changes**, then **Reconnect** and allow every permission TikTok lists.

**"TikTok rejected the sign-in (401)"**

- TikTok no longer accepts the saved sign-in. Press **Reconnect**.

**The sign-in fails with "invalid client" or "client_secret"**

- Paste the **Client key** and **Client secret** again from the top of your app's page. They are different for Sandbox and Production; use the Sandbox ones.

**"TikTok rate limit reached"**

- The plugin waits five minutes and keeps showing the last counts meanwhile (for up to an hour). Raise the refresh interval if it happens often.

**"This FiestaBoard version cannot sign in to TikTok"**

- Update FiestaBoard to 9.9.0 or later.

**Plugin shows "Not Available"**

- The error next to the plugin on the Integrations page says what went wrong. For more detail, check the logs: `docker compose logs -f`
