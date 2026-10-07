# Smart Grocery Inventory Manager

Working model of the architecture in the review deck.

| Layer (slide 11) | Implementation |
|---|---|
| Frontend | `templates/index.html`, `static/style.css`, `static/app.js` (HTML, CSS, JavaScript) |
| Backend | `app.py`: Flask REST APIs, login + roles (owner/staff), input validation |
| Database | SQLite by default (zero setup); same schema runs on MySQL/PostgreSQL |
| Analytics | `analytics.py`: Pandas sales analysis, Scikit-learn demand forecast |
| Output | Dashboard, alerts, CSV reports |

## Run
```
pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000

Logins: `admin / admin123` (owner, can delete products) and `staff / staff123`.
The first run creates `grocery.db` with sample products and 60 days of sales. Delete the file to reset.

## Features mapped to the objectives
- Product registration, edit, search, filter; suppliers; expiry dates
- Stock updates: sale, purchase, return, manual adjustment (blocks overselling)
- Alerts: low stock, out of stock, near expiry (7 days), expired; stored in `alert_log`
- Fast / slow movers from 30-day sales; 7-day demand forecast (linear regression); reorder suggestions
- Dashboard charts and CSV export (inventory, transactions, reorder list)

## Notes
- To use MySQL/PostgreSQL, replace the `sqlite3` calls in `app.py` with your driver or SQLAlchemy; the tables are in `SCHEMA`.
- Set `SECRET_KEY` and change the demo passwords before real use.
