# ساده‌ترین استقرار آرماندو روی سرور لینوکسی شخصی

این روش **Docker نمی‌خواهد**: ZIP را روی سرور باز می‌کنید، یک اسکریپت Python و وابستگی‌های سیستم را نصب می‌کند، محیط مجازی و systemd را می‌سازد؛ شما فقط Secretها را در `.env` می‌گذارید و سرویس را روشن می‌کنید. برای نصب آسان، Ubuntu 24.04 LTS را پیشنهاد می‌کنم؛ Debian/Ubuntu دیگری هم قابل استفاده است اگر Python 3.12 یا جدیدتر داشته باشد.

## ۱) انتقال فایل‌ها

از رایانهٔ خود فایل را بفرستید و به سرور وصل شوید:

```bash
scp Armando-Group-Manager-Hostless.zip USER@SERVER_IP:/tmp/
ssh USER@SERVER_IP
```

روی سرور اجرا کنید:

```bash
sudo mkdir -p /opt/armando
sudo unzip -o /tmp/Armando-Group-Manager-Hostless.zip -d /opt/armando
sudo bash /opt/armando/install-linux.sh
```

اسکریپت بسته‌های لازم از جمله Python venv، ffmpeg و کتابخانه‌های رسانه را نصب می‌کند، `requirements.txt` را در `.venv` نصب می‌کند و سرویس systemd را آماده می‌سازد. برای این روش Docker و بازکردن پورت فایروال لازم نیست.

## ۲) قرار دادن تنظیمات و Secretها

```bash
sudo nano /opt/armando/.env
```

اسکریپت `.env.example` را با دسترسی محدود به `.env` کپی می‌کند. فقط مقدارهای لازم را ویرایش کنید و بقیهٔ تنظیمات را نگه دارید:

```env
BOT_TOKEN=توکن_واقعی_ربات
OWNER_ID=شناسه_عددی_مالک
DATABASE_URL=sqlite+aiosqlite:///./data/armando.sqlite3
USE_WEBHOOK=false
LOG_FILE_ENABLED=true
TIMEZONE=Asia/Tehran
```

فایل `.env` فقط برای کاربر سرویس و مدیر سیستم قابل خواندن است. آن را در Git، ZIP یا پیام‌ها قرار ندهید. SQLite به‌صورت پیش‌فرض در `/opt/armando/data/` می‌ماند و با راه‌اندازی مجدد سرور پاک نمی‌شود.

## ۳) روشن‌کردن ربات و دیدن لاگ

پس از ذخیرهٔ توکن و شناسه:

```bash
sudo systemctl enable --now armando
sudo systemctl status armando --no-pager
sudo journalctl -u armando -f
```

با `Ctrl+C` از نمایش زندهٔ لاگ خارج می‌شوید؛ سرویس روشن می‌ماند. بعد از شروع، در تلگرام `/start` بفرستید تا گزارش‌های خصوصی به مالک برسند. systemd ربات را پس از ری‌استارت سرور هم خودکار بالا می‌آورد.

## به‌روزرسانی

برای SQLite، ابتدا نسخهٔ پشتیبان بگیرید و سرویس را متوقف کنید:

```bash
sudo systemctl stop armando
sudo mkdir -p /opt/armando/backups
sudo cp /opt/armando/data/armando.sqlite3 "/opt/armando/backups/armando-$(date +%F-%H%M).sqlite3"
```

ZIP جدید را به `/tmp/` بفرستید، سپس فایل‌های برنامه را روی نسخهٔ فعلی استخراج و نصب‌کننده را دوباره اجرا کنید:

```bash
sudo unzip -o /tmp/Armando-Group-Manager-Hostless.zip -d /opt/armando
sudo bash /opt/armando/install-linux.sh
sudo systemctl status armando --no-pager
```

ZIP شامل `.env` و دیتابیس نیست، پس تنظیمات و داده‌های موجود را جایگزین نمی‌کند. نصب‌کننده وابستگی‌ها را به‌روز می‌کند و اگر `BOT_TOKEN` تنظیم شده باشد سرویس را راه می‌اندازد. اگر PostgreSQL استفاده می‌کنید، قبل از به‌روزرسانی با `pg_dump` نسخهٔ پشتیبان بگیرید.

## PostgreSQL اختیاری

برای نصب ساده SQLite کافی است. اگر PostgreSQL دارید، مقدار `DATABASE_URL` را در همان `.env` به URI اتصال تغییر دهید. در نصب مستقیم روی سرور، `localhost` برای PostgreSQL محلی همان میزبان است؛ دیتابیس را بدون نیاز به دسترسی عمومی نگه دارید و از رمز قوی استفاده کنید.

این روش اجرای مستقیم با Python و systemd است؛ Docker لازم نیست.
