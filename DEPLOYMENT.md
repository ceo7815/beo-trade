# Deployment

## מה רץ

| שירות | תהליך | תפקיד |
| --- | --- | --- |
| migrate | `python -m app.ops.migrate` | בודק חיבור, יוצר טבלאות חסרות, יוצא |
| api | `uvicorn` | `/health`, `/ready`, וה-API |
| worker | `python -m app.workers.runner` | סריקה, מוניטור, התאמה, לוח זמנים, נעילה יחידה |
| web | `node server.js` | האתר. קורא ל-API דרך `/backend` |

אין Redis. אין קונטיינר מסד. מסד הריצה הוא SQLite על ה-volume `beo_data` בנתיב `/app/data`.

`APP_ENV=local` בכוונה. הערך `production` בקוד דורש PostgreSQL ודוחה את SQLite. לא להחליף אותו בפריסה הזו.

## סדר העלייה

1. `git pull`
2. `scripts/validate-env.sh`
3. בניית images
4. migrate
5. API עד ש-`/health` עונה
6. worker ו-web
7. פעימת worker
8. `scripts/smoke-test.sh`

`docker compose up -d` שומר על הסדר דרך `depends_on`.

## משתני סביבה

הקובץ המלא הוא `.env.example`. הסודות נשארים על השרת.

הסיכון והיציאה לא מגיעים ממשתני סביבה. הם בקובץ `backend/app/config/trading.toml` שבתוך ה-image: 2%, 7%, 10%, 10 פוזיציות, 35%, 15%, 10% סקטור, פוזיציה אחת לכל נכס, אזהרה 2.5%-, עצירת כניסות 4%-, עצירה קשה 5%-, סטופ 40%, הגנה ב-+1R ל-0.25R-, טריילינג ב-+1.5R, winner run ב-+2R, 15%, 90 דקות ל-0DTE, 180 דקות ל-1DTE. `TRADING_CONFIG_PATH` מפיל את העלייה.

## Theta

`THETADATA_BASE_URL` הוא הכתובת שהקוד קורא. אם הוא ריק ו-`THETA_TERMINAL_HOST` מלא, נבנית כתובת `http://HOST:PORT`. בתוך Docker, `127.0.0.1:25503` הוא הקונטיינר עצמו, לא הטרמינל, אלא אם הטרמינל רץ באותו network namespace. לא לחשוף את הטרמינל לאינטרנט הפתוח.

## רשת

רק `web` מפורסם לפורט `${WEB_PORT:-3000}`. אין פורט ציבורי למסד, ל-worker, או ל-API. הדפדפן לא מקבל מפתח של Alpaca, OpenAI, Benzinga, Theta, או service role.

## גיבוי

המצב הקריטי יושב על `beo_data`: `beo_trade.db`, `last_scan.json`, `trading_halt.txt`, ופעימות ה-worker. גיבוי:

```bash
docker compose stop worker
docker run --rm -v beo-trade_beo_data:/data -v "$PWD/backup":/backup alpine \
  tar czf "/backup/beo-data-$(date -u +%Y%m%dT%H%M%SZ).tgz" -C /data .
docker compose start worker
```

לא להעלות את קובץ הגיבוי ל-GitHub. Supabase אינו מסד הריצה של הפריסה הזו. קבצי `supabase/migrations` הם תיעוד סכמה, והפריסה לא מריצה אותם מול ענן.

## משאבים

מינימום: 2 vCPU, 4 GB RAM, 20 GB דיסק.  
נוח: 4 vCPU, 8 GB RAM, 40 GB דיסק.

הגבלות ב-Compose: API ו-worker עד 1 CPU ו-768 MB כל אחד, האתר עד 0.5 CPU ו-512 MB. לוגים מסתובבים ב-10 MB כפול 5 קבצים.

שעון השרת יכול להישאר UTC. לוגיקת השוק היא `America/New_York`. התצוגה היא `Asia/Dubai`.
