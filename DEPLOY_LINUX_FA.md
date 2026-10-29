# راهنمای استقرار آرماندو روی سرور لینوکسی شخصی

این روش از Docker Compose استفاده می‌کند؛ ربات با Long Polling اجرا می‌شود، در راه‌اندازی مجدد خودکار بالا می‌آید و دیتابیس SQLite در پوشهٔ `data/` باقی می‌ماند. برای روش بدون Docker، فایل `deploy/armando.service` نیز نمونهٔ systemd است.

## نیازمندی‌ها

- سرور Debian/Ubuntu 64-bit با دسترسی `sudo`
- Docker Engine و افزونهٔ Docker Compose
- حداقل ۲ GiB حافظه و چند GiB فضای خالی برای ساخت image و وابستگی‌های رسانه‌ای توصیه می‌شود
- توکن ربات از BotFather و شناسهٔ عددی مالک

Docker را از [راهنمای رسمی Docker برای توزیع لینوکس خود](https://docs.docker.com/engine/install/) نصب کنید و سرویس را فعال کنید:

```bash
sudo systemctl enable --now docker
docker --version
docker compose version
```

## ۱) انتقال و بازکردن ZIP

از رایانهٔ خود ZIP را به سرور بفرستید و وصل شوید:

```bash
scp Armando-Group-Manager-Hostless.zip USER@SERVER_IP:/tmp/
ssh USER@SERVER_IP
```

سپس روی سرور:

```bash
sudo mkdir -p /opt/armando
sudo chown "$USER":"$USER" /opt/armando
unzip Armando-Group-Manager-Hostless.zip -d /opt/armando
cd /opt/armando
```

اگر نام فایل یا مسیر آن فرق دارد، همان مسیر واقعی ZIP را جایگزین کنید. ساختار باید شامل `Dockerfile`، `compose.yaml`، `app/` و `.env.example` باشد.

## ۲) تنظیم امن محیط

```bash
cp .env.example .env
nano .env
```

در `.env` دست‌کم این موارد را با مقدار خودت تنظیم کن:

```env
BOT_TOKEN=توکن_واقعی_ربات
OWNER_ID=شناسه_عددی_مالک
DATABASE_URL=sqlite+aiosqlite:///./data/armando.sqlite3
USE_WEBHOOK=false
LOG_FILE_ENABLED=true
TIMEZONE=Asia/Tehran
```

فایل را از دسترس دیگر کاربران سرور دور نگه دار:

```bash
chmod 600 .env
```

برای اسکن حساب‌های حذف‌شده، `TELEGRAM_API_ID` و `TELEGRAM_API_HASH` لازم‌اند؛ سایر کلیدها و نشست‌ها فقط در صورت استفاده از قابلیت‌های مربوطه تنظیم شوند. هیچ Secret را در Git یا ZIP قرار ندهید.

## ۳) ساخت پوشه‌ها و دسترسی Docker

کانتینر با کاربر غیرریشهٔ شناسهٔ `10001` اجرا می‌شود. پوشه‌های bind mount را برای آن قابل نوشتن کنید:

```bash
mkdir -p data logs
sudo chown -R 10001:10001 data logs
```

## ۴) ساخت و اجرای ربات

از `/opt/armando` اجرا کنید:

```bash
sudo docker compose up -d --build
sudo docker compose ps
sudo docker compose logs -f armando
```

در لاگ باید شروع ربات و Long Polling را ببینید. برای خروج از مشاهدهٔ زندهٔ لاگ‌ها `Ctrl+C` بزنید؛ خود ربات متوقف نمی‌شود. پورت ورودی روی فایروال لازم نیست، چون در حالت polling ربات اتصال خروجی به تلگرام برقرار می‌کند.

## فرمان‌های نگهداری

```bash
# وضعیت
sudo docker compose ps

# لاگ زنده
sudo docker compose logs -f armando

# توقف/شروع مجدد
sudo docker compose stop
sudo docker compose start

# ساخت مجدد پس از به‌روزرسانی فایل‌ها
sudo docker compose up -d --build
```

## نسخهٔ پشتیبان و به‌روزرسانی

اگر `DATABASE_URL` روی SQLite پیش‌فرض است، پیش از به‌روزرسانی دیتابیس را با ربات متوقف‌شده کپی کنید:

```bash
sudo docker compose stop
mkdir -p backups
cp data/armando.sqlite3 "backups/armando-$(date +%F-%H%M).sqlite3"
```

برای PostgreSQL از `pg_dump` استفاده کنید؛ فایل SQLite بالا در آن حالت وجود ندارد.

فایل نسخهٔ جدید را به `/opt/armando` منتقل و روی فایل‌های برنامه استخراج کنید؛ `.env` و `data/` را جایگزین نکنید. سپس بسازید و اجرا کنید:

```bash
unzip -o Armando-Group-Manager-Hostless.zip -d /opt/armando
sudo docker compose up -d --build
sudo docker compose logs --tail=100 armando
```

برای بازیابی، ربات را متوقف و نسخهٔ پشتیبان را به `data/armando.sqlite3` برگردانید؛ سپس آن را روشن کنید.

## اختیاری: PostgreSQL

برای حجم بیشتر یا چند مصرف‌کننده، می‌توانید PostgreSQL نصب‌شده روی همان سرور یا سرویس مدیریت‌شده را به کار ببرید. در آن صورت `DATABASE_URL` را به URI اتصال PostgreSQL تغییر دهید و رازهای اتصال را فقط در `.env` با مجوز محدود نگه دارید. اگر PostgreSQL روی خود میزبان است، `localhost` داخل کانتینر به خود کانتینر اشاره می‌کند؛ از نشانی شبکه‌ایِ قابل‌دسترسی از Docker استفاده کنید و دیتابیس را به اینترنت عمومی باز نگذارید. پیش‌فرض SQLite برای نصب ساده مناسب است؛ حتماً پوشهٔ `data/` را در پشتیبان بگنجانید.
