# نصب آسان آرماندو روی سرور لینوکسی با Git

نیازی به فرستادن ZIP یا کپی فایل‌ها به سرور نیست: سرور مخزن را با `git clone` می‌گیرد و نصب‌کننده بقیهٔ کارها را انجام می‌دهد—نصب وابستگی‌های سیستم، ساخت محیط Python، نصب `requirements.txt` و آماده‌سازی systemd. Docker لازم نیست. ساده‌ترین میزبان پیشنهادی Ubuntu 24.04 LTS است؛ توزیع Debian/Ubuntu باید Python 3.12 یا جدیدتر داشته باشد.

## ۱) کلون‌کردن مخزن و نصب

به سرور با SSH وصل شوید. `REPOSITORY_URL` را با آدرس واقعی Git مخزن خود عوض کنید:

```bash
sudo apt-get update
sudo apt-get install -y git
sudo git clone --depth 1 --branch main REPOSITORY_URL /opt/armando
sudo bash /opt/armando/install-linux.sh
```

مثال برای مخزن عمومی:

```bash
sudo git clone --depth 1 --branch main https://github.com/USERNAME/REPOSITORY.git /opt/armando
```

اگر مخزن خصوصی است، قبل از `git clone` برای سرور دسترسی فقط‌خواندنی Git تنظیم کنید (مثلاً SSH deploy key). توکن دسترسی را داخل URL، history ترمینال، سورس یا `.env` قرار ندهید.

نصب‌کننده Python venv، ffmpeg و کتابخانه‌های رسانه را نصب می‌کند، سپس `requirements.txt` را نصب و سرویس systemd را آماده می‌کند. کد برنامه و `.git` برای کاربر ربات فقط‌خواندنی می‌مانند؛ فقط `.env`، محیط مجازی، داده و لاگ مجاز به نوشتن هستند.

## ۲) تنظیم توکن و دیتابیس

```bash
sudo nano /opt/armando/.env
```

نصب‌کننده `.env.example` را با مجوز محدود به `.env` کپی می‌کند. حداقل این موارد را پر کنید:

```env
BOT_TOKEN=توکن_واقعی_ربات
OWNER_ID=شناسه_عددی_مالک
DATABASE_URL=sqlite+aiosqlite:///./data/armando.sqlite3
USE_WEBHOOK=false
LOG_FILE_ENABLED=true
TIMEZONE=Asia/Tehran
```

فایل `.env` فقط برای کاربر سرویس و مدیر سیستم قابل خواندن است. آن را به Git اضافه نکنید. SQLite در `/opt/armando/data/` ذخیره می‌شود و پس از راه‌اندازی مجدد سرور باقی می‌ماند.

## ۳) روشن‌کردن و بررسی لاگ

پس از ذخیرهٔ توکن و شناسه:

```bash
sudo systemctl enable --now armando
sudo systemctl status armando --no-pager
sudo journalctl -u armando -f
```

با `Ctrl+C` از نمایش لاگ خارج می‌شوید؛ سرویس روشن می‌ماند. سرویس پس از ری‌استارت سرور هم خودکار بالا می‌آید. برای Long Polling نیازی به دامنه یا بازکردن پورت ورودی نیست.

## به‌روزرسانی از همان مخزن

برای SQLite، ابتدا سرویس را متوقف و دیتابیس را پشتیبان‌گیری کنید:

```bash
sudo systemctl stop armando
sudo mkdir -p /opt/armando/backups
sudo cp /opt/armando/data/armando.sqlite3 "/opt/armando/backups/armando-$(date +%F-%H%M).sqlite3"
```

سپس آخرین commit را بگیرید و نصب‌کننده را دوباره اجرا کنید:

```bash
sudo git -C /opt/armando pull --ff-only
sudo bash /opt/armando/install-linux.sh
sudo systemctl status armando --no-pager
```

`.env`، دیتابیس و نشست‌ها در Git نیستند؛ به‌روزرسانی آن‌ها را جایگزین نمی‌کند. نصب‌کننده وابستگی‌ها را به‌روز می‌کند و اگر `BOT_TOKEN` تنظیم باشد سرویس را راه می‌اندازد. برای PostgreSQL، پیش از به‌روزرسانی با `pg_dump` پشتیبان بگیرید.

## PostgreSQL اختیاری

SQLite برای نصب ساده کافی است. اگر PostgreSQL دارید، `DATABASE_URL` را در `.env` به URI اتصال تغییر دهید. در نصب مستقیم روی سرور، `localhost` برای PostgreSQL محلی همان میزبان است؛ دسترسی عمومی دیتابیس را باز نگذارید.
