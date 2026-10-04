# Troubleshooting

## האתר לא נפתח

`docker compose ps` ובדיקת `web`. פורט ברירת המחדל הוא 3000, אלא אם `WEB_PORT` שונה. הפרוקסי של XCloud צריך להצביע לפורט הזה.

## API לא נהיה בריא

`docker compose logs api`. כשל נפוץ: `.env` עם `TRADING_MODE` שאינו `PAPER`, `PAPER_ONLY` שאינו `true`, או `ALPACA_BASE_URL` של Live. שלושתם מפילים את העלייה בכוונה.

`APP_ENV=production` גם ייכשל, כי מצב זה דורש PostgreSQL. להשאיר `local`.

## worker יוצא מיד

אם כבר רץ worker על אותו volume, השני יוצא עם הודעה שהוא לא קיבל את הנעילה. לא להריץ עותק שני. `docker compose up -d` מגדיר עותק אחד.

## אין קניות

זה צפוי כש-Theta חסום, כשהשוק סגור, כשאין מפתח AI, או כשאין חשבון Paper. `/ready` מציג את `blocked_reasons`. לא לשנות את זה לירוק בלי ציטוט ושרשרת אמיתיים.

## Theta מתוך Docker

`127.0.0.1` בתוך הקונטיינר אינו השרת המארח. להגדיר `THETADATA_BASE_URL` לכתובת המארח או המכונה שעליה רץ Theta Terminal.

## מסד

לא למחוק את ה-volume `beo_data`. לא להריץ פקודת איפוס. אם הקונטיינר עלה מחדש והפוזיציות חסרות, לבדוק שה-volume עדיין מחובר ב-`docker volume ls`.

## דיסק מלא

לוגי Docker מוגבלים. אם הדיסק מלא בכל זאת: `docker system df` ואז ניקוי images ישנים. לא למחוק את `beo_data`.
