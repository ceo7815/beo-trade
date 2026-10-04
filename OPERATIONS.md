# Operations

## בריאות

`GET /health` בודק את התהליך, את המסד, את נעילת ה-PAPER, ואת מדיניות הסיכון והיציאה. Docker משתמש בו. הוא לא טוען ספקים חיצוניים, כדי שלא יסומן חיבור שלא נבדק.

`GET /ready` עונה אם מותר להתחיל עבודה אוטונומית. `autonomous=BLOCKED` הוא מצב תקין כשהשוק סגור, כשאין פעימת worker, או כש-Theta עדיין `BLOCKED`. Theta לא מסומן `HEALTHY` מהנתיב הזה. OpenAI עם מפתח הוא `AUTH_ONLY`, לא מוכן, כל עוד לא רצה קריאת החלטה.

פעימות:

- `/app/data/worker_heartbeat.txt` בכל סיבוב
- `monitor_heartbeat.txt` אחרי מוניטור
- `reconcile_heartbeat.txt` אחרי התאמה

פעימה ישנה מ-180 שניות מסמנת את הרכיב כלא תקין. תהליך חי בלי פעימה נראה ב-`/ready`.

## עצירה

`SIGTERM` מפסיק כניסות חדשות, מריץ עוד מחזור מוניטור אחד, ויוצא. הפקודות שכבר נשלחו לא נשלחות שוב. מצב הפוזיציה נשאר במסד על ה-volume. עצירת כניסות מהממשק (`trading_halt.txt`) לא מכבה מכירה, סטופ, טריילינג, סגירת סשן, פקיעה, או סגירת הכל.

## כשל ספק

אין ציטוט, אין שרשרת, אין AI, אין ברוקר, או שהסשן סגור: אין BUY. ה-worker נשאר למעלה.

## שדרוג

`bash scripts/deploy.sh` מושך fast-forward, בונה, ומעלה. הוא לא מוחק volume ולא מריץ איפוס מסד.

## חזרה אחורה

```bash
bash scripts/rollback.sh <git-revision>
```

הקוד חוזר. המסד לא נמחק ולא מגולגל אחורה. `create_all` רק מוסיף טבלאות חסרות.

## לוגים

```bash
docker compose logs --tail 100 api
docker compose logs --tail 100 worker
docker compose logs --tail 100 web
```

הלוגים הם JSON עם זמן ושדה `service`. לא נכתבים אליהם מפתחות.
