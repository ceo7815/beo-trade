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

## מה ממלאים אחרי תשלום

רק ב-`.env` על השרת:

- `THETADATA_BASE_URL` — הכתובת של המכונה שמריצה את Theta Terminal, לא `127.0.0.1` שבתוך הקונטיינר
- `BENZINGA_API_KEY`
- `OPENAI_API_KEY`
- מפתחות Alpaca Paper

אחר כך `bash scripts/deploy.sh`. אין צורך בשינוי קוד.

כל עוד Theta לא החזיר ציטוט ושרשרת, `/ready` נשאר `BLOCKED` וה-worker לא קונה.
