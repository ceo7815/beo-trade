# אבטחה

מפתחות נשארים במשתני סביבה. `.env` לא נכנס ל-Git.

ב-`APP_ENV=production` השרת לא עולה בלי `AUTH_REQUIRED=true`, מסד Postgres, ו-`SUPABASE_JWT_SECRET`. טבלאות לא נוצרות אוטומטית בפרודקשן.

הטוקן נבדק מול סוד ה-JWT. הרשאה לא נשענת על `user_metadata`.

יש כותרות אבטחה ומגבלת קצב. שגיאות קלט נדחות. כל BUY וכל עסקת דמה נכתבים ל-`audit_logs`.
