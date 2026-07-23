# Food Distribution Website V2

Flask + SQLite lab project with 3 roles:

- **Admin**: approves/rejects donations, manages users, manages requests, updates donation status
- **Donor**: donates food, sees user food requests, gets email when approved/rejected/status changed
- **User**: requests food, sees approved food in their area, gets email when food is approved in their area

## New features added

1. Food request system
2. Admin manage users: block/unblock/delete
3. Donation status: pending, approved, rejected, delivered, expired
4. Dashboard statistics
5. Better UI design
6. Contact donor/admin buttons
7. Food expiry auto-warning/auto-expire
8. Email to donor when admin approves/rejects/updates donation

## Run

```bash
cd food_distribute_website_v2
python -m venv venv
source venv/Scripts/activate
pip install -r requirements.txt
copy .env.example .env
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Default admin

```text
Email: admin@example.com
Password: admin123
```

## Gmail setup

For Gmail, use a Google **App Password**, not your normal Gmail password.

`.env` example:

```env
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=your_email@gmail.com
MAIL_PASSWORD=your_16_digit_app_password
MAIL_FROM="Food Donate System <your_email@gmail.com>"
EMAIL_USE_TLS=true
```

If email does not send, the app prints the email in terminal, so the project still works for lab demo.

## Important

Area names are normalized automatically. Example: `chattogram`, `Chattogram`, and `CHATTOGRAM` become `Chattogram`.

If you used the old version and get database column errors, delete the old SQLite database inside the `instance` folder and run again.

## New in v3

- Admin can delete any food donation item.
- Donor can delete their own donation item.
- When admin deletes a donation, donor gets an email notification.
