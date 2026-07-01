# Wolves Judo Bot — CLAUDE.md

> קובץ זה מתעד החלטות ארכיטקטורה חשובות. יש לעדכן אותו בכל שינוי משמעותי.

---

## WhatsApp Integration

### ארכיטקטורה
- **ספרייה:** `@whiskeysockets/baileys` v6.7.9 (WhatsApp Web protocol, ללא Chromium)
- **שפה:** Node.js 20 (ES module) — קובץ: `whatsapp_service/index.js`
- **גשר:** Express HTTP על פורט 3000, Python מדבר אליו דרך `wa_client.py`
- **Docker image:** `nikolaik/python-nodejs:python3.12-nodejs20` (Python + Node יחד)
- **Auth:** נשמר ב-`/data/wa_baileys_auth` (Render persistent disk — שורד restarts ו-deploys)
- **QR fallback:** נשמר ל-`/data/wa_qr.txt`

### נקודות קצה (Express)
| Method | Path | תיאור |
|--------|------|--------|
| GET | `/status` | `{connected, status, has_qr}` |
| GET | `/qr` | `{qr: base64PNG}` |
| POST | `/send` | `{to, message}` — phone או `@g.us` JID |
| GET | `/groups` | `{groups: [{id, name, size}]}` |
| POST | `/reconnect` | מוחק auth ומתחיל מחדש |

### הפעלה
- **WA service מופעל ב-`on_startup`** (thread daemon ברקע, non-blocking)
- אם ה-auth קיים → מתחבר אוטומטית ללא QR
- אם אין auth / נותק → צריך `/wa_connect` כדי לסרוק QR חדש

### קבוצות — סינון
- `WOLVES_KEYWORDS` מסנן לפי: `wolves, ג'ודו, גודו, וולבס, טופז, judo, איפון פייט, מועדון הג`
- מועדפים נשמרים ב-`/data/wa_favorite_groups.json`
- פקודה: `/wa_groups` — מציג קבוצות מסוננות + מועדפים
- פקודה: `/wa_groups [חיפוש]` — חיפוש ידני

### פקודות בוט
| פקודה | תיאור |
|-------|--------|
| `/wa_connect` | מחבר / סורק QR (120 שניות timeout) |
| `/wa_status` | מצב החיבור |
| `/wa_groups` | רשימת קבוצות + שליחה |
| `/message [שם]` | שולח לפרט (להורה של ספורטאי) |

### כללים חשובים
1. **אחרי כל deploy** — לבדוק שהחיבור פעיל (`/wa_status` בפועל)
2. **אין לומר "עובד" בלי אישור מהמשתמש** — בדיקת קוד בלבד לא מספיקה
3. **קונפליקט קוד** — כשמעדכנים bot.py לוודא שאין כפילויות ב-`wa_client`, `on_startup`, `cmd_wa_groups`
4. **memory issue:** WA service היה מופעל בסטארטאפ וגרם ל-timeout ב-Claude API — תוקן ע"י הפעלה ב-thread נפרד

### סטטוסים אפשריים (`bridge_offline` = service לא רץ)
- `connected` — מחובר ✅
- `connecting` — מתחבר...
- `qr_ready` — ממתין לסריקת QR
- `logged_out` — נותק מהטלפון → צריך QR מחדש
- `bridge_offline` — Node.js service לא פועל → `/wa_connect`

---

## Deploy & Render

- **Platform:** Render background worker
- **Persistent disk:** `/data` — שורד restarts, deploys, ו-resets
- **Git push → auto-deploy** (בד"כ ~2-3 דקות)
- אחרי כל push: להמתין לRender ואז לבדוק `/wa_status`

---

## Bot Architecture

- **Framework:** python-telegram-bot v21 + APScheduler (job_queue)
- **AI:** Anthropic Claude API (claude-sonnet)
- **Google Sheets:** OAuth pickle token (`GOOGLE_CREDS_B64` env var)
- **Timezone:** `Asia/Jerusalem` (`IL_TZ = zoneinfo.ZoneInfo("Asia/Jerusalem")`)
- **Data files** (כולם ב-`/data`):
  - `conversation_history.json` — היסטוריית Claude
  - `training_log.json` — יומן אימונים
  - `wa_favorite_groups.json` — קבוצות ווטסאפ מועדפות
  - `wa_baileys_auth/` — WhatsApp session


---

## באגים שתוקנו

### נוכחות נווה ירק — שם קבוצה ג-ז (תוקן 01/07/2026)
**הבעיה:** שם הטאב בגיליון Google Sheets הוא `"ג-ז"` (ז׳=zayin) אבל הקוד כתב `"ג-ו"` (ו׳=vav). גרם ל-`Sheet 'ג-ו' not found`.

**מיקום:** `attendance.py → BRANCH_GROUPS`, `WEEKLY_SCHEDULE`; `bot.py → PLAN_GROUPS`, belt wizard SCHED/SCHEDULE.

**לקח:** תמיד לאמת שם טאב מול הגיליון בפועל לפני קידוד. הוסף `_get_sheet_id` עם fuzzy matching כגיבוי.
