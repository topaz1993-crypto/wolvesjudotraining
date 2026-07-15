# Wolves Judo Bot — CLAUDE.md

> קובץ זה מתעד החלטות ארכיטקטורה חשובות. יש לעדכן אותו בכל שינוי משמעותי.

---

## ארכיטקטורת הבוט

- **Framework:** python-telegram-bot v21 + APScheduler (job_queue)
- **AI:** Anthropic Claude API (`claude-sonnet-4-6`)
- **Google Sheets:** OAuth pickle token (`GOOGLE_CREDS_B64` env var)
- **Google Calendar:** אותו OAuth — אם פג תוקף: `invalid_grant`. פתרון: הרצת auth script מקומי → base64 encode → עדכון `GOOGLE_CREDS_B64` ב-Render
- **Timezone:** `Asia/Jerusalem` (`IL_TZ = zoneinfo.ZoneInfo("Asia/Jerusalem")`)
- **שפה:** Python 3.12, async/await לכל ה-handlers

### קבצי נתונים (כולם ב-`/data`)
| קובץ | תוכן |
|------|------|
| `conversation_history.json` | היסטוריית Claude לכל משתמש (max 40) |
| `training_log.json` | יומן אימונים |
| `pending_plans.json` | תוכנית ממתינה לאישור לפני שמירה לגיליון |
| `wa_favorite_groups.json` | קבוצות ווטסאפ מועדפות |
| `wa_baileys_auth/` | WhatsApp session (שורד restarts) |
| `dropout_undo.json` | undo לפרישות |
| `recent_calendar_events.json` | cache של אירועי לוח שנה |
| `corrections.json` | הוראות/תיקונים שנוספו ע"י המשתמש ל-system prompt |
| `action_history.json` | היסטוריית פעולות לundo |

### משתני סביבה נדרשים
| משתנה | תיאור |
|--------|--------|
| `TELEGRAM_BOT_TOKEN` | טוקן הבוט |
| `ANTHROPIC_API_KEY` | מפתח Anthropic |
| `GOOGLE_CREDS_B64` | OAuth pickle base64-encoded |
| `WA_PORT` | פורט Node.js bridge (ברירת מחדל: 3000) |
| `WA_API_KEY` | מפתח הגנה ל-WA bridge (`wolves-wa-secret`) |

---

## פקודות הבוט — כל 39 הפקודות

### ניווט
| פקודה | תיאור |
|-------|--------|
| `/start` | אתחול הבוט, הצגת תפריט ראשי |
| `/menu` | הצגת תפריט ראשי |
| `/help` | עזרה + רשימת פקודות |
| `/myid` | הצגת Telegram ID של המשתמש |
| `/reset` | ביטול כל session פעיל |

### נוכחות
| פקודה | תיאור |
|-------|--------|
| דרך תפריט | סמן נוכחות לכל קבוצה לפי לוח |
| `/cleanup` | מחיקת עמודות ריקות בגיליונות נוכחות |
| `/design` | עיצוב מחדש של כל גיליונות הנוכחות |
| `/dropouts` | הצגת רשימת פורשים לפי סניף וקבוצה |
| `/dropout` | סימון ידני של סטודנט כפורש |
| `/student` | חיפוש מידע על סטודנט |
| `/deactivate` | סימון סטודנט כלא-פעיל (ללא מחיקה) |
| `/activate` | הפעלה מחדש של סטודנט |
| `/update_student` | עדכון פרטי סטודנט בגיליון |
| `/add_missing` | הוספת סטודנטים חסרים לאחר בדיקה |
| `/contacts` | ניהול אנשי קשר |
| `/contacts_import` | יבוא אנשי קשר מ-CSV |

### תוכניות אימון
| פקודה | תיאור |
|-------|--------|
| שליחת טקסט חופשי | זיהוי תוכנית אימון → הצעת שמירה לגיליון |
| `/edit` | עריכת תוכנית קיימת בגיליון |
| `/delete_plan` | מחיקת תוכנית מהגיליון |
| `/week_plan` | צפייה בתוכניות לשבוע שלם |
| `/archive` | ארכיון תוכניות ישנות |

### לוח שנה
| פקודה | תיאור |
|-------|--------|
| `/today` | לוח האימונים להיום |
| `/tomorrow` | לוח האימונים למחר |
| `/week` | תצוגת לוח שנה שבועית |
| `/month` | תצוגת לוח שנה חודשית |
| `/cal_test` | בדיקת חיבור לכל לוחות השנה |

### תשלומים
| פקודה | תיאור |
|-------|--------|
| `/payments` | סיכום תשלומים לפי חודש |
| `/unpaid` | רשימת סטודנטים שלא שילמו |
| `/email` | בדיקת מיילים לתשלומים חדשים מ-Invoice4u |
| `/email_debug` | debug לסנכרון מיילים |
| `/report` | דוח כספי מלא |
| `/message` | שליחת הודעה להורה של ספורטאי |
| `/registrations` | ניהול רישומים |

### אירועים
| פקודה | תיאור |
|-------|--------|
| `/camp` | ניהול רישומים למחנה קיץ |
| `/lyla` | ניהול לילה יפני (40 ילדים) |
| `/stats` | סטטיסטיקות אירוע (מחנה / לילה יפני) |

### ניהול בוט
| פקודה | תיאור |
|-------|--------|
| `/correction` | הוספת הוראה ל-system prompt של Claude |
| `/corrections` | הצגת כל ההוראות שנוספו |
| `/clear_history` | מחיקת היסטוריית שיחה עם Claude |

### WhatsApp
| פקודה | תיאור |
|-------|--------|
| `/wa_connect` | התחברות / סריקת QR (timeout 120 שניות) |
| `/wa_status` | מצב החיבור |
| `/wa_groups` | רשימת קבוצות ווטסאפ + שליחה |

---

## זרמי שיחה (sheets_sessions)

כל flow רב-שלבי מנוהל דרך `sheets_sessions[user_id]` — dict בזיכרון.

### 1. שמירת תוכנית אימון — רב-קבוצתי
```
שיחה חופשית עם Claude → זיהוי תוכנית →
step: "mg_pick_branch"  — המשתמש בוחר סניף
step: "mg_pick_date"    — המשתמש בוחר תאריך
→ שמירה ל-Google Sheets
```

### 2. עריכת תוכנית קיימת
```
/edit
step: "plan_edit_who"     — בחירת קבוצה
step: "plan_edit_group"   — (אם יש כמה)
step: "plan_edit_date"    — בחירת תאריך
step: "plan_edit_content" — שליחת תוכן חדש
→ עדכון גיליון
```

### 3. הוספת תלמיד חדש
```
(מתוך session נוכחות)
step: "first_name"  — שם פרטי
step: "last_name"   — שם משפחה
→ הוספה לגיליון + סימון ירוק היום
```

### 4. wizard חגורה
```
טקסט שמכיל "חגורה" / "טקס"
step: "belt_msg_details"
step: "belt_wizard_child_name"
step: "belt_wizard_belt_color"
step: "belt_wizard_ceremony_day"
→ יצירת אירוע לוח שנה
```

### 5. סנכרון תשלומים
```
/email → זיהוי Invoice4u
step: "payment_sync_*"
→ התאמה ידנית של תשלומים לתלמידים
→ כתיבה לגיליון
```

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

### סטטוסים אפשריים (`bridge_offline` = service לא רץ)
- `connected` — מחובר ✅
- `connecting` — מתחבר...
- `qr_ready` — ממתין לסריקת QR
- `logged_out` — נותק מהטלפון → צריך QR מחדש
- `bridge_offline` — Node.js service לא פועל → `/wa_connect`

### כללים חשובים
1. **אחרי כל deploy** — לבדוק שהחיבור פעיל (`/wa_status` בפועל)
2. **אין לומר "עובד" בלי אישור מהמשתמש** — בדיקת קוד בלבד לא מספיקה
3. **קונפליקט קוד** — כשמעדכנים bot.py לוודא שאין כפילויות ב-`wa_client`, `on_startup`, `cmd_wa_groups`
4. **memory issue:** WA service היה מופעל בסטארטאפ וגרם ל-timeout ב-Claude API — תוקן ע"י הפעלה ב-thread נפרד

---

## Deploy & Render

- **Platform:** Render background worker
- **Persistent disk:** `/data` — שורד restarts, deploys, ו-resets
- **Git push → auto-deploy** (בד"כ ~2-3 דקות)
- אחרי כל push: להמתין לRender ואז לבדוק `/wa_status` + לשלוח הודעה ולוודא תגובה

---

## גיליונות Google Sheets

### גיליון תוכניות אימון
**Spreadsheet ID:** `1hi073ueyzdzEjzhP6a3ZgTPpeZDNzH2g2rKPj-L8a6I`
**קובץ:** `training_plans.py`

#### טאבים (BRANCH_TABS)
| מפתח (בקוד) | שם טאב בגיליון |
|-------------|----------------|
| סירקין | סירקין |
| חגור | חגור |
| נווה ירק | נווה ירק |
| אהרונוביץ | אהרונוביץ |
| איפון פייט | איפון פייט |
| פונקציונלי | `פונקציונאלי ` (שימו לב: רווח בסוף!) |
| נבחרת | נבחרת |

#### מבנה הגיליון
- **עמודה A:** שעה — NAVY background
- **עמודה B:** קבוצה — NAVY background
- **עמודות C+:** תאריכים (D/M format) — צבע לפי סטטוס:
  - עבר: כחול כהה header + כחול בהיר cells
  - היום/אחרון: כתום חזק header + צהוב-קרם cells
  - עתיד: כתום בינוני header + קרם חם cells

#### שורות לכל קבוצה (ROW_TYPES)
כל בלוק קבוצה מכיל 6 שורות בסדר קבוע:
1. חימום
2. תרגול
3. קרבות
4. משחק
5. כוח
6. נוסף

**חשוב:** שמירה היא **פוזיציונלית** — פריט 1 → שורה 1 (חימום), פריט 2 → שורה 2 (תרגול), וכן הלאה. אין keyword matching.

---

### גיליונות נוכחות (BRANCH_SHEETS)
**קובץ:** `attendance.py`

| סניף | Spreadsheet ID | קבוצות |
|------|----------------|---------|
| סירקין | `1L0mcnpBPW4_3nsxaMy3EunQuOHPjWejvL1Wb6SGzltQ` | ד-ו, ג, א-ב, גן חובה, ז-בוגרים, איפון פייט ב-ד, איפון פייט ה-ז, נבחרת בוגרת, נבחרת צעירה |
| נווה ירק | `1_J1H0q4-RGy9rH0wyhwfv-47K-uKxiHtbI-D2RoVVOU` | גנים, ג-ו, א-ב |
| פונקציונלי | `1LYqia2ESkLY0HD8QA0vkg1xxqLI5qx0nY9CVVj5MGGY` | ז-ח, ט-י"ב |
| אהרונוביץ | `1MAN8_OnQRBeiznYMvGa57GHU-xz-MErgFkkNOV_Ms8E` | א-ה |
| חגור | `18p087VLNCRqPOhGbDzUeEg4YIHatiCfSc7v8NVFEPHA` | ד-ח, א-ג, גנים |

#### שמות טאבים שונים מהקוד (_SHEET_TAB_ALIASES)
| גיליון | שם בקוד | שם טאב בפועל בגיליון |
|--------|----------|----------------------|
| נווה ירק | ג-ו | ג-ז (עדיין לא שונה) |
| אהרונוביץ | א-ה | ג-ו |
| סירקין | גן חובה | גנים |

מנגנון: `_SHEET_TAB_ALIASES` ב-`attendance.py` ממפה `(spreadsheet_id, logical_name) → actual_tab_name`

#### מבנה גיליון נוכחות
- **שורה 1:** חודשים (ינואר, פברואר...) — ORANGE background
- **שורה 2:** תאריכים (1, 2, 3...) — NAVY header
- **עמודה A:** מספר תלמיד
- **עמודה B:** שם פרטי
- **עמודה C:** שם משפחה
- **עמודות D+:** נוכחות — **ירוק** = נכח, **אדום** = נעדר, **שחור** = פרש

#### טאב פורשים
שמות עמודות: שם | שם משפחה | קבוצה | תאריך הצטרפות | תאריך פרישה

---

### גיליון תשלומים
**Spreadsheet ID:** `1hzkQZhmtIPL2S11Z399OmJik3pqKyOQsFp33tTNij5o`
**קבועים:** `PAYMENTS_SHEET_ID` ב-`attendance.py`, `payments_sheet.py`, `invoice4u_sync.py`

#### טאב: תשלומים
- **עמודות A-E:** שם, שם משפחה, סוג מנוי, מועדון, גיל
- **עמודות F-P:** ספטמבר עד יולי (חודשי תשלום)

#### טאב: התחשבנות ג'ודו סירקין
- **עמודות A-D:** משפחה, פרטי, תחום, חוג
- **עמודות E+:** ספטמבר עד יולי + מחנה פסח

#### טאב: חגורות
עמודות: # | תאריך | שם | שם משפחה | גיל | צבע חגורה | מועדון | סכום (60₪) | מזומן/סליקה | חשבונית (✓)

---

### גיליון מחנה קיץ
**Spreadsheet ID:** `1lDULmVEYkbbASAdG2MKiozoV1gzsYQ_P-sw_CyilhyE`
**קובץ:** `camp_sheet.py` | **טאב:** `רשומים`

| עמודה | תוכן |
|-------|------|
| A | שם |
| B | כיתה |
| C | סניף |
| D | מידת חולצה |
| E | הערות (שבועיים / שבוע אחד / ...) |
| F | תשלום |
| G | צהרון |

---

### גיליון לילה יפני
**Spreadsheet ID:** `1srujIboIUR3D0WQ9z1tHB9_d7jxs3Heoqz2KlwGLbdA`
**קובץ:** `lyla_sheet.py` | **טאב:** `משתתפים`

| עמודה | תוכן |
|-------|------|
| A | # |
| B | שם |
| C | כיתה |
| D | מועדון |
| E | נוכחות |
| F | הערות |

- מיון אוטומטי לפי כיתה → שם
- צביעה לפי סניף (4 צבעים לפי מועדון)
- `add_from_csv()` — יבוא מ-Compete CSV בלי כפילויות

---

### גיליון תחרויות
**Spreadsheet ID:** `1SaUURPE3a2GgmYRtCTcr7zSUr_EbjeBFEYkk2Nwilow`
**קובץ:** `competitions_sheet.py`

עמודות: # | שם פרטי | שם משפחה | מועדון | שנתון | משקל | קרב 1-4 (🥇/🥈/🥉)

---

### לוג שיחות בוט
**קובץ:** `conversation_log.py` — נוצר אוטומטית ב-Google Drive
**עמודות:** תאריך | שעה | הודעת משתמש | תגובת הבוט | פעולה שבוצעה | הצלחה | הערות

---

## לוח שיעורים קבוע (WEEKLY_SCHEDULE)

> Python weekday: 0=שני, 1=שלישי, 2=רביעי, 3=חמישי, 4=שישי, 6=ראשון

| יום | סניף | קבוצה | שעה |
|-----|------|--------|-----|
| ראשון (6) | חגור | ד-ח | 15:15 |
| ראשון (6) | חגור | א-ג | 16:30 |
| ראשון (6) | חגור | גנים | 17:15 |
| שני (0) | סירקין | ד-ו | 14:30 |
| שני (0) | סירקין | ג | 15:30 |
| שני (0) | סירקין | א-ב | 16:30 |
| שני (0) | סירקין | ז-בוגרים | 18:00 |
| שלישי (1) | נווה ירק | גנים | 16:00 |
| שלישי (1) | נווה ירק | ג-ו | 16:45 |
| שלישי (1) | נווה ירק | א-ב | 17:45 |
| רביעי (2) | אהרונוביץ | א-ה | 13:50 |
| רביעי (2) | פונקציונלי | ז-ח | 16:15 |
| רביעי (2) | פונקציונלי | ט-י"ב | 17:15 |
| רביעי (2) | סירקין (איפון פייט) | ב-ד | 18:30 |
| רביעי (2) | סירקין (איפון פייט) | ה-ז | 19:15 |
| חמישי (3) | סירקין | ד-ו | 14:30 |
| חמישי (3) | סירקין | ג | 15:30 |
| חמישי (3) | סירקין | א-ב | 16:30 |
| חמישי (3) | סירקין | גן חובה | 17:15 |
| חמישי (3) | סירקין | ז-בוגרים | 18:00 |
| שישי (4) | פונקציונלי | ז-ח | 09:00 |
| שישי (4) | פונקציונלי | ט-י"ב | 10:00 |
| שישי (4) | סירקין | נבחרת צעירה | 13:15 |
| שישי (4) | סירקין | נבחרת בוגרת | 15:30 |

**הערה:** יום שני בסירקין — אין גן חובה (מוחרג מה-WEEKLY_SCHEDULE).

---

## PLAN_GROUPS — קבוצות לפי סניף (bot.py)

```python
PLAN_GROUPS = {
    "סירקין":     ["ד-ו", "ג", "א-ב", "גן חובה", "ז-בוגרים"],
    "נווה ירק":   ["גנים", "ג-ו", "א-ב"],
    "פונקציונלי": ["ז-ח", 'ט-י"ב'],
    "אהרונוביץ":  ["א-ה"],
    "חגור":       ["ד-ח", "א-ג", "גנים"],
    "איפון פייט": ["ב-ד", "ה-ז"],
    "נבחרת":      ["נבחרת"],
}
```

---

## החלטות ארכיטקטוניות חשובות

### 1. שמירת תוכניות — פוזיציונלית, לא keyword-based
`smart_map_items` ב-`training_plans.py` ממפה פריטים **לפי סדר** לשורות (חימום→תרגול→קרבות...).
לא מנסה לנחש איזה פריט שייך לאיזה סוג. כך נשמר הסדר שהמשתמש כתב.

### 2. הצגת תוכנית — ללא תוויות
`_plan_offer_save` מציג את התוכנית **ללא** פרפיקס "חימום:", "תרגול:" וכו'.
גם Claude מקבל הוראה לא לכתוב תוויות (ב-`system_prompt.txt`).
גם אם Claude כותב תוויות — נמחקות בקוד לפני תצוגה ולפני שמירה ב-`pending_plans`.

### 3. הגנה על היסטוריית Claude
`call_claude` מבצע rollback להיסטוריה אם ה-API נכשל.
לפני כל קריאה — מסנן הודעות consecutive עם אותו role (מונע BadRequestError).

### 4. זיהוי תוכנית אימון
תוכנית מזוהה אם יש ≥2 keywords מרשימת PLAN_STRUCTURE **או** header של קבוצה ("ד-ח:", "א-ג:" וכו').
NEGATIVE_SIGNALS מונעים זיהוי שגוי של שאלות על קוד.

### 5. זרימת pending_plans
Claude כותב תוכנית → נשמרת ב-`pending_plans.json` → המשתמש מאשר → נשמרת לגיליון.
כך אפשר לראות תצוגה מקדימה לפני כתיבה לגיליון.

### 6. WhatsApp — non-blocking startup
WA service מופעל ב-thread נפרד ב-`on_startup` כדי לא לחסום את הבוט בזמן אתחול.

### 7. Google OAuth — Render
ה-token נשמר כ-base64 pickle ב-env var. כשפג תוקף — צריך להריץ auth script מקומי וליצור token חדש.

### 8. Tab aliases
כשהשם הלוגי בקוד שונה מהשם הפיזי בגיליון — `_SHEET_TAB_ALIASES` ב-`attendance.py` מגשר.
מאפשר לשנות שמות בקוד בלי לשנות גיליונות קיימים.

---

## באגים שתוקנו עונה 2025-2026

### 1. נוכחות נווה ירק — שם קבוצה ג-ז (תוקן 01/07/2026)
**הבעיה:** שם הטאב בגיליון הוא `"ג-ז"` (ז=zayin) אבל הקוד כתב `"ג-ו"` (ו=vav). גרם ל-`Sheet 'ג-ו' not found`.
**מיקום:** `attendance.py → BRANCH_GROUPS`, `WEEKLY_SCHEDULE`; `bot.py → PLAN_GROUPS`.
**תיקון:** הוספת `_SHEET_TAB_ALIASES` כגיבוי. שם בקוד נשאר "ג-ו" (נכון לוגית), alias מגשר לגיליון.
**לקח:** תמיד לאמת שם טאב מול הגיליון בפועל לפני קידוד.

### 2. שגיאה בתקשורת עם Claude לאחר כתיבת תוכנית (תוקן 03/07/2026)
**הבעיה:** אחרי שמירת תוכנית — הודעות Claude API נכשלות עם `BadRequestError`.
**סיבה:** corruption בהיסטוריה — הודעת user נוספה לפני קריאת API, אם API נכשל assistant לא נוסף → consecutive user messages → שגיאה בקריאה הבאה.
**תיקון:** rollback להיסטוריה אם exception, + סינון consecutive same-role messages לפני כל קריאה ל-Claude.
**קובץ:** `bot.py → call_claude`

### 3. mg_pick_branch session — תוכנית נכנסת לClaude במקום להישמר (תוקן 03/07/2026)
**הבעיה:** כשהמשתמש היה ב-step `mg_pick_branch` ושלח תוכנית ג'ודו — לא היה handler → הטקסט עבר ל-Claude → API errors.
**תיקון:** הוספת handler ב-`handle_sheets_text` שמזהה keywords של תוכנית ומתחיל flow חדש.
**קובץ:** `bot.py → handle_sheets_text`

### 4. /cal_test קורס עם HTML parse error (תוקן 03/07/2026)
**הבעיה:** `parse_mode="HTML"` עם הודעות שגיאה שמכילות תווים מיוחדים → `Can't parse entities at byte offset 1085`.
**תיקון:** הסרת `parse_mode` מ-`cal_test_command` — שימוש ב-plain text בלבד.
**קובץ:** `bot.py → cal_test_command`

### 5. Claude כותב תוויות בתוכניות (תוקן 03/07/2026)
**הבעיה:** Claude כתב "חימום: ...", "תרגול: ..." גם אחרי הוראה ב-system_prompt.
**תיקון שלושה-שכבתי:**
1. `system_prompt.txt` — הוראה מפורשת לא לכתוב תוויות
2. `training_plans.py → _extract_items` — stripping תוויות בעת ניתוח
3. `bot.py → handle_message` — regex stripping לפני הצגה ולפני שמירה ב-pending_plans
**קובץ:** `bot.py`, `training_plans.py`, `system_prompt.txt`

### 6. זיהוי תוכנית ללא תוויות (תוקן 03/07/2026)
**הבעיה:** אחרי הסרת תוויות — הזיהוי של "יש כאן תוכנית אימון" לא עבד (חיפש "חימום:", "תרגול:" וכו').
**תיקון:** הוספת זיהוי לפי headers של קבוצות ("ד-ח:", "א-ג:" וכו') — מספיק header אחד.
**קובץ:** `bot.py → handle_message`

### 7. credits balance error מ-Anthropic
**לא באג בקוד** — חשבון Anthropic נגמרו הקרדיטים.
פתרון: הוספת credits ב-console.anthropic.com.
עכשיו השגיאה מוצגת עם `err_type` בפירוט: `❌ שגיאה בתקשורת עם Claude [BadRequestError]: ...`

---

## מה עדיין פתוח / לא הושלם

### א. טאב "ג-ז" בנווה ירק
הטאב בגיליון עדיין נקרא "ג-ז" — יש alias בקוד אבל עדיף לשנות שם בגיליון ל-"ג-ו" לעקביות.

### ב. OAuth Google Calendar — תהליך ידני
כשה-token פג (`invalid_grant` ב-`/cal_test`) — צריך:
1. להריץ `python3 auth_google.py` מקומית
2. לאמת דפדפן
3. לקחת `token.pickle` → `base64 -i token.pickle | pbcopy`
4. לעדכן `GOOGLE_CREDS_B64` ב-Render
אין אוטומציה לזה עדיין.

### ג. labels בתוכניות — טרם נאשר
הסרת התוויות תוקנה בקוד (commit `ef5c31e`), אבל **טרם נאשרה ע"י המשתמש** שהבעיה נפתרה בפועל.

### ד. conversation_log — sheet ID לא persistent
גיליון הלוג נוצר אוטומטית ב-Drive אבל ה-ID נשמר מחוץ ל-`/data` — לא שורד restarts ב-Render.

---

## מה מתוכנן לעונה הבאה (2026-2027)

> בקשות שעלו בשיחות — לא מומשו עדיין

1. **שמירת תוכנית בסדר חופשי** — המשתמש יוכל לסדר את הסעיפים איך שרוצה, לא חייב 6 שורות קבועות
2. **תצוגת תוכנית עשירה יותר** — preview עם קבוצות בנפרד לפני שמירה
3. **אוטומציה של OAuth** — token refresh אוטומטי ללא צורך בהתערבות ידנית
4. **שחזור מלא** — /undo לכל פעולה (כרגע קיים חלקית)
5. **ווטסאפ — שליחה קבוצתית** — שליחת תוכנית שבועית לכל קבוצות WhatsApp בלחיצה אחת
6. **דוחות נוכחות אוטומטיים** — סיכום חודשי אוטומטי בוואטסאפ / מייל
7. **monitor פורשים** — בדיקה שבועית אוטומטית (יש `dropout_detector.py` אבל לא מופעל שוטף)
