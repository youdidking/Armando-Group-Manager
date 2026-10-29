# راهنمای استقرار آسان آرماندو روی Hostless Cloud

## روش پیشنهادی: اتصال Git و تشخیص خودکار Railpack

برای این ربات **Docker لازم نیست**. Hostless با گزینهٔ **Detect automatically (Railpack)** پروژهٔ Python را تشخیص می‌دهد، Python را از `runtime.txt` می‌گیرد و وابستگی‌ها را از `requirements.txt` نصب می‌کند. فایل `railpack.json` هم فرمان اجرا و بسته‌های سیستمی موردنیاز رسانه‌ها را مشخص می‌کند. کافی است مخزن را وصل کنید، متغیرها را در پنل بگذارید و Worker را Deploy کنید.

آرماندو با Long Polling یک فرایند پس‌زمینه است؛ بنابراین آن را به‌صورت **Standalone Worker** بسازید، نه Web App. در حالت polling نیازی به دامنه یا پورت عمومی ندارید.

## ۱) ZIP را به مخزن خصوصی Git بفرستید

Hostless مخزن Git را می‌سازد و با هر push دوباره Deploy می‌کند. ZIP را در رایانه باز کنید و فایل‌های داخل آن را (نه یک پوشهٔ تودرتو) در مخزن خصوصی GitHub یا Git provider متصل به Hostless قرار دهید:

```bash
unzip Armando-Group-Manager-Hostless.zip -d armando
cd armando
git init
git add .
git commit -m "Deploy Armando"
git branch -M main
git remote add origin https://github.com/USERNAME/armando.git
git push -u origin main
```

در ZIP فایل `.env` یا مقدار Secret وجود ندارد؛ آن را نسازید و Secretها را به مخزن اضافه نکنید.

## ۲) Worker را بسازید

در داشبورد Hostless:

1. **Workers → Create worker → Standalone** را انتخاب کنید.
2. حساب Git، مخزن خصوصی و شاخهٔ `main` را وصل کنید.
3. در بخش Build، **Detect automatically / Railpack** را انتخاب کنید؛ **Docker** را انتخاب نکنید.
4. دستور Start را روی این مقدار بگذارید (فایل `railpack.json` هم همین را تنظیم می‌کند):

   ```text
   python -m app.main
   ```

5. تعداد replica را **۱** بگذارید. Long Polling با چند replica برای یک توکن تلگرام تعارض ایجاد می‌کند.
6. نیازی به دامنه، پورت ورودی، Health check وب یا Volume فایل نیست. برای حفظ داده‌ها، PostgreSQL را در مرحلهٔ بعد وصل کنید.

در استقرار بعدی، Hostless از `requirements.txt` نصب را انجام می‌دهد؛ لازم نیست دستور نصب دستی یا Dockerfile را تنظیم کنید. `railpack.json` بسته‌های سیستمی لازم برای ffmpeg و OpenCV/NudeNet را هم به تصویر اجرا اضافه می‌کند.

## ۳) پایگاه دادهٔ پایدار

برای حفظ تنظیمات و داده‌های گروه‌ها، یک **PostgreSQL** مدیریت‌شده بسازید و از Connection Information رشتهٔ اتصال را بردارید. آن را فقط به‌صورت Secret در متغیرهای Worker قرار دهید:

```text
DATABASE_URL=postgresql://...?...sslmode=require
```

کد آرماندو قالب اتصال Hostless را برای SQLAlchemy/asyncpg تبدیل می‌کند؛ رشتهٔ واقعی را در Git، ZIP یا گفتگو نگذارید. اگر Hostless گزینهٔ Link/Connect به Worker را نشان داد، باز هم مطمئن شوید نام متغیر `DATABASE_URL` است.

## ۴) متغیرهای محیطی

در بخش **Worker → Environment/Secrets** این متغیرها را تنظیم کنید:

| نام | مقدار |
| --- | --- |
| `BOT_TOKEN` | توکن BotFather؛ حتماً به‌صورت Secret |
| `OWNER_ID` | شناسهٔ عددی مالک |
| `DATABASE_URL` | URI پایگاه PostgreSQL؛ به‌صورت Secret |
| `USE_WEBHOOK` | `false` |
| `LOG_FILE_ENABLED` | `false`؛ لاگ‌ها در stdout و Logs پنل می‌روند |
| `DB_POOL_SIZE` | `2` برای شروع |
| `DROP_PENDING_UPDATES` | `false` برای حفظ Updateهای منتظر |
| `TIMEZONE` | `Asia/Tehran` |

`OWNER_ID` را برای گزارش‌های خصوصی و فرمان‌های مالک وارد کنید. برای استفادهٔ کامل، پس از Deploy در تلگرام به ربات `/start` بفرستید.

### متغیرهای اختیاری

- اسکن حساب حذف‌شده با نشست کاربری: `TELEGRAM_API_ID`، `TELEGRAM_API_HASH` و `TELEGRAM_SESSION_STRING` — همگی محرمانه‌اند؛ هرگز نشست را در مخزن یا فایل ZIP نگذارید.
- گفت‌وگوی هوشمند: `AI_CHAT_BASE_URL` و `AI_CHAT_API_KEY` (کلید به‌صورت Secret).
- AI مدیریتی: `AI_PROVIDER` و `AI_API_KEY`، فقط اگر این قابلیت را فعال می‌کنید.
- کوکی اینستاگرام: `INSTAGRAM_COOKIES_B64`، فقط در صورت نیاز و به‌صورت Secret.

اگر از این قابلیت‌ها استفاده نمی‌کنید، متغیرهای مربوط به آن‌ها را خالی بگذارید یا اصلاً تعریف نکنید.

## ۵) Deploy و بررسی

روی **Create/Deploy** بزنید. Build باید Python و وابستگی‌های `requirements.txt` را نصب کند؛ سپس Start باید `python -m app.main` را اجرا کند. در Logs پیام شروع polling را بررسی کنید، در تلگرام `/start` بفرستید و ربات را به گروه اضافه کنید.

## هزینه و ماندگاری

منابع رایگان Hostless بین پروژه‌ها و منابع حساب مشترک‌اند و شرایط ممکن است با نوع حساب یا دورهٔ صورت‌حساب فرق کند. مستندات فعلی حداقل منابع پایگاه داده و یک سهمیهٔ مشترک حساب را توضیح می‌دهد؛ «همیشه رایگان» را تضمین‌شده فرض نکنید. پیش از Deploy در **Billing → Cost Breakdown** هزینهٔ Worker و PostgreSQL را ببینید. Volume لازم نیست؛ دادهٔ اصلی در PostgreSQL می‌ماند.

مستندات: [استقرار خودکار Railpack](https://docs.hostless.cloud/started)، [Standalone Worker](https://docs.hostless.cloud/app/workers)، [تنظیمات Build](https://docs.hostless.cloud/app/configurations)، [Railpack config](https://railpack.com/config/file/)، [PostgreSQL](https://docs.hostless.cloud/databases/connecting)، [هزینه‌ها](https://docs.hostless.cloud/billing/costbreakdown).
