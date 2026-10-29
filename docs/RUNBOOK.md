# הפעלה

מהתיקייה `backend`:

```
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
python -m app.workers.runner
```

מהתיקייה `frontend`:

```
npm install
npm run dev
```

הדשבורד: `http://localhost:3000`.

בדיקות:

```
python -m pytest backend/tests -q
```

לשרת XCloud, מתוך שורש הפרויקט:

```
docker compose up --build
```

ב-xCloud: Custom Docker, Docker Compose From Git, שם הקובץ `docker-compose.yml`, פורט ראשי `3000`. מפעילים Environment File ומדביקים את הערכים מ-`.env.example`. את המפתחות ממלאים שם, לא בגיט, ואז Deploy Now. דחיפה ל-Git לא מעדכנת את האתר.

## מפתחות גישה ב-XCloud

ה-API סגור כברירת מחדל. בלי `API_ADMIN_KEY` כל נתיב פרט ל-`/health` ו-`/ready` מחזיר 503: אין נתונים ואין פעולה. כשהמפתח מוגדר, בקשה בלי מפתח מקבלת 401, ומפתח קריאה על נתיב אסור או על כתיבה מקבל 403. `?key=` לא מתקבל.

בקובץ הסביבה של XCloud חייבים להיות כל אלה, ואז Deploy Now:

- `API_ADMIN_KEY` — מחרוזת ארוכה אקראית. הדשבורד מוסיף אותה רק אחרי התחברות תקינה.
- `API_READ_KEY` — מחרוזת ארוכה אקראית. קריאה בלבד לדוחות ולעסקאות, למשל למוניטור חיצוני.
- `DASHBOARD_USER`
- `DASHBOARD_PASSWORD`
- `SESSION_SECRET` — מחרוזת ארוכה אקראית שחותמת את עוגיית ההתחברות ל-30 יום.
- `THETADATA_CLIENT_KEY` — מזהה הלקוח של ThetaData לבדיקת הזהות (`TD-TERMINAL-KEY`). אין ערך בקוד. אם המפתח הישן פורסם, מחליפים אותו אצל ThetaData ומכניסים את החדש רק כאן.

בלי `DASHBOARD_USER`, `DASHBOARD_PASSWORD` ו-`SESSION_SECRET` ההתחברות לא מוגדרת, והפרוקסי לא מדביק את מפתח הניהול. בקשה שכבר נושאת `X-Beo-Key` או `Authorization: Bearer` עוברת כמו שהיא, בלי להחליף אותה במפתח הניהול.

ה-worker, מנוע הנייר, והיציאות המתוזמנות רצים בתוך התהליך. הם לא קוראים ל-API דרך הפרוקסי ולא תלויים ב-API פתוח.

אחרי Deploy Now, בלי עוגייה ובלי מפתח:

```
curl -s -o /dev/null -w "%{http_code}\n" https://beo-trade.1wp.site/backend/api/v1/report/trades
curl -s -o /dev/null -w "%{http_code}\n" https://beo-trade.1wp.site/backend/api/v1/learning/trades
```

התשובה הצפויה היא 401. 503 אומר שהמפתחות לא נטענו לקונטיינר של ה-API. 200 אומר שהסגירה לא עלתה.

`APP_ENV` נשאר `local`. מסד הריצה הוא SQLite על ה-volume `beo_data`, לא Supabase. `TRADING_MODE` נשאר `PAPER`. Redis עולה עם המערכת ומקבל `redis://redis:6379/0`. בלי מפתחות ספק אין נתוני שוק ואין BUY. בלי מחירי טוקנים אין קריאות AI.

ספי הסריקה יושבים ב-`backend/app/config/trading.toml`. שעות הבורסה והחגים יושבים ב-`backend/app/config/market_calendar.json`.

קצב כניסות (`entry_pace_enabled`, ברירת מחדל דולק, גרסה `v10.29-pacing`): לכל היותר 2 כניסות חדשות בחלון של 5 דקות, בלי ערבוב CALL ו-PUT באותו חלון, ואחרי סגירה בהפסד הצד ההפוך חסום ל-30 דקות. כיבוי: `entry_pace_enabled = false` ב-`trading.toml` ואז Restart. לא נוגע בסטופ, בטרייל, במתג, ברצפת היום, או בגודל.
