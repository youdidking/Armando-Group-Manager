# راهنمای استقرار آرماندو روی Hostless Cloud

## معماری مناسب این ربات

آرماندو را به‌صورت **Standalone Worker** و با Long Polling اجرا کنید؛ این نوع Worker برای فرایندهای پس‌زمینهٔ بلندمدت است و نیازی به دامنه، وب‌هوک یا پورت عمومی ندارد. در Hostless گزینهٔ **App** را انتخاب نکنید، مگر اینکه بخواهید وب‌هوک را جداگانه راه‌اندازی کنید.

برای ماندگاری اطلاعات، PostgreSQL مدیریت‌شدهٔ Hostless را به‌جای SQLite محلی انتخاب کنید. فضای فایل موقت Worker در استقرار بعدی قابل اتکا نیست.

## ۱) فرستادن ZIP به مخزن خصوصی Git

Hostless از مخزن Git برای ساخت و استقرار استفاده می‌کند؛ ZIP را مستقیم به‌عنوان برنامه آپلود نکنید. ZIP را روی رایانه باز کنید، پوشهٔ پروژه را به یک مخزن **خصوصی** GitHub (یا ارائه‌دهندهٔ Git متصل به Hostless) بفرستید:

```bash
unzip Armando-Group-Manager-Hostless.zip -d armando
cd armando
git init
git add .
git commit -m "Deploy Armando on Hostless"
git branch -M main
git remote add origin https://github.com/USERNAME/armando.git
git push -u origin main
```

فایل `.env` را نسازید یا commit نکنید. فایل ZIP عمداً `.env`، دیتابیس، لاگ، تست و نشست را ندارد؛ `.gitignore` نیز از اضافه‌شدن فایل‌های محرمانه جلوگیری می‌کند.

## ۲) ساخت PostgreSQL

1. در داشبورد Hostless یک PostgreSQL بسازید.
2. برای شروع، کوچک‌ترین منابع قابل انتخاب را بگذارید؛ راهنمای فعلی Hostless حداقل ۰٫۲ vCPU، ۲۵۶ MiB RAM و ۲۵۶ MiB فضای پایگاه داده را ذکر می‌کند.
3. بعد از وضعیت `RUNNING`، رشتهٔ اتصال PostgreSQL را از بخش Connection Information بردارید.
4. آن را فقط در متغیر محیطی `DATABASE_URL` مربوط به Worker ثبت کنید. رشتهٔ اتصال محرمانه است.

کد آرماندو URL استاندارد Hostless با `postgresql://...?...sslmode=require` را برای SQLAlchemy/asyncpg تبدیل می‌کند؛ لازم نیست رمز یا URL را برای کسی بفرستید.

## ۳) ساخت Worker

در Hostless:

1. وارد پروژه شوید و **Workers → Create worker → Standalone** را انتخاب کنید.
2. حساب Git و مخزن خصوصی آرماندو را متصل و شاخهٔ `main` را انتخاب کنید.
3. نوع ساخت را **Docker** بگذارید تا `Dockerfile` ریشهٔ مخزن استفاده شود.
4. دستور شروع را وارد کنید:

   ```text
   python -m app.main
   ```

5. تعداد replica را **۱** بگذارید. برای شروع، منابع Worker را پایین نگه دارید (مثلاً سقف حدود ۰٫۲۵ vCPU و ۵۱۲ MiB RAM، اگر این مقادیر در پنل قابل انتخاب‌اند). افزایش منابع یا اجرای چند replica می‌تواند از سهمیهٔ رایگان عبور کند.
6. در بخش Environment/Secrets متغیرهای لازم را اضافه کنید و Worker را بسازید.

### متغیرهای اصلی Worker

```env
BOT_TOKEN=توکن_واقعی_ربات
OWNER_ID=شناسه_عددی_مالک
DATABASE_URL=رشته_اتصال_PostgreSQL
USE_WEBHOOK=false
LOG_FILE_ENABLED=false
DB_POOL_SIZE=2
DROP_PENDING_UPDATES=false
TIMEZONE=Asia/Tehran
```

مقدارهای واقعی `BOT_TOKEN` و `DATABASE_URL` را در داشبورد Hostless به‌صورت Secret ثبت کنید؛ آن‌ها را در Git، ZIP یا گفتگو قرار ندهید. `LOG_FILE_ENABLED=false` باعث می‌شود لاگ‌ها به stdout بروند تا از تب Logs خود Hostless دیده شوند. Worker با Long Polling کار می‌کند و به `WEBHOOK_URL` یا `PORT` نیازی ندارد.

`OWNER_ID` برای قابلیت‌های مالک و گزارش‌های خصوصی لازم است؛ برای استفادهٔ کامل آن را تنظیم کنید.

### تنظیم قابلیت‌های اختیاری

- **اسکن حساب‌های حذف‌شده:** `TELEGRAM_API_ID` و `TELEGRAM_API_HASH` را اضافه کنید. چون Worker دیسک پایدار ندارد، روی Hostless در صورت فعال‌کردن اسکن `TELEGRAM_SESSION_STRING` را هم به‌صورت Secret تنظیم کنید تا نشست روی فایل محلی ذخیره نشود. اگر این قابلیت را نمی‌خواهید، این سه را تنظیم نکنید.
- **گفت‌وگوی هوشمند:** در صورت استفاده، `AI_CHAT_BASE_URL` و `AI_CHAT_API_KEY` را اضافه کنید؛ کلید را Secret نگه دارید.
- **AI مدیریتی:** فقط در صورت فعال‌کردن آن، `AI_PROVIDER` و `AI_API_KEY` را اضافه کنید.
- **کوکی اینستاگرام:** فقط در صورت نیاز `INSTAGRAM_COOKIES_B64` را به‌صورت Secret ثبت کنید.

## ۴) بررسی راه‌اندازی

1. در Logs باید پیام شروع polling را ببینید و وضعیت Worker باید فعال باشد.
2. به ربات در تلگرام `/start` بفرستید تا بتواند گزارش خصوصی را به مالک تحویل دهد.
3. ربات را به گروه اضافه و به‌اندازهٔ قابلیت‌هایی که استفاده می‌کنید دسترسی مدیر بدهید.
4. Worker را روی یک replica نگه دارید؛ چند polling هم‌زمان برای یک توکن تلگرام باعث خطای `Conflict` می‌شود.
5. بعد از Deploy، اطلاعات پایگاه داده در PostgreSQL می‌ماند؛ دیتابیس را حذف نکنید مگر اینکه از آن نسخهٔ پشتیبان گرفته باشید.

## سهمیهٔ رایگان و هزینه

طبق [راهنمای سهمیه و صورتحساب Hostless](https://docs.hostless.cloud/billing/costbreakdown)، سهمیهٔ رایگان حساب **مشترک** است (در مستندات فعلی: ۰٫۵ vCPU، ۱ GiB RAM و ۵۱۲ MiB فضای پایدار) و بین Worker، اپ‌ها، پایگاه داده و دیگر پروژه‌های همان حساب تقسیم می‌شود. مصرف بالاتر از سهمیه صورتحساب دارد؛ Volume پایدار نیز طبق مستندات هزینهٔ جداگانه دارد. بنابراین «همیشه رایگان» را تضمین‌شده فرض نکنید: در Billing/Cost Breakdown میزان مصرف را بررسی کنید و از ساخت Volume غیرضروری بپرهیزید.

مستندات رسمی: [ساخت Worker](https://docs.hostless.cloud/app/workers)، [شروع استقرار از Git](https://docs.hostless.cloud/started)، [اتصال پایگاه داده](https://docs.hostless.cloud/databases/connecting)، [متغیرها و Secrets](https://docs.hostless.cloud/app/envvars).
