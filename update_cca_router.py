import re

path = r'backend/app/routers/cca.py'
with open(path, 'r', encoding='utf-8') as f:
    txt = f.read()

txt = txt.replace(
    'def get_db(request: Request):\n    return request.state.db if hasattr(request.state, "db") else None',
    'def get_cca_db():\n    from ..main import SessionLocal\n    db = SessionLocal()\n    try:\n        yield db\n    finally:\n        db.close()'
)
txt = txt.replace('db: Session = Depends()', 'db: Session = Depends(get_cca_db)')

with open(path, 'w', encoding='utf-8') as f:
    f.write(txt)

print("Successfully replaced get_cca_db in cca.py")
