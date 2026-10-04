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

ב-xCloud: Custom Docker, Docker Compose From Git, שם הקובץ `docker-compose.yml`, פורט ראשי `3000`. מפעילים Environment File ומדביקים את הערכים מ-`.env.example`. את המפתחות ממלאים שם, לא בגיט.

`APP_ENV` נשאר `local`. מסד הריצה הוא SQLite על ה-volume `beo_data`, לא Supabase. `TRADING_MODE` נשאר `PAPER`. Redis עולה עם המערכת ומקבל `redis://redis:6379/0`. בלי מפתחות ספק אין נתוני שוק ואין BUY. בלי מחירי טוקנים אין קריאות AI.

ספי הסריקה יושבים ב-`backend/app/config/trading.toml`. שעות הבורסה והחגים יושבים ב-`backend/app/config/market_calendar.json`.
