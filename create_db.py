from database import db

db.init_db(force=False)
print(f"Database initialized via backend: {db.active_backend()}")
