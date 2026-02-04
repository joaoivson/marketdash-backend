from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.user import User
import sys

def fix_user():
    url = "postgresql://dashads_user:dashads_password@db:5432/dashads_db"
    engine = create_engine(url)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    user = session.query(User).filter(User.id == 2).first()
    if not user:
        print("User not found")
        return
        
    # The correct hash for 'Kapilca2804'
    correct_hash = "$2b$12$QtN2WiIK3utYNG9WGAPdvO.9RYSfgH.BHJ4a4rrFJI8HkMAvV.IzG"
    user.email = "joaoivsonn@gmail.com"
    user.hashed_password = correct_hash
    session.commit()
    print(f"User {user.email} updated successfully with correct hash.")
    session.close()

if __name__ == "__main__":
    fix_user()
