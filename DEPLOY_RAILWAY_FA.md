# استقرار آسان آرماندو روی Railway

## روش پیشنهادی: اتصال مخزن Git و Railpack خودکار

برای این ربات **Dockerfile یا دستور نصب دستی لازم نیست**. Railway با Railpack فایل‌های `requirements.txt` و `runtime.txt` را تشخیص می‌دهد و وابستگی‌ها را نصب می‌کند. `railpack.json` فرمان اجرا، Python provider و بسته‌های سیستمی لازم برای ffmpeg/OpenCV را تنظیم می‌کند.

آرماندو در حالت پیش‌فرض از **Long Polling** استفاده می‌کند؛ بنابراین یک Railway Service کافی است و به دامنه، وب‌هوک یا پورت عمومی نیازی ندارد.

## ۱) وصل‌کردن مخزن

1. فایل `Armando-Group-Manager-Railway.zip` را باز کنید و محتوای آن را در مخزن GitHub خصوصی قرار دهید؛ فایل‌های داخل ZIP را در ریشهٔ مخزن بگذارید، نه داخل یک پوشهٔ تودرتو.
2. در Railway یک Project بسازید و **Deploy from GitHub repo** را انتخاب کنید.
3. مخزن آرماندو و branch موردنظر (معمولاً `main`) را وصل کنید.
4. Railway به‌صورت پیش‌فرض از **Railpack** برای این پروژه استفاده می‌کند. در Build/Settings، Builder را روی Railpack نگه دارید؛ Dockerfile در ZIP نیست.
5. دستور Start از `railpack.json` می‌آید. اگر Railway در صفحهٔ تنظیمات Start Command را می‌خواهد، این را وارد کنید:

   ```text
   python -m app.main
   ```

6. تعداد replica را روی **۱** بگذارید؛ چند poller با یک Bot Token باعث `Conflict` می‌شود.

بعد از push هر commit به branch متصل، Railway build و deploy را خودکار اجرا می‌کند.

## ۲) پایگاه دادهٔ پایدار

برای نگه‌داشتن تنظیمات گروه‌ها بین Deployها، در همان Project یک **PostgreSQL** بسازید. سپس در Variables سرویس ربات، `DATABASE_URL` را به متغیر دیتابیس وصل کنید. اگر نام سرویس دیتابیس `Postgres` است، مقدار Railway reference چنین است:

```text
${{Postgres.DATABASE_URL}}
```

اگر سرویس نام دیگری دارد، نام واقعی همان سرویس را در reference بگذارید یا مقدار Connection URL را از بخش Variables کپی کنید. اتصال را در گفتگو یا مخزن قرار ندهید.

برای آزمایش کوتاه، SQLite پیش‌فرض نیز اجرا می‌شود؛ اما فایل محلی Railway را برای نگه‌داری پایدار داده‌ها فرض نکنید. برای Deploy ماندگار، PostgreSQL پیشنهاد می‌شود.

## ۳) تنظیم Variables

در سرویس ربات، تب **Variables** را باز کنید و حداقل این مقدارها را بسازید:

| نام | مقدار |
| --- | --- |
| `BOT_TOKEN` | توکن BotFather؛ محرمانه نگه دارید |
| `OWNER_ID` | شناسهٔ عددی مالک |
| `DATABASE_URL` | reference به PostgreSQL Railway |
| `USE_WEBHOOK` | `false` |
| `LOG_FILE_ENABLED` | `false`؛ لاگ‌ها را از Railway Logs ببینید |
| `DB_POOL_SIZE` | `2` برای شروع |
| `DROP_PENDING_UPDATES` | `false` |
| `TIMEZONE` | `Asia/Tehran` |

`BOT_TOKEN` و `DATABASE_URL` را فقط در Variables سرویس ثبت کنید، نه در Git یا ZIP. `OWNER_ID` را برای گزارش‌های خصوصی و فرمان‌های مالک تنظیم کنید.

### متغیرهای اختیاری

- اسکن حساب‌های حذف‌شده با نشست کاربری: `TELEGRAM_API_ID`، `TELEGRAM_API_HASH` و `TELEGRAM_SESSION_STRING`؛ همه را محرمانه نگه دارید.
- گفت‌وگوی هوشمند: `AI_CHAT_BASE_URL` و `AI_CHAT_API_KEY`.
- AI مدیریتی: `AI_PROVIDER` و `AI_API_KEY`، فقط در صورت فعال‌کردن این قابلیت.
- کوکی اینستاگرام: `INSTAGRAM_COOKIES_B64`، فقط در صورت نیاز.

اگر از این قابلیت‌ها استفاده نمی‌کنید، متغیرهایشان را تعریف نکنید. برای نشست MTProto، مقدار session را هرگز داخل مخزن، `.env.example` یا ZIP نگذارید.

## ۴) بررسی اجرای بات

در Deployment Logs باید Railpack نصب Python و `requirements.txt` را نشان دهد و سپس `python -m app.main` اجرا شود. بعد از Deploy موفق، در Telegram به ربات `/start` بفرستید و آن را به گروه اضافه کنید. نیازی به Generate Domain یا بازکردن پورت نیست.

## استقرارهای بعدی

با push کردن commit جدید به branch وصل‌شده، Railway به‌صورت خودکار Deploy می‌کند. پیش از حذف یا جایگزینی سرویس PostgreSQL، از داده‌های آن نسخهٔ پشتیبان بگیرید.
