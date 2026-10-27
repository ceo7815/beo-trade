# Beo-Trade על XCloud

החבילה מריצה את הדסק בדפדפן, את ה-API, ו-worker אחד. ה-worker הוא גם הסורק, גם מוניטור הפוזיציות, גם ההתאמה מול Alpaca Paper, וגם לוח הזמנים. אין קונטיינר שני לאותו תפקיד, כדי שלא יישלחו שתי פקודות על אותה החלטה.

מצב המסחר נעול ל-`PAPER`. כתובת Live של Alpaca מפילה את העלייה.

## פקודות על השרת

```bash
git clone https://github.com/ceo7815/beo-trade.git
cd beo-trade
sudo bash scripts/bootstrap-server.sh
cp .env.example .env
# לערוך את .env על השרת. לא להעלות אותו ל-GitHub.
bash scripts/deploy.sh
```

`bootstrap-server.sh` מיועד ל-Ubuntu או Debian, ורק אם Docker עדיין לא מותקן. הוא לא מחליף הגדרות Docker קיימות.

הפורט הציבורי היחיד הוא פורט האתר, ברירת מחדל `3000`. ה-API נשאר ברשת הפנימית `beo_trade`. XCloud מנתב HTTPS אל פורט זה.

## מה ממלאים לפני Deploy Now

בקובץ הסביבה של XCloud, לא בגיט. בלי החמישה הראשונים ה-API לא מחזיר נתונים (503 עד שמגדירים מפתח, 401 אחר כך):

- `API_ADMIN_KEY`
- `API_READ_KEY`
- `DASHBOARD_USER`
- `DASHBOARD_PASSWORD`
- `SESSION_SECRET`
- `THETADATA_CLIENT_KEY` — מזהה הלקוח לבדיקת הזהות, בלי ערך בקוד

אחרי תשלום, באותו קובץ:

- `THETADATA_BASE_URL` — הכתובת של המכונה שמריצה את Theta Terminal, לא `127.0.0.1` שבתוך הקונטיינר
- `THETADATA_API_KEY`
- `BENZINGA_API_KEY`
- `OPENAI_API_KEY`
- מפתחות Alpaca Paper

אחר כך Deploy Now (או `bash scripts/deploy.sh` על שרת משלכם). אין צורך בשינוי קוד. בדיקת הסגירה אחרי העלייה: `curl` אנונימי אל `/backend/api/v1/report/trades` מחזיר 401. הפירוט ב-`docs/RUNBOOK.md`.

Theta Terminal רץ כשירות `theta` על אותו שרת, עם Java 21, על פורט 25503 בתוך הרשת הפנימית. אחרי תשלום מוסיפים רק `THETADATA_API_KEY` ב-xCloud ופורסים מחדש. הכתובת הפנימית היא `http://theta:25503`. לא משתמשים ב-`127.0.0.1`.

בדיקת הספקים, בלי שליחת פקודת מסחר:

```bash
./activate-providers.sh
```

כל עוד Theta לא החזיר ציטוט ושרשרת, `/ready` נשאר `BLOCKED` וה-worker לא קונה.
